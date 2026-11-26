"""Stage 5.4 API: generate the verified wall model for one drawing page."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.services import wall_mesh_generation as generation

router = APIRouter(tags=["Stage 5.4 verified wall model"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail=f"{label} not found") from None


@router.post("/api/projects/{project_id}/geometry/stage5-4/generate")
def generate_verified_wall_model(
    project_id: str,
    drawing_id: str = Query(..., min_length=1, max_length=64),
    page_number: int = Query(1, ge=1),
):
    """
    Build 3D walls for the reviewer-confirmed walls on one page and save them
    as artifacts/mesh_batches/<drawing>_stage5_4.json. View them through
    GET /api/projects/{id}/drawings/{drawing_id}/meshes.
    """
    safe_project = _uuid(project_id, "Project")
    safe_drawing = _uuid(drawing_id, "Drawing")
    folder = PROJECTS_DIR / safe_project
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        artifact, path = generation.generate_page_model(folder, safe_project, safe_drawing, page_number)
    except generation.GenerationBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the model: {exc}") from exc

    return {
        "message": "Stage 5.4 verified wall model generated.",
        "project_id": safe_project,
        "drawing_id": safe_drawing,
        "page_number": page_number,
        "artifact_file": path.name,
        "summary": artifact["summary"],
        "failed": artifact["failed"],
    }
