"""API: one-click PDF-to-model generation and its progress."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.services import auto_pipeline

router = APIRouter(tags=["Automatic model"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"


def _folder(project_id: str) -> tuple[str, Path]:
    try:
        safe = str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project not found") from None
    folder = PROJECTS_DIR / safe
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe, folder


@router.post("/api/projects/{project_id}/auto-model", status_code=202)
def start_auto_model(project_id: str):
    """Start generating the model from the project's PDFs; poll the GET route for progress."""
    safe, folder = _folder(project_id)
    try:
        return auto_pipeline.start(folder, safe)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/projects/{project_id}/auto-model")
def get_auto_model_status(project_id: str):
    safe, folder = _folder(project_id)
    status = auto_pipeline.read_status(folder, safe)
    if status is None:
        return {"project_id": safe, "state": "not_started", "steps": [
            {"key": key, "label": label, "status": "pending", "detail": ""} for key, label in auto_pipeline.STEPS]}
    return status
