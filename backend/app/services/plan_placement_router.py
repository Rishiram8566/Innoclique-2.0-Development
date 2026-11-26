"""Stage 5.4 API: plan placements (level + shared reference point) and the building model."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.services import building_model
from app.services import height_verification_ledger as ledger_service
from app.services.height_verification_router import _LEDGER_LOCK
from app.services import wall_mesh_generation as generation
from app.services.recognition_supplement import read_recognition
from app.services.sheet_registration import _page_sizes, compare_sheets, sheet_index
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ASSOCIATION_ARTIFACT_NAME,
)

router = APIRouter(tags=["Stage 5.4 plan placement and building model"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"
BASE = "/api/projects/{project_id}/geometry/stage5-4"


class PlacementRequest(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1)
    base_level_key: str = Field(min_length=1, max_length=64)
    top_level_key: str | None = Field(default=None, max_length=64)
    reference_point_pdf: list[float] | None = Field(default=None, min_length=2, max_length=2)
    reference_label: str | None = Field(default=None, max_length=120)
    # Reuse the reference point of a placed plan on a registered sheet instead of clicking.
    reference_from_placement_id: str | None = Field(default=None, max_length=64)
    reference_snapped_line: dict | None = None
    reviewer: str = Field(min_length=2, max_length=120)
    justification: str = Field(min_length=20, max_length=2000)
    cross_drawing_justification: str | None = Field(default=None, max_length=2000)


class RevokeRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=20, max_length=2000)


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail=f"{label} not found") from None


def _folder(project_id: str) -> tuple[str, Path]:
    safe = _uuid(project_id, "Project")
    folder = PROJECTS_DIR / safe
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe, folder


@router.get(BASE + "/placement-options")
def get_placement_options(
    project_id: str,
    drawing_id: str = Query(..., min_length=1, max_length=64),
    page_number: int = Query(1, ge=1),
):
    safe, folder = _folder(project_id)
    try:
        ledger = ledger_service.load_ledger(folder, safe)
        height_report = ledger_service.read_optional_artifact(folder, HEIGHT_ASSOCIATION_ARTIFACT_NAME)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ledger_service.placement_options(
        height_report=height_report, ledger=ledger,
        drawing_id=_uuid(drawing_id, "Drawing"), page_number=page_number,
    )


@router.post(BASE + "/placements", status_code=201)
def create_placement(project_id: str, payload: PlacementRequest):
    safe, folder = _folder(project_id)
    request = payload.model_dump()
    request["drawing_id"] = _uuid(request["drawing_id"], "Drawing")
    with _LEDGER_LOCK:
        try:
            if request.get("reference_from_placement_id"):
                _apply_reference_transfer(folder, safe, request)
            recognition = read_recognition(folder)
            height_report = ledger_service.read_optional_artifact(folder, HEIGHT_ASSOCIATION_ARTIFACT_NAME)
            if height_report is None:
                raise HTTPException(status_code=409, detail="Stage 5.3.4 level register has not been generated.")
            ledger = ledger_service.load_ledger(folder, safe)
            record = ledger_service.build_placement_record(
                ledger=ledger, recognition_report=recognition,
                height_report=height_report, request=request,
            )
        except ledger_service.VerificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ledger_service.apply_placement(ledger, record)
        try:
            ledger_service.save_ledger(folder, ledger)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not save ledger: {exc}") from exc
    return {"message": "Plan placement recorded.", "placement": record}


def _apply_reference_transfer(folder: Path, project_id: str, request: dict) -> None:
    """Copy a placed plan's reference point after proving the two sheets are registered."""
    ledger = ledger_service.load_ledger(folder, project_id)
    source = ledger_service.placement_by_id(ledger, request["reference_from_placement_id"])
    if not source or source.get("status") != "active":
        raise HTTPException(status_code=404, detail="Source placement not found or not active")
    sizes = _page_sizes(folder)
    registration = compare_sheets(
        sheet_index(folder), source["drawing_id"], request["drawing_id"],
        sizes.get(source["drawing_id"]), sizes.get(request["drawing_id"]),
    )
    if not registration["registered"]:
        raise HTTPException(status_code=422, detail="Sheets are not registered: " + "; ".join(registration["reasons"]))
    request["reference_point_pdf"] = list(source["reference_point"]["point_pdf"])
    request["reference_label"] = source["reference_point"]["label"]
    request["reference_transfer"] = {
        "placement_id": source["placement_id"],
        "drawing_id": source["drawing_id"],
        "drawing_title": source.get("drawing_title"),
        "registration": registration,
    }


@router.post("/api/projects/{project_id}/model/building/generate")
def generate_all_placed_plans(project_id: str):
    """Generate Stage 5.4 walls for every placed plan; report each plan's result."""
    safe, folder = _folder(project_id)
    ledger = ledger_service.load_ledger(folder, safe)
    results = []
    for placement in ledger.get("plan_placements", []):
        if placement.get("status") != "active":
            continue
        item = {"drawing_id": placement["drawing_id"], "drawing_title": placement.get("drawing_title")}
        try:
            artifact, _ = generation.generate_page_model(folder, safe, placement["drawing_id"], placement["page_number"])
            item.update(status="generated", **artifact["summary"])
        except generation.GenerationBlocked as exc:
            item.update(status="blocked", reason=str(exc))
        except (FileNotFoundError, ValueError) as exc:
            item.update(status="error", reason=str(exc))
        results.append(item)
    return {"message": "Generation run for all placed plans.", "plans": results}


@router.post(BASE + "/placements/{placement_id}/revoke")
def revoke_placement(project_id: str, placement_id: str, payload: RevokeRequest):
    safe, folder = _folder(project_id)
    with _LEDGER_LOCK:
        try:
            ledger = ledger_service.load_ledger(folder, safe)
            record = ledger_service.revoke_placement(
                ledger, placement_id, reviewer=payload.reviewer, reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ledger_service.VerificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            ledger_service.save_ledger(folder, ledger)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not save ledger: {exc}") from exc
    return {"message": "Plan placement revoked.", "placement": record}


@router.get("/api/projects/{project_id}/model/building")
def get_building_model(project_id: str):
    safe, folder = _folder(project_id)
    try:
        return Response(content=building_model.cached_building_model_json(folder, safe), media_type="application/json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
