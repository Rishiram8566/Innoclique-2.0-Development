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

Two clean-ups are derived from the confirmed elements, never from guesses:

* a line-pair wall lying on a confirmed legend-hatch wall of the same storey
  is superseded by it (one wall, not two overlapping boxes);
* LOD 400 joins: a wall end that stops short of a perpendicular wall, a
  column or (by at most a drawing gap) a collinear wall is extended to meet
  it, by no more than JOIN_TOLERANCE_MM. The LOD 300 geometry stays as drawn;
  each join is recorded on the element.
"""

from __future__ import annotations

import copy

import bisect
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from app.services import height_verification_ledger as ledger_service
from app.services import roof_assembly
from app.services import wall_mesh_generation as generation

ELEMENT_FIELDS = (
    "element_id", "component", "element_type", "detected_by", "wall_type", "dimensions_mm", "lod",
    "height_status", "height_verification", "provenance", "opening", "between", "host_component",
)
OPENING_TYPES = ("door", "window", "opening")
ROOM_SIZE_SEARCH = 3.0          # a size text sits within this many label heights below the room name
JOIN_TOLERANCE_MM = 500.0
COLLINEAR_GAP_MM = 150.0
SUPERSEDE_OVERLAP = 0.5
ISOLATED_COLUMN_MM = 10000.0
SLAB_CELL_MM = 500.0            # largest raster cell; small buildings use finer cells (down to 100 mm)
SLAB_MIN_CELL_MM = 100.0
SLAB_CLOSE_MM = 4000.0          # openings narrower than 8 m (about one column bay) are bridged
SLAB_MIN_HULL_SHARE = 0.4       # enclosed area must cover this share of the storey's convex hull, else not closed
SLAB_MAX_CELLS = 2_000_000
HEIGHT_REPORT_ARTIFACT = "wall_height_association_stage5_3_4.json"


def _current_scale(calibration_report: dict[str, Any] | None, drawing_id: str, page_number: int) -> float | None:
    record = (calibration_report or {}).get("calibrations", {}).get(drawing_id, {}).get(str(page_number))
    if isinstance(record, dict) and record.get("status") == "calibrated":
        return float(record["millimetres_per_pdf_point"])
    return None


# Built models, kept while the project's files are unchanged: the viewer asks for the model often (and several
# requests at once), and a large project takes seconds to build. One build per project at a time.
MODEL_CACHE_SIZE = 6
_MODEL_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_MODEL_LOCKS: dict[str, "threading.Lock"] = {}
_MODEL_JSON: dict[str, tuple[dict[str, Any], bytes]] = {}
_CACHE_GUARD = None
CACHE_IGNORED_FILES = {"auto_model_status.json"}       # progress notes, rewritten while a run is in progress


def _project_fingerprint(folder: Path) -> str:
    """Every input and artifact file of the project, by path, size and modification time."""
    import hashlib

    digest = hashlib.sha1()
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file() and path.name not in CACHE_IGNORED_FILES:
            stat = path.stat()
            digest.update(f"{path.relative_to(folder)}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def cached_building_model_json(folder: Path, project_id: str) -> bytes:
    """
    The model as JSON bytes, made once per build: converting a large model to JSON on every request (every mesh
    coordinate) took seconds, so the response is sent as these bytes.
    """
    model = cached_building_model(folder, project_id)
    with _CACHE_GUARD:
        hit = _MODEL_JSON.get(project_id)
        if hit is not None and hit[0] is model:
            return hit[1]
    body = json.dumps(model, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    with _CACHE_GUARD:
        _MODEL_JSON[project_id] = (model, body)
        while len(_MODEL_JSON) > MODEL_CACHE_SIZE:
            _MODEL_JSON.pop(next(iter(_MODEL_JSON)))
    return body


def cached_building_model(folder: Path, project_id: str) -> dict[str, Any]:
    """build_building_model, reused while the project's files are unchanged (callers must not modify it)."""
    import threading

    global _CACHE_GUARD
    if _CACHE_GUARD is None:
        _CACHE_GUARD = threading.Lock()
    with _CACHE_GUARD:
        lock = _MODEL_LOCKS.setdefault(project_id, threading.Lock())
    with lock:
        fingerprint = _project_fingerprint(folder)
        hit = _MODEL_CACHE.get(project_id)
        if hit and hit[0] == fingerprint:
            return hit[1]
        model = build_building_model(folder, project_id)
        with _CACHE_GUARD:
            _MODEL_CACHE.pop(project_id, None)
            _MODEL_CACHE[project_id] = (_project_fingerprint(folder), model)
            while len(_MODEL_CACHE) > MODEL_CACHE_SIZE:
                _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)))
        return model


def build_building_model(project_folder: Path, project_id: str) -> dict[str, Any]:
    folder = Path(project_folder)
    from app.services import imported_models

    project_file = folder / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8")) if project_file.is_file() else {}
    from app.services import structure_model

    if structure_model.is_structure(project):
        # Towers, dams and power plants: built from the dimensions printed on their drawings.
        return structure_model.build(folder, project, project_id)
    imported = imported_models.load_imported(folder, project)
    if imported:
        return imported_models.build_from_imported(imported, project, project_id)
    ledger = ledger_service.load_ledger(folder, project_id)
    calibration = ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME)
    mesh_dir = folder / "artifacts" / generation.OUTPUT_DIRECTORY

    placements = sorted(
        (p for p in ledger.get("plan_placements", []) if p.get("status") == "active"),
        key=lambda p: float(p["base_level"]["elevation_mm"]),
    )
    placed = {(p["drawing_id"], p["page_number"]) for p in placements}

    elements: list[dict[str, Any]] = []
    rooms: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    detected_confirmations = ledger_service.active_column_confirmations(ledger)
    storeys: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    pages: dict[str, dict[str, Any]] = {}

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
        pages[storey["placement_id"]] = {"drawing_id": drawing_id, "page_number": page_number, "scale": scale,
                                         "offset": (offset_x, offset_y), "served": served}

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
                "reviewed_by": _reviewer(element, detected_confirmations),
            })
        withheld += [{**item, "drawing_id": drawing_id} for item in held]
        rooms += room_labels(folder, drawing_id, storey, served, scale=scale, offset=(offset_x, offset_y))

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

    elements, superseded = supersede_line_pair_walls(elements)
    for storey in storeys:
        storey["walls_served"] = sum(1 for e in elements if e["storey"]["placement_id"] == storey["placement_id"])
    add_wall_joins(elements)
    isolated = flag_isolated_columns(storeys, elements)
    slabs = approximate_slabs(storeys, elements)
    served_count = len(elements)            # parapets below are derived from these, not served drawing elements
    from app.services import envelope_details, stair_detection
    from app.services.sheet_registration import sheet_index

    index = sheet_index(folder)
    in_model = [s["drawing_id"] for s in sorted(storeys, key=lambda s: s["base_elevation_mm"])]
    envelope_texts = [t for d in in_model for t in (index.get(d) or {}).get("envelope_texts", [])]
    axes = envelope_details.grid_axes(dict((index.get(in_model[-1]) or {}).get("labels") or {})) if in_model else {}
    facts = envelope_details.envelope_facts(envelope_texts)

    # Stairs from the riser lines in each STAIR room, rising to the next storey; the slab above gets an opening.
    stairs, stair_voids, stair_notes = stair_detection.building_stairs(
        folder, storeys, pages, facts.get("slab_thickness_mm"))
    elements.extend(stairs)
    lift_doors = {i for e in stairs for i in ((e.get("lift") or {}).get("door_ids") or [])}
    for element in elements:
        if element["element_id"] in lift_doors:
            element["lift_door"] = True
    for void in stair_voids:
        slab = next((s for s in slabs if s["element_id"] == f"slab:{void['placement_id']}:base"
                     and s.get("footprint")), None)
        if slab:
            slab["footprint"]["rects_mm"] = stair_detection.subtract_rect(slab["footprint"]["rects_mm"], void["rect_mm"])
            slab.setdefault("openings", []).append(void)

    envelope = roof_assembly.finish_envelope(
        storeys, elements, slabs,
        roof_assembly.parapet_notes_from_report(
            ledger_service.read_optional_artifact(folder, HEIGHT_REPORT_ARTIFACT)),
        {d for d, _ in placed},
        facts=facts, axes=axes)

    # LOD 500 furniture in every labelled room whose walls are found (indicative, typical sizes).
    from app.services import furnishing

    line_cache: dict[str, list] = {}

    def drawn_lines(placement_id: str) -> list:
        """The plan's long straight lines (3 m or more) as thin rectangles in building mm."""
        if placement_id not in line_cache:
            from app.services import hatch_wall_detection as hatch

            page = pages.get(placement_id)
            rects = []
            if page:
                sidecar = folder / "artifacts" / "primitive_sidecars" / f"{page['drawing_id']}_page_{page['page_number']}.jsonl"
                if sidecar.is_file():
                    scale, (ox, oy) = page["scale"], page["offset"]
                    for orient, pos, a0, a1 in hatch.axis_lines(sidecar):
                        if abs(a1 - a0) * scale < 3000:
                            continue
                        lo, hi = sorted((a0 * scale, a1 * scale))
                        if orient == "h":
                            rects.append((lo - ox, pos * scale - oy - 25, hi - ox, pos * scale - oy + 25))
                        else:
                            rects.append((pos * scale - ox - 25, lo - oy, pos * scale - ox + 25, hi - oy))
            line_cache[placement_id] = rects
        return line_cache[placement_id]

    furniture, furniture_notes = furnishing.furnish_building(storeys, elements, rooms, drawn_lines)
    furniture_notes += furnishing.open_double_height_rooms(rooms, slabs)
    elements.extend(furniture)

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
            "walls_served": served_count,
            "walls_withheld": len(withheld),
            "walls_superseded": len(superseded),
            "walls_joined": sum(1 for e in elements if e.get("joined_mesh")),
            "automatic_elements": sum(1 for e in elements if (e.get("reviewed_by") or "").startswith("Automatic")),
            "doors": sum(1 for e in elements if e.get("element_type") == "door"),
            "windows": sum(1 for e in elements if e.get("element_type") == "window"),
            "openings": sum(1 for e in elements if e.get("element_type") == "opening"),
            "rooms": len(rooms),
            "columns_to_review": len(isolated),
            "stairs": sum(1 for e in elements if e.get("component") == "stair flight"),
            "lifts": sum(1 for e in elements if e.get("element_type") == "lift shaft"),
            "furniture": len(furniture),
            "exterior_elements": envelope["exterior_elements"],
            "parapets": envelope["parapets"],
            "roof": envelope["roof"],
            "plans_excluded": len(excluded),
        },
        "envelope": envelope,
        "stair_notes": stair_notes,
        "furniture_notes": furniture_notes,
        "storeys": storeys,
        "elements": elements,
        "slabs": slabs,
        "rooms": rooms,
        "withheld_elements": withheld,
        "superseded_elements": superseded,
        "excluded_plans": excluded,
        "limitations": [
            "Only reviewer-confirmed walls on reviewer-placed plans are included.",
            "Plans are aligned by one shared reference point each (translation only, no rotation).",
            "Base elevations come from annotated levels chosen by the reviewer.",
            "Slabs are approximate: the area enclosed by each storey's confirmed walls and columns "
            f"(openings under {2 * SLAB_CLOSE_MM / 1000:.0f} m bridged; the storey below's outline, then the convex "
            "hull, where it does not close; isolated columns left out); thickness not established (the drawings refer to separate "
            "structure drawings).",
            f"LOD 400 joins extend wall ends by at most {JOIN_TOLERANCE_MM:.0f} mm to meet a wall or column; "
            "LOD 300 shows the walls exactly as drawn.",
            "Doors and windows are the gaps between confirmed walls, typed by the drawing's symbol; their "
            "heights come from a schedule or note on the drawing, otherwise typical values marked assumed.",
            "Room names and sizes are the drawing's own room labels.",
            "No beams are generated (they are not on the architectural drawings).",
            "Stairs are read from the riser lines in rooms labelled STAIR: rise = storey height / risers, going and "
            "width as drawn, direction from the UP text; the floor slab above gets an opening over the stair.",
            "LOD 500 roof: " + (envelope["roof_note"] if envelope["roof_form"] == "gable" else
                                "the top storey's top slab at its placed level. " + envelope["parapet_note"]
                                + (f" ({envelope['roof_note']})" if envelope.get("roof_note") else "")),
            "Exterior or interior (brick or plaster at LOD 500) follows which walls face the outside of each "
            "storey's outline; it is a finish shown for presentation, not a material read from the drawings.",
        ],
    }


def _reviewer(element: dict[str, Any], detected_confirmations: dict[str, dict[str, Any]]) -> str | None:
    """Who confirmed this element: a person's name, or the automatic pipeline."""
    record = detected_confirmations.get(str(element.get("element_id")))
    if record:
        return record.get("reviewer")
    return (element.get("height_verification") or {}).get("reviewer")


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
    LOD 200 slab extents at each placed storey's base level (SSL = top of
    slab); the highest storey also gets one at its top level. The extent is
    the area enclosed by the storey's walls and columns (slab_footprint), or
    the convex hull of its columns and hatch walls where that outline does not
    close. Isolated columns are left out. Thickness is not established.
    """
    by_storey: dict[str, list[tuple[float, float]]] = {}
    rects_by_storey: dict[str, list[tuple[float, float, float, float]]] = {}
    walls_by_storey: dict[str, list[tuple[float, float, float, float]]] = {}
    left_out: dict[str, int] = {}
    for storey in storeys:
        placement_id = storey["placement_id"]
        members = [e for e in elements if e["storey"]["placement_id"] == placement_id and _is_box(e)
                   and not e.get("review_flags")]
        left_out[placement_id] = sum(1 for e in elements if e["storey"]["placement_id"] == placement_id
                                     and e.get("review_flags"))
        slanted = [r for e in elements if e["storey"]["placement_id"] == placement_id and e.get("plan_rects")
                   and not e.get("review_flags") for r in plan_rects(e)]
        rects_by_storey[placement_id] = [_rect(e) for e in members] + slanted
        walls_by_storey[placement_id] = [_rect(e) for e in members if e.get("element_type") == "wall"] + slanted
        for element in members:
            if element.get("element_type") == "column" or element.get("detected_by") == "legend_hatch":
                by_storey.setdefault(placement_id, []).extend((v[0], v[1]) for v in element["mesh"]["vertices"])

    slabs = []
    ordered = sorted(storeys, key=lambda s: s["base_elevation_mm"])
    for position, storey in enumerate(ordered):
        hull = _convex_hull(by_storey.get(storey["placement_id"], []))
        if len(hull) < 3:
            continue
        footprint = slab_footprint(rects_by_storey.get(storey["placement_id"], []),
                                   walls_by_storey.get(storey["placement_id"]))
        if footprint is None and position > 0:
            # The slab at this level is also the ceiling of the storey below: use that storey's outline.
            below = slab_footprint(rects_by_storey.get(ordered[position - 1]["placement_id"], []),
                                   walls_by_storey.get(ordered[position - 1]["placement_id"]))
            if below:
                footprint = {**below, "method": "enclosed_by_walls_of_storey_below",
                             "from_storey": ordered[position - 1].get("drawing_title")}
        own = footprint
        if footprint is not None and position > 0 and footprint.get("method") == "enclosed_by_walls_and_columns":
            below = slab_footprint(rects_by_storey.get(ordered[position - 1]["placement_id"], []),
                                   walls_by_storey.get(ordered[position - 1]["placement_id"]))
            if below:
                extra = covered_beyond(below["rects_mm"], footprint["rects_mm"])
                if extra:
                    footprint = {**footprint, "rects_mm": footprint["rects_mm"] + extra,
                                 "area_m2": round(footprint["area_m2"] + sum((r[2] - r[0]) * (r[3] - r[1]) for r in extra) / 1e6, 1),
                                 "ceiling_of_storey_below": ordered[position - 1].get("drawing_title"),
                                 "ceiling_area_m2": round(sum((r[2] - r[0]) * (r[3] - r[1]) for r in extra) / 1e6, 1)}
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
                "outline_method": footprint["method"] if footprint else "convex_hull",
                # Own copy: an opening in this floor is not one in the roof. The roof covers this storey only;
                # the parts of the storey below that reach beyond it are roofed by this storey's floor slab.
                "footprint": copy.deepcopy(own if which == "top" else footprint),
                "thickness_mm": None,
                "approximate": True,
                "role": "roof" if which == "top" else "floor",
                "lod": {"category": "slab", "minimum_lod": 200},
                "basis": (
                    ({"enclosed_by_walls_and_columns": "area enclosed by the confirmed walls and columns",
                      "enclosed_by_walls_of_storey_below": "area enclosed by the walls of the storey below"}
                     .get((footprint or {}).get("method"), "convex hull of the confirmed columns and hatch walls"))
                    + f" on {storey.get('drawing_title')}"
                    + (f" ({left_out[storey['placement_id']]} isolated column(s) left out)"
                       if left_out.get(storey["placement_id"]) else "")
                    + f"; level {level_key} from the plan placement"
                ),
            })
    return slabs


# ---------------------------------------------------------------------------
# Plan rectangles (all Stage 5.4 walls and columns are axis-aligned boxes)
# ---------------------------------------------------------------------------

def plan_rects(element: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    """
    Axis-aligned rectangles of an element's footprint: its box, or the strip of small rectangles a slanted or
    curved wall carries (its bounding box would cover the rooms beside it).
    """
    if element.get("plan_rects"):
        return [tuple(r) for r in element["plan_rects"]]
    return [_rect(element)]


def _rect(element: dict[str, Any]) -> tuple[float, float, float, float]:
    xs = [v[0] for v in element["mesh"]["vertices"]]
    ys = [v[1] for v in element["mesh"]["vertices"]]
    return min(xs), min(ys), max(xs), max(ys)


def _is_box(element: dict[str, Any]) -> bool:
    vertices = element["mesh"]["vertices"]
    if len(vertices) != 8:
        return False
    x0, y0, x1, y1 = _rect(element)
    return all(abs(v[0] - x0) < 1e-6 or abs(v[0] - x1) < 1e-6 for v in vertices) and all(
        abs(v[1] - y0) < 1e-6 or abs(v[1] - y1) < 1e-6 for v in vertices)


def _box_vertices(rect, z0: float, z1: float) -> list[list[float]]:
    x0, y0, x1, y1 = (round(v, 3) for v in rect)
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


def _overlap_area(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def supersede_line_pair_walls(elements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop line-pair walls that lie on a confirmed hatch wall of the same storey (one wall, not two)."""
    hatch = [e for e in elements if e.get("detected_by") == "legend_hatch"]
    kept, superseded = [], []
    for element in elements:
        if element.get("element_type") == "wall" and element.get("detected_by") != "legend_hatch":
            rect = _rect(element)
            area = (rect[2] - rect[0]) * (rect[3] - rect[1]) or 1.0
            share, by = max(
                ((_overlap_area(rect, _rect(h)) / area, h) for h in hatch
                 if h["storey"]["placement_id"] == element["storey"]["placement_id"]),
                default=(0.0, None), key=lambda item: item[0],
            )
            if share >= SUPERSEDE_OVERLAP:
                superseded.append({
                    "element_id": element["element_id"], "drawing_id": element["drawing_id"],
                    "superseded_by": by["element_id"],
                    "reason": f"{round(share * 100)}% of this line-pair wall lies on a confirmed hatch wall.",
                })
                continue
        kept.append(element)
    return kept, superseded


def _join_end(rect, axis: int, direction: int, targets) -> tuple[float, dict[str, Any]] | None:
    """New end coordinate for one wall end, or None. axis 0 = wall runs along x, 1 = along y."""
    cross = 1 - axis
    end = rect[axis + 2] if direction > 0 else rect[axis]
    c0, c1 = rect[cross], rect[cross + 2]
    centre = (c0 + c1) / 2
    best = None
    for target_id, kind, target in targets:
        t0, t1 = target[axis], target[axis + 2]           # target extent along this wall's axis
        tc0, tc1 = target[cross], target[cross + 2]       # target extent across it
        near, far = (t0, t1) if direction > 0 else (t1, t0)
        gap = (near - end) * direction
        if gap > JOIN_TOLERANCE_MM or (far - end) * direction <= 0:
            continue                                      # too far away, or already behind this end
        if kind == "column":
            if not (tc0 <= centre <= tc1) or gap <= 0:
                continue
            new_end, how = near, "to column face"
        else:
            target_axis = 0 if (target[2] - target[0]) >= (target[3] - target[1]) else 1
            if target_axis == axis:                        # collinear wall: bridge only a drawing gap
                if not (tc0 < c1 and c0 < tc1) or not (0 < gap <= COLLINEAR_GAP_MM):
                    continue
                new_end, how = near, "collinear gap closed"
            else:
                reach = (c1 - c0) + 50.0                   # a corner partner ends within one wall thickness
                if tc1 < c0 - reach or tc0 > c1 + reach:
                    continue
                if tc0 <= c0 + 1 and tc1 >= c1 - 1:       # target passes this wall: tee junction
                    if gap <= 0:
                        continue
                    new_end, how = near, "tee into wall face"
                else:                                      # target ends here too: corner
                    new_end, how = far, "corner to far face"
        extension = (new_end - end) * direction
        if extension <= 0.5 or extension > JOIN_TOLERANCE_MM + (target[axis + 2] - target[axis]):
            continue
        if best is None or extension < best[1]:
            best = (new_end, extension, {"to": target_id, "join": how, "extended_mm": round(extension, 1)})
    return (best[0], best[2]) if best else None


def add_wall_joins(elements: list[dict[str, Any]]) -> None:
    """LOD 400 geometry: extend wall ends to meet neighbouring walls and columns of the same storey."""
    by_storey: dict[str, list[dict[str, Any]]] = {}
    for element in elements:
        by_storey.setdefault(element["storey"]["placement_id"], []).append(element)
    for group in by_storey.values():
        boxes = [e for e in group if _is_box(e) and e.get("element_type") not in OPENING_TYPES]
        targets = [(e["element_id"], "column" if e.get("element_type") == "column" else "wall", _rect(e))
                   for e in boxes]
        for element in boxes:
            if element.get("element_type") != "wall":
                continue
            rect = list(_rect(element))
            axis = 0 if (rect[2] - rect[0]) >= (rect[3] - rect[1]) else 1
            others = [t for t in targets if t[0] != element["element_id"]]
            joins = []
            for direction, which in ((-1, "start"), (1, "end")):
                found = _join_end(tuple(rect), axis, direction, others)
                if found:
                    rect[axis + 2 if direction > 0 else axis] = found[0]
                    joins.append({"end": which, **found[1]})
            if joins:
                zs = [v[2] for v in element["mesh"]["vertices"]]
                element["joined_mesh"] = {"vertices": _box_vertices(rect, min(zs), max(zs)),
                                          "faces": element["mesh"]["faces"]}
                element["joins"] = joins


def _isolated_columns(structural: list[dict[str, Any]]) -> set[str]:
    """Columns far from every other column and wall of the storey (e.g. a detached plinth)."""
    columns = [e for e in structural if e.get("element_type") == "column"]
    if len(columns) < 4:
        return set()
    rects = {e["element_id"]: _rect(e) for e in structural}

    def nearest(element_id):
        r0 = rects[element_id]
        return min(
            (math.hypot(max(r[0] - r0[2], r0[0] - r[2], 0), max(r[1] - r0[3], r0[1] - r[3], 0))
             for other, r in rects.items() if other != element_id),
            default=math.inf,
        )

    distances = {c["element_id"]: nearest(c["element_id"]) for c in columns}
    typical = median(distances.values())
    return {i for i, d in distances.items() if d > max(ISOLATED_COLUMN_MM, 3 * typical)}


def flag_isolated_columns(storeys: list[dict[str, Any]], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark columns far from every other column and wall of their storey for review (kept, not hidden)."""
    flagged = []
    for storey in storeys:
        structural = [e for e in elements if e["storey"]["placement_id"] == storey["placement_id"]
                      and (e.get("element_type") == "column" or e.get("detected_by") == "legend_hatch")]
        isolated = _isolated_columns(structural)
        for element in structural:
            if element["element_id"] in isolated:
                element["review_flags"] = [
                    f"Isolated: no other column or wall within {ISOLATED_COLUMN_MM / 1000:.0f} m on this storey - "
                    "check it is a structural column before relying on it."
                ]
                flagged.append({"element_id": element["element_id"], "drawing_id": element["drawing_id"],
                                "storey": storey["base_level_key"]})
    return flagged


# ---------------------------------------------------------------------------
# Slab footprints enclosed by the confirmed walls and columns
# ---------------------------------------------------------------------------

def _dilate(mask: list[list[bool]], r: int) -> list[list[bool]]:
    """Square (Chebyshev) dilation by r cells, separable: rows then columns."""
    if r <= 0:
        return [row[:] for row in mask]

    def line(values: list[bool]) -> list[bool]:
        prefix = [0]
        for v in values:
            prefix.append(prefix[-1] + (1 if v else 0))
        n = len(values)
        return [prefix[min(n, j + r + 1)] - prefix[max(0, j - r)] > 0 for j in range(n)]

    rows = [line(row) for row in mask]
    cols = [line(list(col)) for col in zip(*rows)]
    return [list(row) for row in zip(*cols)]


def _erode(mask: list[list[bool]], r: int) -> list[list[bool]]:
    inverted = [[not v for v in row] for row in mask]
    return [[not v for v in row] for row in _dilate(inverted, r)]


def _outside(mask: list[list[bool]]) -> list[list[bool]]:
    """Cells reachable from the grid border without crossing the mask."""
    ny, nx = len(mask), len(mask[0])
    seen = [[False] * nx for _ in range(ny)]
    stack = [(i, j) for i in range(ny) for j in (0, nx - 1)] + [(i, j) for j in range(nx) for i in (0, ny - 1)]
    while stack:
        i, j = stack.pop()
        if 0 <= i < ny and 0 <= j < nx and not seen[i][j] and not mask[i][j]:
            seen[i][j] = True
            stack.extend(((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)))
    return seen


def _clip_half_plane(poly: list[tuple[float, float]], a: tuple[float, float], n: tuple[float, float]):
    """The part of a convex polygon on the side of the line through a where (p - a) . n >= 0."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        dp = (p[0] - a[0]) * n[0] + (p[1] - a[1]) * n[1]
        dq = (q[0] - a[0]) * n[0] + (q[1] - a[1]) * n[1]
        if dp >= 0:
            out.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp / (dp - dq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def trim_slabs_to_slanted_walls(slabs: list[dict[str, Any]], elements: list[dict[str, Any]],
                                storeys: list[dict[str, Any]]) -> None:
    """
    A slab outline is built from square cells, so along a slanted wall it steps out past the wall's outer face.
    Cells there are cut along that face; the footprint gains "polygons_mm" (one per cell, cut or not) for drawing.
    """
    ordered = sorted(storeys, key=lambda s: s["base_elevation_mm"])
    below = {s["placement_id"]: ordered[i - 1]["placement_id"] for i, s in enumerate(ordered) if i > 0}
    for slab in slabs:
        footprint = slab.get("footprint")
        if not footprint or not footprint.get("rects_mm"):
            continue
        pid = slab["element_id"][len("slab:"):].rsplit(":", 1)[0]
        own = {pid, below.get(pid)}
        walls = [e for e in elements if e.get("plan_rects") and e["storey"]["placement_id"] in own]
        if not walls:
            continue
        rects = footprint["rects_mm"]
        cx = sum((r[0] + r[2]) / 2 * (r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        cy = sum((r[1] + r[3]) / 2 * (r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects) or 1.0
        centre = (cx / area, cy / area)
        faces = []
        for wall in walls:
            vs = wall["mesh"]["vertices"]
            ring = [(v[0], v[1]) for v in vs[: len(vs) // 2]]
            if len(ring) != 4:
                continue
            edges = [(ring[i], ring[(i + 1) % 4]) for i in range(4)]
            long_edges = sorted(edges, key=lambda e: -((e[1][0] - e[0][0]) ** 2 + (e[1][1] - e[0][1]) ** 2))[:2]
            # The outer face is the long edge farther from the slab's centre; keep the side the centre is on.
            a, b = max(long_edges, key=lambda e: ((e[0][0] + e[1][0]) / 2 - centre[0]) ** 2
                       + ((e[0][1] + e[1][1]) / 2 - centre[1]) ** 2)
            length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            if length < 1.0:
                continue
            d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
            n = (-d[1], d[0])
            if (centre[0] - a[0]) * n[0] + (centre[1] - a[1]) * n[1] < 0:
                n = (-n[0], -n[1])
            faces.append((a, d, n, length))
        if not faces:
            continue
        polygons, trimmed = [], 0
        for r in rects:
            poly = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
            mx, my = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            for a, d, n, length in faces:
                along = (mx - a[0]) * d[0] + (my - a[1]) * d[1]
                across = (mx - a[0]) * n[0] + (my - a[1]) * n[1]
                if -1500.0 <= along <= length + 1500.0 and across < 2000.0:
                    clipped = _clip_half_plane(poly, a, n)
                    if len(clipped) != len(poly) or clipped != poly:
                        trimmed += 1
                    poly = clipped
                    if len(poly) < 3:
                        break
            if len(poly) >= 3:
                polygons.append([[round(x, 1), round(y, 1)] for x, y in poly])
        if trimmed:
            footprint["polygons_mm"] = polygons
            footprint["trimmed_to_slanted_walls"] = trimmed


def covered_beyond(below: list[list[float]], here: list[list[float]], min_side: float = 300.0) -> list[list[float]]:
    """The parts of the storey below's outline that this storey's outline does not cover (plan mm)."""
    from app.services.stair_detection import subtract_rect

    rest = [list(r) for r in below]
    for hole in here:
        rest = subtract_rect(rest, hole)
    return [[round(v, 1) for v in r] for r in rest if r[2] - r[0] >= min_side and r[3] - r[1] >= min_side]


def drop_columns_in_soil(storeys: list[dict[str, Any]], elements: list[dict[str, Any]],
                         reach_mm: float = 500.0) -> list[str]:
    """
    Below ground (a storey whose slab level is under the 0.00 datum), a column standing outside the area its
    walls enclose would be in the soil: a plan drawn with the structural grid shows the columns of the floor
    above there. Those are left out of the basement (run before the slabs are outlined); returns notes.
    Only for CAD plans, whose walls are complete: walls read from a PDF can miss a stretch (a slanted
    retaining wall), and the columns behind it are real.
    """
    notes = []
    for storey in storeys:
        if float(storey["base_elevation_mm"]) >= 0:
            continue
        walls = [r for e in elements if e["storey"]["placement_id"] == storey["placement_id"]
                 and e.get("element_type") == "wall" and (_is_box(e) or e.get("plan_rects")) for r in plan_rects(e)]
        enclosed = slab_footprint(walls, walls)
        if enclosed is None:
            continue
        rects = enclosed["rects_mm"]
        drop = []
        for element in elements:
            if element.get("element_type") != "column" or element["storey"]["placement_id"] != storey["placement_id"]:
                continue
            x0, y0, x1, y1 = _rect(element)
            if not any(x0 - reach_mm < r[2] and r[0] < x1 + reach_mm and y0 - reach_mm < r[3] and r[1] < y1 + reach_mm
                       for r in rects):
                drop.append(element["element_id"])
        if drop:
            gone = set(drop)
            elements[:] = [e for e in elements if e["element_id"] not in gone]
            notes.append(f"{storey.get('drawing_title')}: {len(drop)} column(s) drawn outside the basement's walls "
                         "(in the soil) left out; they belong to the floor above.")
    return notes


def slab_footprint(rects: list[tuple[float, float, float, float]],
                   wall_rects: list[tuple[float, float, float, float]] | None = None) -> dict[str, Any] | None:
    """
    Region enclosed by a storey's walls and columns (plan mm), or None.

    Walls and columns are rasterised on SLAB_CELL_MM cells; a morphological
    closing of SLAB_CLOSE_MM bridges openings (doors, windows, column bays)
    narrower than twice that; everything not reachable from outside is the
    slab. If the outline does not close (the enclosed area covers less than
    SLAB_MIN_HULL_SHARE of the walls' and columns' convex hull), None is returned.
    """
    if not rects:
        return None
    hull = _convex_hull([(rc[i], rc[j]) for rc in rects for i, j in ((0, 1), (2, 1), (2, 3), (0, 3))])
    hull_mm2 = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(hull, hull[1:] + hull[:1]))) / 2
    # About 100 cells across the building: 500 mm for large floors, finer for a house.
    cell = max(SLAB_MIN_CELL_MM, min(SLAB_CELL_MM, round(math.sqrt(hull_mm2) / 100 / 50) * 50 or SLAB_MIN_CELL_MM))
    r = int(round(SLAB_CLOSE_MM / cell))
    margin = (r + 2) * cell
    x0 = min(rc[0] for rc in rects) - margin
    y0 = min(rc[1] for rc in rects) - margin
    nx = int(math.ceil((max(rc[2] for rc in rects) + margin - x0) / cell))
    ny = int(math.ceil((max(rc[3] for rc in rects) + margin - y0) / cell))
    if nx * ny > SLAB_MAX_CELLS:
        return None
    mask = [[False] * nx for _ in range(ny)]
    for a0, b0, a1, b1 in rects:
        for i in range(max(0, int((b0 - y0) // cell)), min(ny, int(math.ceil((b1 - y0) / cell)))):
            row = mask[i]
            for j in range(max(0, int((a0 - x0) // cell)), min(nx, int(math.ceil((a1 - x0) / cell)))):
                row[j] = True
    closed = _erode(_dilate(mask, r), r)
    outside = _outside(closed)
    interior = [[not v for v in row] for row in outside]
    inside_cells = sum(map(sum, interior))
    if inside_cells * cell * cell < SLAB_MIN_HULL_SHARE * hull_mm2:
        return None     # the outline leaks: only a corner or the walls themselves are enclosed

    # Rectangles: horizontal runs per row, merged downward while identical.
    open_runs: dict[tuple[int, int], list[int]] = {}
    rects_mm: list[list[float]] = []
    for i in range(ny + 1):
        runs = set()
        if i < ny:
            j = 0
            while j < nx:
                if interior[i][j]:
                    k = j
                    while k < nx and interior[i][k]:
                        k += 1
                    runs.add((j, k))
                    j = k
                else:
                    j += 1
        for run in list(open_runs):
            if run not in runs:
                start = open_runs.pop(run)
                rects_mm.append([x0 + run[0] * cell, y0 + start[0] * cell, x0 + run[1] * cell, y0 + i * cell])
        for run in runs:
            open_runs.setdefault(run, [i])

    # Outline edges: cell sides between interior and exterior, merged along each line.
    def inside(i, j):
        return 0 <= i < ny and 0 <= j < nx and interior[i][j]

    edges: list[list[float]] = []
    for i in range(ny + 1):                      # horizontal edges on grid line y = i
        j = 0
        while j < nx:
            if inside(i - 1, j) != inside(i, j):
                k = j
                while k < nx and inside(i - 1, k) != inside(i, k):
                    k += 1
                edges.append([x0 + j * cell, y0 + i * cell, x0 + k * cell, y0 + i * cell])
                j = k
            else:
                j += 1
    for j in range(nx + 1):                      # vertical edges on grid line x = j
        i = 0
        while i < ny:
            if inside(i, j - 1) != inside(i, j):
                k = i
                while k < ny and inside(k, j - 1) != inside(k, j):
                    k += 1
                edges.append([x0 + j * cell, y0 + i * cell, x0 + j * cell, y0 + k * cell])
                i = k
            else:
                i += 1
    # The raster is coarser than the walls: move each outline coordinate onto the wall or column face it
    # came from (within one cell), so the slab ends at the outer face instead of up to a cell beyond it.
    # Wall faces win over column faces, so a column standing proud of the wall does not widen the slab.
    def faces(source, lo, hi):
        return sorted({v for rc in source for v in (rc[lo], rc[hi])})

    x_walls, y_walls = faces(wall_rects or [], 0, 2), faces(wall_rects or [], 1, 3)
    x_all, y_all = faces(rects, 0, 2), faces(rects, 1, 3)

    def nearest(value: float, faces_: list[float], reach: float) -> float | None:
        k = bisect.bisect_left(faces_, value)
        near = [f for f in faces_[max(0, k - 1):k + 1] if abs(f - value) <= reach]
        return min(near, key=lambda f: abs(f - value)) if near else None

    def sx(value: float) -> float:
        found = nearest(value, x_walls, 2 * cell)        # the raster can overshoot by a cell plus a column
        return found if found is not None else (nearest(value, x_all, cell) or value)

    def sy(value: float) -> float:
        found = nearest(value, y_walls, 2 * cell)
        return found if found is not None else (nearest(value, y_all, cell) or value)

    rects_mm = [[sx(a), sy(b), sx(c), sy(d)] for a, b, c, d in rects_mm]
    rects_mm = [rc for rc in rects_mm if rc[2] > rc[0] and rc[3] > rc[1]]
    edges = [[sx(a), sy(b), sx(c), sy(d)] for a, b, c, d in edges]
    return {
        "method": "enclosed_by_walls_and_columns",
        "cell_mm": cell,
        "closing_mm": SLAB_CLOSE_MM,
        "area_m2": round(inside_cells * cell * cell / 1e6, 1),
        "rects_mm": [[round(v, 1) for v in rc] for rc in rects_mm],
        "edges_mm": [[round(v, 1) for v in e] for e in edges],
    }


# ---------------------------------------------------------------------------
# Room labels from the drawing's text
# ---------------------------------------------------------------------------

def room_labels(folder: Path, drawing_id: str, storey: dict[str, Any], served: list[dict[str, Any]], *,
                scale: float, offset: tuple[float, float]) -> list[dict[str, Any]]:
    """Room names printed inside the storey's walls, each with the size printed under it (if any)."""
    from app.services.sheet_registration import sheet_index

    entry = sheet_index(folder).get(drawing_id) or {}
    boxes = [e for e in served if e.get("element_type") in ("wall", "column")]
    if not boxes:
        return []
    page_rects = [tuple(v / scale for v in _rect(e)) for e in boxes]
    x_min, y_min = min(r[0] for r in page_rects), min(r[1] for r in page_rects)
    x_max, y_max = max(r[2] for r in page_rects), max(r[3] for r in page_rects)
    sizes = entry.get("sizes") or []
    rooms = []
    for name, box in entry.get("rooms") or []:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if not (x_min < cx < x_max and y_min < cy < y_max):
            continue                                   # outside the building (e.g. ENTRANCE, title block)
        height = max(box[3] - box[1], 1.0)
        size = None
        for (w, d), sbox in sizes:
            below = sbox[1] - box[3]
            if -0.5 * height <= below <= ROOM_SIZE_SEARCH * height and abs(sbox[0] - box[0]) <= 3 * height:
                size = [w, d]
                break
        rooms.append({
            "name": name,
            "size_mm": size,
            "position_mm": [round(cx * scale - offset[0], 1), round(cy * scale - offset[1], 1),
                            float(storey["base_elevation_mm"])],
            "storey": {"placement_id": storey["placement_id"], "base_level_key": storey["base_level_key"]},
            "drawing_id": drawing_id,
            "source": "room label printed on the drawing" + (" with its size" if size else ""),
        })
    return rooms
