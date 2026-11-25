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
                try:
                    title = json.loads(path.read_text(encoding="utf-8")).get("drawing_title")
                except (OSError, ValueError):
                    title = None
                excluded.append({"drawing_id": drawing_id, "drawing_title": title,
                                 "reason": "Walls generated, but the plan has no placement (level and reference point)."})

    slabs = approximate_slabs(storeys, elements)

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
        "slabs": slabs,
        "withheld_elements": withheld,
        "excluded_plans": excluded,
        "limitations": [
            "Only reviewer-confirmed walls on reviewer-placed plans are included.",
            "Plans are aligned by one shared reference point each (translation only, no rotation).",
            "Base elevations come from annotated levels chosen by the reviewer.",
            "Slabs are approximate: outlined around each storey's confirmed columns, thickness not "
            "established (the drawings refer to separate structure drawings).",
            "No beams, doors or windows are generated yet.",
        ],
    }


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    points = sorted(set(points))
    if len(points) < 3:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def approximate_slabs(storeys: list[dict[str, Any]], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    LOD 200 slab extents: at each placed storey's base level (SSL = top of slab),
    outlined by the convex hull of that storey's served columns; the highest
    storey also gets one at its top level. Thickness is not established.
    """
    by_storey: dict[str, list[tuple[float, float]]] = {}
    for element in elements:
        if element.get("element_type") != "column":
            continue
        corners = [(v[0], v[1]) for v in element["mesh"]["vertices"]]
        by_storey.setdefault(element["storey"]["placement_id"], []).extend(corners)

    slabs = []
    ordered = sorted(storeys, key=lambda s: s["base_elevation_mm"])
    for position, storey in enumerate(ordered):
        hull = _convex_hull(by_storey.get(storey["placement_id"], []))
        if len(hull) < 3:
            continue
        levels = [("base", storey["base_level_key"], storey["base_elevation_mm"])]
        if position == len(ordered) - 1 and storey.get("top_level_key") and storey.get("storey_height_mm"):
            levels.append(("top", storey["top_level_key"],
                           storey["base_elevation_mm"] + storey["storey_height_mm"]))
        for which, level_key, elevation in levels:
            slabs.append({
                "element_id": f"slab:{storey['placement_id']}:{which}",
                "element_type": "slab_extent",
                "level_key": level_key,
                "elevation_mm": round(float(elevation), 3),
                "outline_mm": [[round(x, 3), round(y, 3)] for x, y in hull],
                "thickness_mm": None,
                "approximate": True,
                "lod": {"category": "slab", "minimum_lod": 200},
                "basis": (
                    f"convex hull of the confirmed columns on {storey.get('drawing_title')}; "
                    f"level {level_key} from the plan placement"
                ),
            })
    return slabs
