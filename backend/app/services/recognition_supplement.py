"""
Stage 5.1.8 - recognition supplement.

The Stage 5.1.6 recognition report is left untouched (confirmed walls refer
to it). This stage re-runs recognition for chosen drawings with the fixed
recognizer (separate horizontal/vertical budgets) and stores, per page:

* added_candidates - parallel line pairs that are NOT already in Stage 5.1.6,
  each geometry-checked against the page's primitive sidecar with the same
  footprint and bounding-box rules the wall builder uses;
* existing_candidate_validation - the same geometry check for Stage 5.1.6
  "unclassified" pairs, which the earlier wall validation skipped.

Consumers read ``read_recognition(folder)``: the Stage 5.1.6 report with the
supplement merged in memory. Nothing in the supplement is approved; an
unclassified pair becomes a wall only through a reviewer's Stage 5.3.5
confirmation.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import bim_geometry_builder as wall_builder
from app.services import height_verification_ledger as ledger_service

STAGE = "5.1.8"
SCHEMA_VERSION = "0.1.0"
SUPPLEMENT_ARTIFACT_NAME = "engineering_element_candidates_stage5_1_8_supplement.json"
SOURCE_TAG = "stage5_1_8_supplement"
PAIR_TYPES = {
    "wall_edge_pair_candidate",
    "wall_geometry_supported_candidate",
    "unclassified_parallel_line_pair_candidate",
}

router = APIRouter(tags=["Stage 5.1.8 recognition supplement"])
PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pair_key(candidate: dict[str, Any]) -> frozenset | None:
    source = candidate.get("source")
    if not isinstance(source, dict):
        return None
    try:
        refs = [(int(source[k][0]), int(source[k][1])) for k in ("line_a", "line_b")]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return frozenset(refs)


def validate_pair_geometry(candidate: dict[str, Any], lines_by_key: dict) -> str:
    """'geometry_validated', or a short reason the pair fails the builder's checks."""
    key = pair_key(candidate)
    if key is None or len(key) != 2:
        return "invalid_source_references"
    lines = []
    for ref in sorted(key):
        matches = [p for p in lines_by_key.get(ref, []) if str(p.get("type", "")).lower() == "line"]
        if len(matches) != 1:
            return "source_line_missing_or_duplicated"
        lines.append(matches[0])
    source = candidate["source"]
    ordered = {tuple(int(v) for v in source["line_a"]): None, tuple(int(v) for v in source["line_b"]): None}
    by_ref = {(int(p["path_index"]), int(p["item_index"])): p for p in lines}
    line_a, line_b = (by_ref[ref] for ref in ordered)
    try:
        footprint = wall_builder._wall_footprint(line_a, line_b, candidate.get("geometry_type"))
        wall_builder._validate_candidate_bbox(candidate, list(footprint))
    except ValueError as exc:
        return f"geometry_invalid: {exc}"
    return "geometry_validated"


def build_drawing_supplement(
    *,
    base_drawing: dict[str, Any],
    fresh_drawing: dict[str, Any],
    line_loader: Callable[[int, set], dict],
) -> dict[str, Any]:
    """Compare a fresh recognition run with Stage 5.1.6 for one drawing."""
    if fresh_drawing.get("source_sha256") != base_drawing.get("source_sha256"):
        raise ValueError("The PDF changed since Stage 5.1.6; re-run full recognition instead")
    drawing_id = base_drawing["drawing_id"]
    base_pages = {p.get("page_number"): p for p in base_drawing.get("pages", []) if isinstance(p, dict)}

    pages, added_total, by_orientation = [], 0, {"horizontal": 0, "vertical": 0}
    for fresh_page in fresh_drawing.get("pages", []):
        page_number = fresh_page.get("page_number")
        base_page = base_pages.get(page_number)
        if base_page is None:
            continue
        existing_keys = {pair_key(c) for c in base_page.get("candidates", []) if isinstance(c, dict)}
        added = []
        for candidate in fresh_page.get("candidates", []):
            if (
                isinstance(candidate, dict)
                and candidate.get("element_type") in PAIR_TYPES
                and candidate.get("geometry_type") in wall_builder.SUPPORTED_WALL_GEOMETRIES
                and pair_key(candidate) not in existing_keys
            ):
                added.append(copy.deepcopy(candidate))
                existing_keys.add(pair_key(candidate))
        unvalidated = [
            c for c in base_page.get("candidates", [])
            if isinstance(c, dict) and c.get("element_type") == "unclassified_parallel_line_pair_candidate"
            and c.get("geometry_type") in wall_builder.SUPPORTED_WALL_GEOMETRIES
        ]

        wanted: set = set()
        for candidate in added + unvalidated:
            wanted |= set(pair_key(candidate) or ())
        lines_by_key = line_loader(page_number, wanted) if wanted else {}

        for candidate in added:
            candidate["recognition_source"] = SOURCE_TAG
            candidate["geometry_validation_status"] = validate_pair_geometry(candidate, lines_by_key)
            orientation = "vertical" if "vertical" in candidate["geometry_type"] else "horizontal"
            by_orientation[orientation] += 1
        existing_validation = {}
        for candidate in unvalidated:
            try:
                element_id = ledger_service.wall_element_id(drawing_id, page_number, candidate)
            except (TypeError, ValueError):
                continue
            existing_validation[element_id] = validate_pair_geometry(candidate, lines_by_key)

        added_total += len(added)
        pages.append({
            "page_number": page_number,
            "added_candidates": added,
            "existing_candidate_validation": existing_validation,
            "added_count": len(added),
            "added_geometry_validated": sum(
                1 for c in added if c["geometry_validation_status"] == "geometry_validated"
            ),
        })

    return {
        "drawing_id": drawing_id,
        "filename": base_drawing.get("filename"),
        "source_sha256": base_drawing.get("source_sha256"),
        "recognition_version": fresh_drawing.get("recognition_version"),
        "generated_at": _now(),
        "summary": {"added_candidates": added_total, "added_by_orientation": by_orientation},
        "pages": pages,
    }


def merge_supplement(recognition: dict[str, Any], supplement: dict[str, Any] | None) -> dict[str, Any]:
    """Return the Stage 5.1.6 report with supplement candidates added (input is not modified)."""
    if not isinstance(supplement, dict) or supplement.get("project_id") != recognition.get("project_id"):
        return recognition
    entries = {d.get("drawing_id"): d for d in supplement.get("drawings", []) if isinstance(d, dict)}
    merged = dict(recognition)
    merged_drawings = []
    for drawing in recognition.get("drawings", []):
        entry = entries.get(drawing.get("drawing_id")) if isinstance(drawing, dict) else None
        if not entry or entry.get("source_sha256") != drawing.get("source_sha256"):
            merged_drawings.append(drawing)
            continue
        extra = {p.get("page_number"): p for p in entry.get("pages", [])}
        new_drawing = dict(drawing)
        new_pages = []
        for page in drawing.get("pages", []):
            add = extra.get(page.get("page_number"))
            if not add:
                new_pages.append(page)
                continue
            validation = add.get("existing_candidate_validation", {})
            candidates = []
            for candidate in page.get("candidates", []):
                status = None
                if candidate.get("element_type") == "unclassified_parallel_line_pair_candidate":
                    try:
                        status = validation.get(ledger_service.wall_element_id(
                            drawing["drawing_id"], page["page_number"], candidate))
                    except (TypeError, ValueError, KeyError):
                        status = None
                candidates.append({**candidate, "geometry_validation_status": status} if status else candidate)
            candidates += list(add.get("added_candidates", []))
            new_pages.append({**page, "candidates": candidates, "candidate_count": len(candidates)})
        new_drawing["pages"] = new_pages
        merged_drawings.append(new_drawing)
    merged["drawings"] = merged_drawings
    merged["supplement"] = {"stage": STAGE, "artifact": SUPPLEMENT_ARTIFACT_NAME,
                            "drawings": sorted(entries)}
    return merged


def read_recognition(folder: Path) -> dict[str, Any]:
    """Stage 5.1.6 recognition with the Stage 5.1.8 supplement merged in (if any)."""
    recognition = ledger_service.read_required_artifact(folder, ledger_service.RECOGNITION_ARTIFACT_NAME)
    supplement = ledger_service.read_optional_artifact(folder, SUPPLEMENT_ARTIFACT_NAME)
    return merge_supplement(recognition, supplement)


def generate_supplement(folder: Path, project_id: str, drawing_ids: list[str]) -> tuple[dict[str, Any], Path]:
    """Re-run recognition for the given drawings and save/replace their supplement entries."""
    from app.services import engineering_element_recognition as recognizer
    from app.services.wall_mesh_generation import GenerationBlocked, load_sidecar_lines

    folder = Path(folder)
    base = ledger_service.read_required_artifact(folder, ledger_service.RECOGNITION_ARTIFACT_NAME)
    if base.get("project_id") != project_id:
        raise ValueError("Recognition report project ownership mismatch")
    project = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
    records = {str(d.get("id")): d for d in project.get("drawings", []) if isinstance(d, dict)}
    base_drawings = {d.get("drawing_id"): d for d in base.get("drawings", []) if isinstance(d, dict)}

    path = folder / "artifacts" / SUPPLEMENT_ARTIFACT_NAME
    existing = ledger_service.read_optional_artifact(folder, SUPPLEMENT_ARTIFACT_NAME) or {}
    entries = {d["drawing_id"]: d for d in existing.get("drawings", []) if isinstance(d, dict)}

    for drawing_id in drawing_ids:
        record = records.get(drawing_id)
        base_drawing = base_drawings.get(drawing_id)
        if record is None or base_drawing is None:
            raise ValueError(f"Drawing {drawing_id} is not in this project's Stage 5.1.6 report")
        pdf = folder / "drawings" / Path(str(record.get("stored_filename") or f"{drawing_id}.pdf")).name
        fresh = recognizer.recognize_pdf_elements(pdf, record)

        def line_loader(page_number: int, wanted: set) -> dict:
            try:
                found, _ = load_sidecar_lines(
                    folder / "artifacts", drawing_id, page_number, wanted, base_drawing.get("source_sha256"),
                )
                return found
            except GenerationBlocked:
                return {}

        entries[drawing_id] = build_drawing_supplement(
            base_drawing=base_drawing, fresh_drawing=fresh, line_loader=line_loader,
        )

    report = {
        "stage": STAGE,
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "recognition_supplement",
        "project_id": project_id,
        "base_artifact": ledger_service.RECOGNITION_ARTIFACT_NAME,
        "updated_at": _now(),
        "fix": "separate horizontal and vertical candidate budgets in _wall_pair_candidates",
        "drawings": sorted(entries.values(), key=lambda d: d.get("filename") or ""),
        "limitations": [
            "Added candidates are unverified geometric hypotheses; most are 'unclassified' pairs.",
            "An unclassified pair becomes a wall only through a reviewer's Stage 5.3.5 confirmation.",
            "The Stage 5.1.6 report is not modified; this file only adds to it.",
        ],
    }
    ledger_service.save_json_atomic(path, report)
    return report, path


class SupplementRequest(BaseModel):
    drawing_ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/api/projects/{project_id}/geometry/stage5-1-8/supplement")
def create_supplement(project_id: str, payload: SupplementRequest):
    try:
        safe_project = str(UUID(project_id))
        drawing_ids = [str(UUID(d)) for d in payload.drawing_ids]
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project or drawing not found") from None
    folder = PROJECTS_DIR / safe_project
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        report, path = generate_supplement(folder, safe_project, drawing_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    done = [d for d in report["drawings"] if d["drawing_id"] in drawing_ids]
    return {
        "message": "Stage 5.1.8 recognition supplement updated.",
        "artifact_file": path.name,
        "drawings": [{"drawing_id": d["drawing_id"], "filename": d["filename"], **d["summary"]} for d in done],
    }
