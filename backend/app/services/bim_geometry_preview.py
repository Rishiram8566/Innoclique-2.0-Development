from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import ijson
from fastapi import APIRouter, HTTPException, Query

from app.services import bim_geometry_builder as wall_builder

router = APIRouter(tags=["Stage 5.3 BIM geometry"])

# backend/app/services -> backend/data/projects
PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"

RECOGNITION_ARTIFACT = "engineering_element_candidates_stage5_1_6.json"
GEOMETRY_ARTIFACT = "drawing_geometry_stage5.json"


def _project_folder(project_id: str) -> tuple[str, Path]:
    try:
        safe_id = str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project not found") from None

    folder = PROJECTS_DIR / safe_id
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe_id, folder


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Required artifact not found: {path.name}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read {path.name}: {exc}") from exc

    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"Invalid JSON object in {path.name}")
    return value


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _find_recognition_drawing(report: dict[str, Any], drawing_id: str) -> dict[str, Any]:
    drawings = report.get("drawings")
    if not isinstance(drawings, list):
        raise HTTPException(status_code=422, detail="Recognition report has no drawings list")

    matches = [item for item in drawings if isinstance(item, dict) and item.get("drawing_id") == drawing_id]
    if len(matches) != 1:
        raise HTTPException(
            status_code=404 if not matches else 409,
            detail="Drawing is missing or duplicated in the recognition report",
        )
    return matches[0]


def _load_one_geometry_drawing(geometry_path: Path, drawing_id: str) -> dict[str, Any]:
    """Load only the requested drawing object; never load the 2.6 GB project artifact."""
    try:
        with geometry_path.open("rb") as stream:
            for drawing in ijson.items(stream, "drawings.item"):
                if isinstance(drawing, dict) and drawing.get("drawing_id") == drawing_id:
                    return drawing
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stage 5.0 geometry artifact not found") from None
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not stream Stage 5.0 geometry: {exc}") from exc

    raise HTTPException(status_code=404, detail="Drawing not found in Stage 5.0 geometry artifact")


def _get_page(report_drawing: dict[str, Any], page_number: int) -> dict[str, Any]:
    pages = report_drawing.get("pages")
    if not isinstance(pages, list):
        raise HTTPException(status_code=422, detail="Recognition drawing has no pages list")
    matches = [p for p in pages if isinstance(p, dict) and p.get("page_number") == page_number]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail=f"Recognition page {page_number} not found")
    return matches[0]


def _source_line_map(primitives: Any, candidate: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
    source = candidate.get("source")
    if not isinstance(source, dict):
        raise ValueError("Candidate source references are missing")

    refs = []
    for key in ("line_a", "line_b"):
        ref = source.get(key)
        if not isinstance(ref, (list, tuple)) or len(ref) != 2:
            raise ValueError(f"Candidate {key} reference is invalid")
        refs.append((int(ref[0]), int(ref[1])))

    if refs[0] == refs[1]:
        raise ValueError("Candidate references the same source line twice")

    wanted = set(refs)
    found: dict[tuple[int, int], dict[str, Any]] = {}
    for primitive in primitives:
        if not isinstance(primitive, dict):
            continue
        key = (primitive.get("path_index"), primitive.get("item_index"))
        if key in wanted:
            if primitive.get("type") != "line":
                raise ValueError(f"Source reference {key} is not a line primitive")
            if int(primitive.get("page_number", 0)) != page_number:
                raise ValueError(f"Source reference {key} belongs to another page")
            found[key] = primitive
            if len(found) == 2:
                break

    missing = wanted - set(found)
    if missing:
        raise ValueError(f"Source line references not found: {sorted(missing)}")
    return [found[refs[0]], found[refs[1]]]


def _finite_bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("Candidate bounding box must contain four coordinates")
    result = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Candidate bounding box contains a non-finite value")
        result.append(number)
    return result


def _preview_candidate(
    *,
    candidate: dict[str, Any],
    lines: list[dict[str, Any]],
    recognition_drawing: dict[str, Any],
    project_id: str,
    drawing_id: str,
) -> dict[str, Any]:
    geometry_type = candidate.get("geometry_type")
    if candidate.get("element_type") not in wall_builder.SUPPORTED_WALL_CANDIDATE_TYPES:
        raise ValueError("Unsupported candidate element type")
    if geometry_type not in wall_builder.SUPPORTED_WALL_GEOMETRIES:
        raise ValueError("Unsupported wall geometry orientation")
    if candidate.get("verification_required") is not True:
        raise ValueError("Candidate must remain verification-required")

    x_min, y_min, x_max, y_max = wall_builder._wall_footprint(
        lines[0], lines[1], geometry_type
    )
    expected_bbox = [x_min, y_min, x_max, y_max]
    wall_builder._validate_candidate_bbox(candidate, expected_bbox)

    if geometry_type == "parallel_horizontal_lines":
        length = x_max - x_min
        thickness = y_max - y_min
    else:
        length = y_max - y_min
        thickness = x_max - x_min

    if not all(math.isfinite(v) and v > 0 for v in (length, thickness)):
        raise ValueError("Derived footprint dimensions are invalid")

    source = candidate["source"]
    return {
        "element_id": (
            f"{drawing_id}:p{int(candidate['page_number'])}:wall:"
            f"{int(source['line_a'][0])}-{int(source['line_a'][1])}:"
            f"{int(source['line_b'][0])}-{int(source['line_b'][1])}"
        ),
        "element_type": "wall_footprint_candidate",
        "geometry_status": "uncalibrated_2d_preview",
        "verification_required": True,
        "approval_status": "not_approved",
        "coordinate_system": {
            "unit": "pdf_point",
            "frame": "page_local",
            "x_axis": "right",
            "y_axis": "down",
            "z_axis": "not_defined",
            "shared_building_coordinates_established": False,
        },
        "footprint_pdf_points": {
            "bbox": [round(v, 6) for v in expected_bbox],
            "polygon": [
                [round(x_min, 6), round(y_min, 6)],
                [round(x_max, 6), round(y_min, 6)],
                [round(x_max, 6), round(y_max, 6)],
                [round(x_min, 6), round(y_max, 6)],
                [round(x_min, 6), round(y_min, 6)],
            ],
            "length": round(length, 6),
            "thickness": round(thickness, 6),
        },
        "provenance": {
            "source_pdf_sha256": recognition_drawing.get("source_sha256"),
            "candidate_type": candidate.get("element_type"),
            "candidate_geometry_type": geometry_type,
            "candidate_confidence": candidate.get("confidence"),
            "candidate_evidence": candidate.get("evidence", []),
            "source_lines": {
                "line_a": {
                    "path_index": int(source["line_a"][0]),
                    "item_index": int(source["line_a"][1]),
                },
                "line_b": {
                    "path_index": int(source["line_b"][0]),
                    "item_index": int(source["line_b"][1]),
                },
            },
            "source_bbox_pdf_points": [round(v, 6) for v in expected_bbox],
        },
        "limitations": [
            "This is a 2D page-local footprint preview in PDF points, not a metric BIM mesh.",
            "No page calibration is available or applied.",
            "Candidate is an unverified geometric hypothesis, not a confirmed wall.",
            "Wall height, level placement, and shared building coordinates are not established.",
            "This preview is not a native Revit element and is not approved for construction.",
        ],
    }


@router.post("/api/projects/{project_id}/geometry/stage5-3/preview")
def create_stage5_3_geometry_preview(
    project_id: str,
    drawing_id: str = Query(..., min_length=1, max_length=200),
    page_number: int = Query(1, ge=1),
    max_candidates: int = Query(100, ge=1, le=500),
):
    """
    Stage 5.3: create source-linked, uncalibrated 2D wall-footprint previews.
    This endpoint deliberately does not convert PDF points to millimetres or invent height.
    """
    safe_project_id, folder = _project_folder(project_id)

    recognition_path = folder / "artifacts" / RECOGNITION_ARTIFACT
    geometry_path = folder / "artifacts" / GEOMETRY_ARTIFACT

    recognition_report = _read_json(recognition_path)
    recognition_drawing = _find_recognition_drawing(recognition_report, drawing_id)
    recognition_page = _get_page(recognition_drawing, page_number)

    geometry_drawing = _load_one_geometry_drawing(geometry_path, drawing_id)

    recognition_hash = recognition_drawing.get("source_sha256")
    geometry_hash = geometry_drawing.get("source_sha256")
    if not recognition_hash or recognition_hash != geometry_hash:
        raise HTTPException(
            status_code=409,
            detail="Recognition and geometry source PDF hashes do not match. Re-extract/re-recognize this PDF.",
        )

    geometry_pages = geometry_drawing.get("pages")
    if not isinstance(geometry_pages, list):
        raise HTTPException(status_code=422, detail="Stage 5.0 drawing has no pages list")
    matching_geometry_pages = [
        p for p in geometry_pages
        if isinstance(p, dict) and p.get("page_number") == page_number
    ]
    if len(matching_geometry_pages) != 1:
        raise HTTPException(status_code=404, detail=f"Stage 5.0 page {page_number} not found")

    primitives = matching_geometry_pages[0].get("primitives")
    if not isinstance(primitives, list):
        raise HTTPException(status_code=422, detail="Stage 5.0 page has no primitive list")

    candidates = recognition_page.get("candidates", [])
    if not isinstance(candidates, list):
        raise HTTPException(status_code=422, detail="Recognition page candidates are invalid")

    wall_candidates = [
        candidate for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("element_type") in wall_builder.SUPPORTED_WALL_CANDIDATE_TYPES
    ]

    elements = []
    rejected = []
    for index, candidate in enumerate(wall_candidates[:max_candidates]):
        try:
            lines = _source_line_map(primitives, candidate, page_number)
            elements.append(
                _preview_candidate(
                    candidate=candidate,
                    lines=lines,
                    recognition_drawing=recognition_drawing,
                    project_id=safe_project_id,
                    drawing_id=drawing_id,
                )
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            rejected.append({
                "candidate_index": index,
                "source": candidate.get("source"),
                "reason": str(exc),
            })

    artifact = {
        "schema_version": "0.1.0",
        "stage": "5.3",
        "artifact_type": "uncalibrated_wall_footprint_preview",
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "project_id": safe_project_id,
        "drawing_id": drawing_id,
        "page_number": page_number,
        "source_pdf_sha256": recognition_hash,
        "coordinate_system": {
            "unit": "pdf_point",
            "frame": "page_local",
            "x_axis": "right",
            "y_axis": "down",
            "z_axis": "not_defined",
        },
        "calibration": {
            "status": "not_applied",
            "reason": "No verified Stage 5.2 calibration record was found for this page.",
            "metric_geometry_available": False,
        },
        "summary": {
            "wall_candidates_found": len(wall_candidates),
            "preview_elements_created": len(elements),
            "rejected_candidates": len(rejected),
            "truncated": len(wall_candidates) > max_candidates,
        },
        "elements": elements,
        "rejected_candidates": rejected,
        "limitations": [
            "Preview geometry is page-local and remains in PDF points.",
            "No metric scale, wall height, floor elevation, or building registration is inferred.",
            "Every element remains provisional and requires engineering verification.",
        ],
    }

    artifact_path = (
        folder / "artifacts" / "geometry_previews"
        / f"{drawing_id}_p{page_number}_stage5_3_preview.json"
    )
    try:
        _atomic_write_json(artifact_path, artifact)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save Stage 5.3 preview: {exc}") from exc

    return {
        "message": "Stage 5.3 uncalibrated source-linked footprint preview completed.",
        "project_id": safe_project_id,
        "drawing_id": drawing_id,
        "page_number": page_number,
        "summary": artifact["summary"],
        "calibration": artifact["calibration"],
        "artifact_file": str(artifact_path),
        "elements": elements,
        "rejected_candidates": rejected,
    }


@router.get("/api/projects/{project_id}/drawings/{drawing_id}/geometry-preview")
def get_stage5_3_geometry_preview(
    project_id: str,
    drawing_id: str,
    page_number: int = Query(1, ge=1),
):
    safe_project_id, folder = _project_folder(project_id)
    try:
        safe_drawing_id = str(UUID(drawing_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Drawing not found") from None

    artifact_path = (
        folder / "artifacts" / "geometry_previews"
        / f"{safe_drawing_id}_p{page_number}_stage5_3_preview.json"
    )
    artifact = _read_json(artifact_path)
    if artifact.get("project_id") != safe_project_id:
        raise HTTPException(status_code=422, detail="Preview project ownership mismatch")
    if artifact.get("drawing_id") != safe_drawing_id:
        raise HTTPException(status_code=422, detail="Preview drawing ownership mismatch")
    return artifact
