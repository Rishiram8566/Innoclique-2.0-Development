from pathlib import Path
import json
import math
from uuid import UUID

import pymupdf
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter(tags=["Phase 6 - BIM Scene"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"
RECOGNITION_FILE = "engineering_element_candidates_stage5_1_6.json"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not read required project artifact: {exc}",
        ) from exc


def _valid_bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return None

    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None

    if not all(math.isfinite(item) for item in bbox):
        return None

    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return None

    return bbox


def _resolve_drawing_pdf(project_folder: Path, drawing_id: str, drawing_record: dict) -> Path:
    """Resolve a PDF only from within this project's folder."""
    drawings_folder = project_folder / "drawings"
    candidates = [
        drawings_folder / f"{drawing_id}.pdf",
        drawings_folder / str(drawing_record.get("filename") or ""),
        project_folder / str(drawing_record.get("filename") or ""),
    ]

    # Some project records retain the stored relative path under one of these keys.
    for key in ("file_path", "stored_path", "path", "pdf_path", "storage_path"):
        value = drawing_record.get(key)
        if isinstance(value, str) and value.strip():
            raw_path = Path(value)
            candidates.append(raw_path if raw_path.is_absolute() else project_folder / raw_path)

    resolved_root = project_folder.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return resolved

    raise HTTPException(status_code=404, detail="Source PDF for this drawing was not found")


@router.get("/api/projects/{project_id}/phase6/preview/{drawing_id}")
def get_phase6_pdf_preview(
    project_id: str,
    drawing_id: str,
    page_number: int = Query(default=1, ge=1),
):
    """Render one source PDF page as PNG for the Phase 6 drawing viewer."""
    try:
        safe_project_id = str(UUID(project_id))
        safe_drawing_id = str(UUID(drawing_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project or drawing not found")

    project_folder = PROJECTS_DIR / safe_project_id
    if not project_folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")

    project = _read_json(project_folder / "project.json")
    project_drawing = next(
        (
            item for item in project.get("drawings", [])
            if str(item.get("id", "")) == safe_drawing_id
        ),
        None,
    )
    if project_drawing is None:
        raise HTTPException(status_code=404, detail="Drawing does not belong to this project")

    pdf_path = _resolve_drawing_pdf(project_folder, safe_drawing_id, project_drawing)

    try:
        with pymupdf.open(pdf_path) as document:
            if page_number > document.page_count:
                raise HTTPException(
                    status_code=404,
                    detail=f"Page {page_number} does not exist; PDF has {document.page_count} page(s)",
                )
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            image_bytes = pixmap.tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not render source PDF page: {exc}") from exc

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/projects/{project_id}/phase6/scene/{drawing_id}")
def get_phase6_scene(
    project_id: str,
    drawing_id: str,
    page_number: int = Query(default=1, ge=1),
):
    """Phase 6.0: create a source-grounded 2D scene from recognized candidates."""

    try:
        safe_project_id = str(UUID(project_id))
        safe_drawing_id = str(UUID(drawing_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project or drawing not found")

    project_folder = PROJECTS_DIR / safe_project_id
    if not project_folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")

    project = _read_json(project_folder / "project.json")
    project_drawing = next(
        (
            item for item in project.get("drawings", [])
            if str(item.get("id", "")) == safe_drawing_id
        ),
        None,
    )

    if project_drawing is None:
        raise HTTPException(
            status_code=404,
            detail="Drawing does not belong to this project",
        )

    report_path = project_folder / "artifacts" / RECOGNITION_FILE
    if not report_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Stage 5.1.6 recognition report not found. Run element recognition first.",
        )

    report = _read_json(report_path)
    if report.get("project_id") != safe_project_id:
        raise HTTPException(
            status_code=422,
            detail="Recognition report project ownership mismatch",
        )

    drawing_report = next(
        (
            item for item in report.get("drawings", [])
            if str(item.get("drawing_id", item.get("id", ""))) == safe_drawing_id
        ),
        None,
    )

    if drawing_report is None:
        raise HTTPException(
            status_code=404,
            detail="Drawing is not present in the recognition report",
        )

    page_report = next(
        (
            item for item in drawing_report.get("pages", [])
            if int(item.get("page_number", 0)) == page_number
        ),
        None,
    )

    if page_report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_number} is not present in the recognition report",
        )

    elements = []
    rejected = 0

    for index, candidate in enumerate(page_report.get("candidates", [])):
        if not isinstance(candidate, dict):
            rejected += 1
            continue

        bbox = _valid_bbox(candidate.get("bbox_pdf_points"))
        if bbox is None:
            rejected += 1
            continue

        x0, y0, x1, y1 = bbox
        source = candidate.get("source")
        if not isinstance(source, dict):
            source = {}

        line_a = source.get("line_a")
        line_b = source.get("line_b")

        elements.append({
            "element_id": (
                f"{safe_drawing_id}:p{page_number}:candidate:{index}"
            ),
            "element_type": candidate.get(
                "element_type", "unclassified_candidate"
            ),
            "geometry_type": candidate.get("geometry_type"),
            "geometry_status": "2d_plan_footprint",
            "verification_required": True,
            "approval_status": "not_approved",
            "confidence": candidate.get("confidence"),
            "confidence_is_calibrated_probability": False,
            "bbox_pdf_points": bbox,
            "plan_geometry": {
                "outline": [
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ],
                "center": [
                    round((x0 + x1) / 2, 4),
                    round((y0 + y1) / 2, 4),
                ],
                "width": round(x1 - x0, 4),
                "height": round(y1 - y0, 4),
            },
            "source": {
                "line_a": line_a,
                "line_b": line_b,
                "evidence": candidate.get("evidence", []),
                "semantic_text_hints": candidate.get(
                    "semantic_text_hints", []
                ),
            },
        })

    return {
        "schema_version": "0.1.0",
        "phase": "6.0",
        "scene_type": "source_grounded_2d_plan",
        "project_id": safe_project_id,
        "drawing_id": safe_drawing_id,
        "drawing_filename": project_drawing.get("filename"),
        "page_number": page_number,
        "lod": None,
        "coordinate_system": {
            "unit": "pdf_points",
            "frame": "drawing_page_local",
            "x_axis": "right",
            "y_axis": "down",
            "z_axis_defined": False,
            "shared_building_coordinates_established": False,
        },
        "summary": {
            "candidate_count": len(page_report.get("candidates", [])),
            "renderable_footprint_count": len(elements),
            "rejected_invalid_bbox_count": rejected,
            "approved_bim_element_count": 0,
        },
        "elements": elements,
        "limitations": [
            "This scene contains 2D plan footprints, not completed 3D BIM elements.",
            "PDF points are page-local and are not calibrated millimetres.",
            "Candidate classifications remain unverified hypotheses.",
            "No element height, elevation, or shared building coordinate system is inferred.",
            "No native Revit elements are created by this endpoint.",
        ],
    }
