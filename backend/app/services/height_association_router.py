"""Stage 5.3.4 API: wall-height evidence association report."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME,
    generate_project_height_association,
)

router = APIRouter(tags=["Stage 5.3.4 height evidence association"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"

ROUTE = "/api/projects/{project_id}/geometry/stage5-3-4/height-association"


def _project_folder(project_id: str) -> tuple[str, Path]:
    try:
        safe_id = str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project not found") from None
    folder = PROJECTS_DIR / safe_id
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe_id, folder


def _without_sources(report: dict) -> dict:
    """Drop per-occurrence source lists; counts and evidence IDs remain."""
    trimmed = dict(report)
    trimmed["level_register"] = [
        {k: v for k, v in level.items() if k != "source_references"}
        for level in report.get("level_register", [])
    ]
    trimmed["abbreviation_context"] = [
        {k: v for k, v in item.items() if k != "source_references"}
        for item in report.get("abbreviation_context", [])
    ]
    trimmed["sources_included"] = False
    return trimmed


@router.get(ROUTE)
def get_height_association(
    project_id: str,
    include_sources: bool = Query(False),
):
    """Return the saved Stage 5.3.4 report. Nothing in it is approved."""
    safe_id, folder = _project_folder(project_id)
    path = folder / "artifacts" / OUTPUT_ARTIFACT_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Stage 5.3.4 report not generated yet. POST to this route first.",
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read report: {exc}") from exc
    if report.get("project_id") != safe_id:
        raise HTTPException(status_code=422, detail="Report project ownership mismatch")
    return report if include_sources else _without_sources(report)


@router.post(ROUTE)
def create_height_association(project_id: str):
    """
    Regenerate the Stage 5.3.4 report from the Stage 5.3.3 evidence artifact.

    The report is fully derived (no human decisions are stored in it), so it
    is replaced on each run. No approvals or geometry are produced.
    """
    safe_id, folder = _project_folder(project_id)
    try:
        report, path = generate_project_height_association(folder, overwrite=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "message": "Stage 5.3.4 height evidence association completed.",
        "project_id": safe_id,
        "status": report["status"],
        "artifact_file": path.name,
        "summary": report["summary"],
        "level_unit_convention": report["level_unit_convention"],
    }
