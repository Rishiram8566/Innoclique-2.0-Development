"""
Sheet registration: do two drawing sheets share one layout?

Drawing sets produced from one CAD model often place every plan in the same
viewport on the same sheet. When that is true, a scale measured on one sheet
and a reference point (e.g. grid A/1) clicked on one sheet are valid on the
others. This module checks it from the drawings themselves instead of
assuming it:

* grid-bubble labels (single letters / numbers, small text) must appear at the
  same PDF positions on both sheets - at least MIN_MATCHED_LABELS of them,
  within MATCH_TOLERANCE_PT, covering at least half of the source's labels;
* sheet sizes must be identical;
* both sheets must print a common scale (e.g. "1:200").

It also exposes the wall thickness each sheet's own legend states (e.g.
"250mm THERMAL INSULATED BLOCK WALL") for wall suggestions.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import height_verification_ledger as ledger_service
from app.services import scale_calibration

router = APIRouter(tags=["Sheet registration"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"
EVIDENCE_ARTIFACT = "geometry_source_evidence_stage5_3_3_v2.json"
GRID_LABEL = re.compile(r"^(?:[A-Z]{1,2}|\d{1,2})$")
SCALE_TEXT = re.compile(r"\b1\s*:\s*(\d{2,4})\b")
MAX_LABEL_SIZE_PT = 20.0
MATCH_TOLERANCE_PT = 1.0
MIN_MATCHED_LABELS = 8
UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


# ---------------------------------------------------------------------------
# Index of the text facts used for registration
# ---------------------------------------------------------------------------

def build_sheet_index(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"labels": defaultdict(list), "scales": set(), "wall_thickness_mm": []}
    )
    for record in evidence:
        if not isinstance(record, dict):
            continue
        drawing_id = record.get("drawing_id")
        text = str(record.get("text") or "").strip()
        bbox = (record.get("source_reference") or {}).get("bbox_pdf_points")
        if not drawing_id or not text:
            continue
        entry = index[drawing_id]
        for scale in SCALE_TEXT.findall(text):
            entry["scales"].add(f"1:{scale}")
        if (
            GRID_LABEL.match(text) and isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and abs(bbox[2] - bbox[0]) <= MAX_LABEL_SIZE_PT and abs(bbox[3] - bbox[1]) <= MAX_LABEL_SIZE_PT
        ):
            entry["labels"][text].append(((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2))
        if "possible_wall_thickness" in (record.get("categories") or []):
            value = record.get("numeric_value") or {}
            values, unit = value.get("values") or [], value.get("unit")
            if len(values) == 1 and unit in UNIT_MM:
                entry["wall_thickness_mm"].append(round(float(values[0]) * UNIT_MM[unit], 1))
    return dict(index)


@lru_cache(maxsize=2)
def _cached_index(path: str, mtime_ns: int, size: int) -> dict[str, dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as stream:
        evidence = json.load(stream).get("evidence", [])
    return build_sheet_index(evidence)


def sheet_index(folder: Path) -> dict[str, dict[str, Any]]:
    path = Path(folder) / "artifacts" / EVIDENCE_ARTIFACT
    if not path.is_file():
        return {}
    stat = path.stat()
    return _cached_index(str(path), stat.st_mtime_ns, stat.st_size)


def typical_wall_thickness_mm(index: dict[str, dict[str, Any]], drawing_id: str) -> float | None:
    values = (index.get(drawing_id) or {}).get("wall_thickness_mm") or []
    return Counter(values).most_common(1)[0][0] if values else None


def compare_sheets(
    index: dict[str, dict[str, Any]], source_id: str, target_id: str,
    source_size: Any = None, target_size: Any = None,
) -> dict[str, Any]:
    source = index.get(source_id) or {"labels": {}, "scales": set()}
    target = index.get(target_id) or {"labels": {}, "scales": set()}
    offsets = []
    total = 0
    for label, points in source["labels"].items():
        others = target["labels"].get(label, [])
        for x, y in points:
            total += 1
            best = min((math.hypot(x - a, y - b) for a, b in others), default=None)
            if best is not None and best <= MATCH_TOLERANCE_PT:
                offsets.append(best)
    common_scales = sorted(set(source["scales"]) & set(target["scales"]))
    same_size = source_size is None or target_size is None or source_size == target_size
    reasons = []
    if len(offsets) < MIN_MATCHED_LABELS:
        reasons.append(f"only {len(offsets)} grid labels match (need {MIN_MATCHED_LABELS})")
    if total and len(offsets) / total < 0.5:
        reasons.append(f"only {len(offsets)} of {total} grid labels match")
    if not common_scales:
        reasons.append("the sheets print no common scale")
    if not same_size:
        reasons.append("sheet sizes differ")
    return {
        "source_drawing_id": source_id,
        "target_drawing_id": target_id,
        "matched_grid_labels": len(offsets),
        "source_grid_labels": total,
        "max_offset_pt": round(max(offsets), 3) if offsets else None,
        "median_offset_pt": round(median(offsets), 3) if offsets else None,
        "common_printed_scales": common_scales,
        "same_sheet_size": same_size,
        "registered": not reasons,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _folder(project_id: str) -> tuple[str, Path]:
    try:
        safe = str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project not found") from None
    folder = PROJECTS_DIR / safe
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe, folder


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Drawing not found") from None


def _page_sizes(folder: Path) -> dict[str, Any]:
    recognition = ledger_service.read_required_artifact(folder, ledger_service.RECOGNITION_ARTIFACT_NAME)
    return {
        d.get("drawing_id"): (d.get("pages") or [{}])[0].get("page_size_pdf_points")
        for d in recognition.get("drawings", []) if isinstance(d, dict)
    }


@router.get("/api/projects/{project_id}/sheet-registration")
def get_sheet_registration(project_id: str, target_drawing_id: str = Query(..., min_length=1, max_length=64)):
    """Registration of one sheet against every calibrated sheet and every placed sheet."""
    safe, folder = _folder(project_id)
    target = _uuid(target_drawing_id)
    index = sheet_index(folder)
    sizes = _page_sizes(folder)
    calibration = ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME) or {}
    ledger = ledger_service.load_ledger(folder, safe)

    calibrated = []
    for drawing_id, pages in (calibration.get("calibrations") or {}).items():
        record = (pages or {}).get("1")
        if drawing_id == target or not isinstance(record, dict) or record.get("status") != "calibrated":
            continue
        calibrated.append({
            **compare_sheets(index, drawing_id, target, sizes.get(drawing_id), sizes.get(target)),
            "drawing_title": record.get("filename"),
            "millimetres_per_pdf_point": record.get("millimetres_per_pdf_point"),
            "method": record.get("method"),
            "calibration_reference": record.get("source_reference"),
        })

    placed = []
    for placement in ledger.get("plan_placements", []):
        if placement.get("status") != "active" or placement.get("drawing_id") == target:
            continue
        placed.append({
            **compare_sheets(index, placement["drawing_id"], target,
                             sizes.get(placement["drawing_id"]), sizes.get(target)),
            "drawing_title": placement.get("drawing_title"),
            "placement_id": placement["placement_id"],
            "reference_label": placement["reference_point"]["label"],
            "reference_point_pdf": placement["reference_point"]["point_pdf"],
        })

    return {
        "target_drawing_id": target,
        "typical_wall_thickness_mm": typical_wall_thickness_mm(index, target),
        "printed_scales": sorted((index.get(target) or {}).get("scales", set())),
        "calibrated_sources": calibrated,
        "placed_sources": placed,
    }


class ScaleTransferRequest(BaseModel):
    source_drawing_id: str = Field(min_length=1, max_length=64)
    target_drawing_id: str = Field(min_length=1, max_length=64)
    reviewer: str = Field(min_length=2, max_length=120)
    justification: str = Field(min_length=20, max_length=2000)
    replace_existing: bool = False


@router.post("/api/projects/{project_id}/scale-calibration/transfer")
def transfer_scale(project_id: str, payload: ScaleTransferRequest):
    """Copy page 1's calibration to a sheet proven to share its layout and printed scale."""
    safe, folder = _folder(project_id)
    source_id, target_id = _uuid(payload.source_drawing_id), _uuid(payload.target_drawing_id)
    if source_id == target_id:
        raise HTTPException(status_code=422, detail="Source and target must be different drawings")

    path = folder / "artifacts" / ledger_service.CALIBRATION_ARTIFACT_NAME
    report = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    calibrations = report.setdefault("calibrations", {})
    source = (calibrations.get(source_id) or {}).get("1")
    if not isinstance(source, dict) or source.get("status") != "calibrated":
        raise HTTPException(status_code=409, detail="The source drawing is not calibrated")
    if (calibrations.get(target_id) or {}).get("1") and not payload.replace_existing:
        raise HTTPException(status_code=409, detail="The target drawing is already calibrated; confirm replacing it")

    sizes = _page_sizes(folder)
    registration = compare_sheets(sheet_index(folder), source_id, target_id, sizes.get(source_id), sizes.get(target_id))
    if not registration["registered"]:
        raise HTTPException(status_code=422, detail="Sheets are not registered: " + "; ".join(registration["reasons"]))

    target_drawing = scale_calibration._find_geometry_drawing(folder, target_id)
    if not target_drawing:
        raise HTTPException(status_code=404, detail="Target drawing not found in the Stage 5.0 metadata index")
    target_sha = scale_calibration._current_pdf_sha256(folder, target_drawing)

    reviewer = " ".join(payload.reviewer.split())
    mm_per_point = float(source["millimetres_per_pdf_point"])
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "status": "calibrated",
        "method": "transferred_from_registered_sheet",
        "drawing_id": target_id,
        "filename": target_drawing.get("filename"),
        "page_number": 1,
        "source_sha256": target_sha,
        "millimetres_per_pdf_point": mm_per_point,
        "pdf_points_per_metre": round(1000.0 / mm_per_point, 8),
        "transferred_from": {
            "drawing_id": source_id,
            "filename": source.get("filename"),
            "method": source.get("method"),
            "calibrated_at": source.get("calibrated_at"),
            "source_reference": source.get("source_reference"),
        },
        "registration": registration,
        "reviewer": reviewer,
        "justification": " ".join(payload.justification.split()),
        "source_reference": (
            f"Scale transferred from {source.get('filename') or source_id} "
            f"({source.get('source_reference')}); sheets registered: "
            f"{registration['matched_grid_labels']} grid labels within {registration['max_offset_pt']} pt, "
            f"both print {', '.join(registration['common_printed_scales'])}; reviewer {reviewer}."
        )[:500],
        "verification_required": True,
        "calibrated_at": now,
    }
    calibrations.setdefault(target_id, {})["1"] = record
    report.setdefault("project_id", safe)
    report["updated_at"] = now
    ledger_service.save_json_atomic(path, report)
    return {"message": "Scale transferred.", "calibration": record}
