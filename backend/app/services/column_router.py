"""Stage 5.4 API: detected columns (LOD 100) and legend-hatch walls (LOD 300); confirmation and revocation."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import column_detection
from app.services import hatch_wall_detection
from app.services import height_verification_ledger as ledger_service
from app.services.height_verification_router import _LEDGER_LOCK
from app.services.recognition_supplement import read_recognition

router = APIRouter(tags=["Stage 5.4 columns"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"
BASE = "/api/projects/{project_id}/geometry/stage5-4/columns"
WALLS = "/api/projects/{project_id}/geometry/stage5-4/hatch-walls"


class ColumnConfirmRequest(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1)
    column_ids: list[str] = Field(min_length=1, max_length=2000)
    reviewer: str = Field(min_length=2, max_length=120)
    justification: str = Field(min_length=20, max_length=2000)


class HatchWallConfirmRequest(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1)
    element_ids: list[str] = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=2, max_length=120)
    justification: str = Field(min_length=20, max_length=2000)


class RevokeRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=20, max_length=2000)


def _folder(project_id: str) -> tuple[str, Path]:
    try:
        safe = str(UUID(project_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project not found") from None
    folder = PROJECTS_DIR / safe
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return safe, folder


def _drawing(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Drawing not found") from None


def _page_candidates(folder: Path, drawing_id: str, page_number: int) -> tuple[list[dict], dict]:
    recognition = read_recognition(folder)
    try:
        _, page = ledger_service._find_recognition_page(recognition, drawing_id, page_number)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    calibration = ledger_service._page_calibration(
        ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME),
        drawing_id, page_number,
    )
    size = ledger_service._page_size_pdf(page)
    candidates = column_detection.page_columns(
        folder, drawing_id, page_number, page_size=size or (0.0, 0.0),
        millimetres_per_pdf_point=calibration.get("millimetres_per_pdf_point"),
    ) if size else []
    return candidates, calibration


@router.get(BASE)
def get_page_columns(
    project_id: str,
    drawing_id: str = Query(..., min_length=1, max_length=64),
    page_number: int = Query(1, ge=1),
):
    safe, folder = _folder(project_id)
    drawing = _drawing(drawing_id)
    candidates, calibration = _page_candidates(folder, drawing, page_number)
    ledger = ledger_service.load_ledger(folder, safe)
    active = ledger_service.active_column_confirmations(ledger)
    for candidate in candidates:
        record = active.get(candidate["column_id"])
        candidate["confirmation"] = (
            {"status": "confirmed", "confirmation_id": record["confirmation_id"], "height_mm": record["height_mm"]}
            if record else {"status": "unconfirmed"}
        )
    placement = ledger_service.find_active_placement(ledger, drawing, page_number)
    return {
        "drawing_id": drawing,
        "page_number": page_number,
        "calibrated": calibration.get("status") == "calibrated",
        "storey_height_mm": (placement or {}).get("storey_height_mm"),
        "placement_id": (placement or {}).get("placement_id"),
        "summary": {
            "detected": len(candidates),
            "confirmed": sum(1 for c in candidates if c["confirmation"]["status"] == "confirmed"),
        },
        "candidates": candidates,
        "confirmations_on_page": _confirmations_on_page(ledger, drawing, page_number, "column"),
        "rules": [
            "Grid lines come from grid labels printed at both ends of a line.",
            "A column is a square outline of 300-1200 mm centred within 4 PDF points of a grid intersection, "
            "or - for perimeter columns - with a grid intersection inside its outline.",
            "Confirmed columns run the plan's full storey height (slab to slab).",
        ],
    }


def _confirmations_on_page(ledger, drawing, page_number, kind):
    return [
        r for r in ledger.get("column_confirmations", [])
        if r.get("drawing_id") == drawing and r.get("page_number") == page_number
        and r.get("element_kind", "column") == kind
    ]


def _page_hatch_walls(folder: Path, drawing_id: str, page_number: int):
    columns, calibration = _page_candidates(folder, drawing_id, page_number)
    walls, signatures = hatch_wall_detection.page_hatch_walls(
        folder, drawing_id, page_number,
        millimetres_per_pdf_point=calibration.get("millimetres_per_pdf_point"), columns=columns,
    )
    return walls, signatures, calibration


@router.get(WALLS)
def get_page_hatch_walls(
    project_id: str,
    drawing_id: str = Query(..., min_length=1, max_length=64),
    page_number: int = Query(1, ge=1),
):
    safe, folder = _folder(project_id)
    drawing = _drawing(drawing_id)
    walls, signatures, calibration = _page_hatch_walls(folder, drawing, page_number)
    ledger = ledger_service.load_ledger(folder, safe)
    active = ledger_service.active_column_confirmations(ledger)
    for wall in walls:
        record = active.get(wall["wall_id"])
        wall["confirmation"] = (
            {"status": "confirmed", "confirmation_id": record["confirmation_id"], "height_mm": record["height_mm"]}
            if record else {"status": "unconfirmed"}
        )
    placement = ledger_service.find_active_placement(ledger, drawing, page_number)
    by_type: dict[str, dict[str, float]] = {}
    for wall in walls:
        entry = by_type.setdefault(wall["wall_type"], {"detected": 0, "suggested": 0, "length_m": 0.0})
        entry["detected"] += 1
        entry["suggested"] += int(bool(wall.get("suggested")))
        entry["length_m"] = round(entry["length_m"] + wall["length_mm"] / 1000, 1)
    return {
        "drawing_id": drawing,
        "page_number": page_number,
        "calibrated": calibration.get("status") == "calibrated",
        "storey_height_mm": (placement or {}).get("storey_height_mm"),
        "placement_id": (placement or {}).get("placement_id"),
        "legend": [{"label": label, "swatch_pdf": entry["swatch_pdf"]} for label, entry in signatures.items()],
        "summary": {
            "detected": len(walls),
            "suggested": sum(1 for w in walls if w.get("suggested")),
            "confirmed": sum(1 for w in walls if w["confirmation"]["status"] == "confirmed"),
            "by_type": by_type,
        },
        "candidates": walls,
        "confirmations_on_page": _confirmations_on_page(ledger, drawing, page_number, "hatch_wall"),
        "rules": [
            "Wall types come from this sheet's hatch legend: text containing WALL with a swatch beside it.",
            "A wall is two parallel boundary lines 80-600 mm apart, at least 600 mm long, whose strip is filled "
            "with one legend hatch along most of its length.",
            "Suggested walls lie inside the outline of the page's detected columns (2 m margin).",
            "Confirmed walls run the plan's full storey height (slab to slab).",
        ],
    }


@router.post(WALLS + "/confirm", status_code=201)
def confirm_hatch_walls(project_id: str, payload: HatchWallConfirmRequest):
    safe, folder = _folder(project_id)
    request = payload.model_dump()
    request["drawing_id"] = _drawing(request["drawing_id"])
    with _LEDGER_LOCK:
        walls, _, _ = _page_hatch_walls(folder, request["drawing_id"], request["page_number"])
        ledger = ledger_service.load_ledger(folder, safe)
        try:
            record = ledger_service.build_column_confirmation(
                ledger=ledger, project_id=safe, request=request, page_candidates=walls, element_kind="hatch_wall",
            )
        except ledger_service.VerificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ledger_service.apply_column_confirmation(ledger, record)
        ledger_service.save_ledger(folder, ledger)
    return {"message": "Hatch walls confirmed.", "confirmation": record}


@router.post(BASE + "/confirm", status_code=201)
def confirm_columns(project_id: str, payload: ColumnConfirmRequest):
    safe, folder = _folder(project_id)
    request = payload.model_dump()
    request["drawing_id"] = _drawing(request["drawing_id"])
    with _LEDGER_LOCK:
        candidates, _ = _page_candidates(folder, request["drawing_id"], request["page_number"])
        ledger = ledger_service.load_ledger(folder, safe)
        try:
            record = ledger_service.build_column_confirmation(
                ledger=ledger, project_id=safe, request=request, page_candidates=candidates,
            )
        except ledger_service.VerificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ledger_service.apply_column_confirmation(ledger, record)
        ledger_service.save_ledger(folder, ledger)
    return {"message": "Columns confirmed.", "confirmation": record}


@router.post(WALLS + "/{confirmation_id}/revoke")
@router.post(BASE + "/{confirmation_id}/revoke")
def revoke_columns(project_id: str, confirmation_id: str, payload: RevokeRequest):
    safe, folder = _folder(project_id)
    with _LEDGER_LOCK:
        ledger = ledger_service.load_ledger(folder, safe)
        try:
            record = ledger_service.revoke_column_confirmation(
                ledger, confirmation_id, reviewer=payload.reviewer, reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ledger_service.VerificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ledger_service.save_ledger(folder, ledger)
    return {"message": "Confirmation revoked.", "confirmation": record}
