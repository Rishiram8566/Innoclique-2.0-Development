"""
Stage 5.4 building model: stack placed plans into one shared frame.

Each plan's Stage 5.4 walls are in page-local millimetres. A reviewer's plan
placement supplies:

* the base level (from the Stage 5.3.4 level register) - lifts the plan to
  its elevation;
* a shared reference point clicked on the plan (e.g. a grid intersection that
  appears on every plan) - becomes the building origin.

Building coordinates (mm): X = x - ref_x, Y = y - ref_y (plan "down"),
Z = z + base elevation. Alignment is translation only: plans are assumed to
share orientation, which is stated in the output.

Walls are re-screened by the Stage 5.3.5 gate, and a plan whose calibration
changed after its walls were generated is excluded until regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services import height_verification_ledger as ledger_service
from app.services import wall_mesh_generation as generation

ELEMENT_FIELDS = (
    "element_id", "component", "element_type", "dimensions_mm", "lod",
    "height_status", "height_verification", "provenance",
)


def _current_scale(calibration_report: dict[str, Any] | None, drawing_id: str, page_number: int) -> float | None:
    record = (calibration_report or {}).get("calibrations", {}).get(drawing_id, {}).get(str(page_number))
    if isinstance(record, dict) and record.get("status") == "calibrated":
        return float(record["millimetres_per_pdf_point"])
    return None


def build_building_model(project_folder: Path, project_id: str) -> dict[str, Any]:
    folder = Path(project_folder)
    ledger = ledger_service.load_ledger(folder, project_id)
    calibration = ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME)
    mesh_dir = folder / "artifacts" / generation.OUTPUT_DIRECTORY

    placements = sorted(
        (p for p in ledger.get("plan_placements", []) if p.get("status") == "active"),
        key=lambda p: float(p["base_level"]["elevation_mm"]),
    )
    placed = {(p["drawing_id"], p["page_number"]) for p in placements}

    elements: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    storeys: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for placement in placements:
        drawing_id, page_number = placement["drawing_id"], placement["page_number"]
        title = placement.get("drawing_title")
        path = mesh_dir / generation.output_artifact_name(drawing_id)
        if not path.is_file():
            excluded.append({"drawing_id": drawing_id, "drawing_title": title,
                             "reason": "Placed, but no Stage 5.4 walls have been generated for this plan yet."})
            continue
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if (
            artifact.get("project_id") != project_id
            or artifact.get("drawing_id") != drawing_id
            or artifact.get("page_number") != page_number
        ):
            excluded.append({"drawing_id": drawing_id, "drawing_title": title,
                             "reason": "Wall artifact does not match this project, drawing and page."})
            continue

        scale = float(artifact["calibration"]["millimetres_per_pdf_point"])
        current = _current_scale(calibration, drawing_id, page_number)
        if current is None or abs(current - scale) > 1e-9:
            excluded.append({"drawing_id": drawing_id, "drawing_title": title,
                             "reason": "Page calibration changed after the walls were generated; regenerate them."})
            continue

        served, held = ledger_service.screen_mesh_elements(artifact.get("elements", []), ledger)
        ref_x, ref_y = placement["reference_point"]["point_pdf"]
        offset_x, offset_y = float(ref_x) * scale, float(ref_y) * scale
        base_mm = float(placement["base_level"]["elevation_mm"])

        storey = {
            "placement_id": placement["placement_id"],
            "drawing_id": drawing_id,
            "drawing_title": title,
            "page_number": page_number,
            "base_level_key": placement["base_level"]["level_key"],
            "base_elevation_mm": base_mm,
            "top_level_key": (placement.get("top_level") or {}).get("level_key"),
            "storey_height_mm": placement.get("storey_height_mm"),
            "reference_label": placement["reference_point"]["label"],
            "walls_served": len(served),
            "walls_withheld": len(held),
        }
        storeys.append(storey)

        for element in served:
            vertices = [
                [round(x - offset_x, 3), round(y - offset_y, 3), round(z + base_mm, 3)]
                for x, y, z in element["mesh"]["vertices"]
            ]
            elements.append({
                **{key: element.get(key) for key in ELEMENT_FIELDS},
                "drawing_id": drawing_id,
                "drawing_title": title,
                "storey": {key: storey[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
                "mesh": {"vertices": vertices, "faces": element["mesh"]["faces"]},
            })
        withheld += [{**item, "drawing_id": drawing_id} for item in held]

    if mesh_dir.is_dir():
        for path in sorted(mesh_dir.glob("*_stage5_4.json")):
            drawing_id = path.name[: -len("_stage5_4.json")]
            if not any(d == drawing_id for d, _ in placed):
                excluded.append({"drawing_id": drawing_id, "drawing_title": None,
                                 "reason": "Walls generated, but the plan has no placement (level and reference point)."})

    return {
        "schema_version": "0.1.0",
        "stage": "5.4",
        "artifact_type": "building_model",
        "project_id": project_id,
        "coordinate_system": {
            "unit": "mm",
            "origin": "the shared reference point chosen on each placed plan",
            "x_axis": "plan right",
            "y_axis": "plan down",
            "z_axis": "up; elevation from the plan's base level",
            "alignment": "translation only; plans are assumed to share orientation",
        },
        "summary": {
            "placed_plans": len(placements),
            "plans_in_model": len(storeys),
            "walls_served": len(elements),
            "walls_withheld": len(withheld),
            "plans_excluded": len(excluded),
        },
        "storeys": storeys,
        "elements": elements,
        "withheld_elements": withheld,
        "excluded_plans": excluded,
        "limitations": [
            "Only reviewer-confirmed walls on reviewer-placed plans are included.",
            "Plans are aligned by one shared reference point each (translation only, no rotation).",
            "Base elevations come from annotated levels chosen by the reviewer.",
            "No slabs, columns, beams, doors or windows are generated yet.",
        ],
    }
