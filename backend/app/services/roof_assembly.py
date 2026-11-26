"""
LOD 500 envelope from the drawings: outside walls, the roof and parapets.

Nothing here invents a dimension.

* Exterior or interior: the storey's walls, columns and openings are rasterised.
  Gaps under 2 m are bridged, or under 8 m if the outline still leaks. An element
  whose long side (any side for a column) faces the outside of that outline is
  marked ``is_external``. This only decides the LOD 500 finish (brick outside,
  plaster inside).
* Roof: the highest storey's top slab is the roof. Its level comes from the plan
  placement (for example "ROOF SLAB: SSL +3.300").
* Parapet: built only when a drawing states a parapet height, i.e. a note like
  "PARAPET 1.0M HIGH" with one value. The note must be on the top storey's own
  plan, or name the roof. The parapet goes on the top storey's exterior walls,
  columns and openings, from the roof level up by that height. With no note there
  is no parapet; with notes giving different heights there is none either, and the
  model says why.
"""

from __future__ import annotations

import math
import re
from typing import Any

EXTERIOR_CLOSE_MM = 1000.0       # bridges unconfirmed gaps under 2 m
EXTERIOR_SIDE_SHARE = 0.5        # this share of a side's cells must be outside
PARAPET_MIN_MM = 300.0
PARAPET_MAX_MM = 3000.0
PARAPET_ELEMENT_TYPES = ("wall", "column", "door", "window", "opening")
BAND_TOLERANCE_MM = 10.0
TOUCH_MM = 50.0                  # a column within this of a wall is part of that wall's line
ROOF_WORDS = re.compile(r"\bROOF\b", re.IGNORECASE)

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]


# ---------------------------------------------------------------------------
# Exterior / interior
# ---------------------------------------------------------------------------

def _grid(rects: list[tuple[float, float, float, float]], close_mm: float) -> dict[str, Any] | None:
    from app.services import building_model as bm

    hull = bm._convex_hull([(rc[i], rc[j]) for rc in rects for i, j in ((0, 1), (2, 1), (2, 3), (0, 3))])
    if len(hull) < 3:
        return None
    hull_mm2 = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(hull, hull[1:] + hull[:1]))) / 2
    cell = max(bm.SLAB_MIN_CELL_MM, min(bm.SLAB_CELL_MM, round(math.sqrt(hull_mm2) / 100 / 50) * 50 or bm.SLAB_MIN_CELL_MM))
    r = int(round(close_mm / cell))
    margin = (r + 2) * cell
    x0 = min(rc[0] for rc in rects) - margin
    y0 = min(rc[1] for rc in rects) - margin
    nx = int(math.ceil((max(rc[2] for rc in rects) + margin - x0) / cell))
    ny = int(math.ceil((max(rc[3] for rc in rects) + margin - y0) / cell))
    if nx * ny > bm.SLAB_MAX_CELLS:
        return None
    mask = [[False] * nx for _ in range(ny)]
    for a0, b0, a1, b1 in rects:
        for i in range(max(0, int((b0 - y0) // cell)), min(ny, int(math.ceil((b1 - y0) / cell)))):
            row = mask[i]
            for j in range(max(0, int((a0 - x0) // cell)), min(nx, int(math.ceil((a1 - x0) / cell)))):
                row[j] = True
    outside = bm._outside(bm._erode(bm._dilate(mask, r), r))
    inside_mm2 = sum(1 for row in outside for v in row if not v) * cell * cell
    return {"x0": x0, "y0": y0, "cell": cell, "nx": nx, "ny": ny, "outside": outside,
            "closed": inside_mm2 >= bm.SLAB_MIN_HULL_SHARE * hull_mm2, "close_mm": close_mm}


def _faces_outside(grid: dict[str, Any], rect: tuple[float, float, float, float], column: bool) -> bool:
    x0, y0, cell, nx, ny, outside = (grid[k] for k in ("x0", "y0", "cell", "nx", "ny", "outside"))
    a0, b0, a1, b1 = rect
    j0, j1 = int((a0 - x0) // cell), int(math.ceil((a1 - x0) / cell))
    i0, i1 = int((b0 - y0) // cell), int(math.ceil((b1 - y0) / cell))

    def share(cells: list[tuple[int, int]]) -> float:
        cells = [(i, j) for i, j in cells if 0 <= i < ny and 0 <= j < nx]
        return sum(1 for i, j in cells if outside[i][j]) / len(cells) if cells else 0.0

    along_x = (a1 - a0) >= (b1 - b0)
    sides = []
    if along_x or column:
        sides += [[(i0 - 1, j) for j in range(j0, j1)], [(i1, j) for j in range(j0, j1)]]
    if not along_x or column:
        sides += [[(i, j0 - 1) for i in range(i0, i1)], [(i, j1) for i in range(i0, i1)]]
    return max((share(side) for side in sides), default=0.0) >= EXTERIOR_SIDE_SHARE


def mark_exterior(storeys: list[dict[str, Any]], elements: list[dict[str, Any]]) -> int:
    """Set ``is_external`` on each storey's walls, columns and openings; returns how many face outside."""
    from app.services import building_model as bm

    count = 0
    for storey in storeys:
        members = [e for e in elements if e["storey"]["placement_id"] == storey["placement_id"]
                   and e.get("element_type") in PARAPET_ELEMENT_TYPES and (bm._is_box(e) or e.get("plan_rects"))]
        outline = [r for e in members if not e.get("review_flags") for r in bm.plan_rects(e)]
        if not outline:
            continue
        grid = _grid(outline, EXTERIOR_CLOSE_MM)
        if grid is not None and not grid["closed"]:
            grid = _grid(outline, bm.SLAB_CLOSE_MM)
        if grid is None or not grid["closed"]:
            continue                                      # outline does not close: leave the finish undecided
        for element in members:
            external = not element.get("review_flags") and any(
                _faces_outside(grid, rect, element.get("element_type") == "column") for rect in bm.plan_rects(element))
            element["is_external"] = external
            element["exterior_basis"] = (
                f"faces the outside of {storey.get('drawing_title') or 'the storey'}'s outline "
                f"(gaps under {2 * grid['close_mm'] / 1000:.0f} m bridged)"
                if external else "inside the storey outline")
            count += external
        # A column in a wall line takes that wall's finish; a free-standing one stays bare concrete.
        lines = [e for e in members if e.get("element_type") != "column"]
        for column in (e for e in members if e.get("element_type") == "column"):
            hosts = [w.get("component") if w.get("element_type") == "wall" else w.get("host_component")
                     for w in lines if w.get("is_external") == column.get("is_external")
                     and any(_touching(bm._rect(column), rect) for rect in bm.plan_rects(w))]
            hosts = [h for h in hosts if h]
            column["host_component"] = max(set(hosts), key=hosts.count) if hosts else None
    return count


def _touching(a, b, tolerance: float = TOUCH_MM) -> bool:
    return a[0] <= b[2] + tolerance and b[0] <= a[2] + tolerance and a[1] <= b[3] + tolerance and b[1] <= a[3] + tolerance


# ---------------------------------------------------------------------------
# Parapet height from the drawings' notes
# ---------------------------------------------------------------------------

def parapet_notes_from_report(height_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Each printed parapet height (Stage 5.3.4 wall-type annotations naming PARAPET) with its source text."""
    if not height_report:
        return []
    occurrences = {o.get("evidence_id"): o for o in height_report.get("component_heights", []) if isinstance(o, dict)}
    notes = []
    for candidate in height_report.get("wall_type_height_candidates", []):
        words = [w.upper() for w in candidate.get("component_words") or []]
        height = candidate.get("height_mm")
        if "PARAPET" not in words or not isinstance(height, (int, float)):
            continue
        for evidence_id in candidate.get("source_evidence_ids") or []:
            item = occurrences.get(evidence_id) or {}
            notes.append({"height_mm": float(height), "text": item.get("text"), "drawing_id": item.get("drawing_id"),
                          "drawing_title": item.get("drawing_title"), "candidate_id": candidate.get("candidate_id"),
                          "evidence_id": evidence_id})
    return notes


def parapet_notes_from_texts(texts: list[str], *, drawing_id: str, drawing_title: str) -> list[dict[str, Any]]:
    """Parapet heights printed in plain texts (a DXF's notes)."""
    from app.services.wall_height_evidence_association import parse_component_heights

    notes = []
    for text in texts:
        for height in parse_component_heights(text):
            named = [w.upper() for w in (height.get("components_named") or {}).get("wall", [])]
            values = height.get("values_mm") or []
            if "PARAPET" in named and len(values) == 1 and not height.get("is_range"):
                notes.append({"height_mm": float(values[0]), "text": text, "drawing_id": drawing_id,
                              "drawing_title": drawing_title, "candidate_id": None, "evidence_id": None})
    return notes


def choose_parapet(notes: list[dict[str, Any]], top_storey: dict[str, Any] | None,
                   placed_drawing_ids: set[str]) -> tuple[dict[str, Any] | None, str]:
    """The one parapet height that applies to the roof, or None with the reason."""
    if top_storey is None:
        return None, "No storey in the model."
    usable = [n for n in notes if PARAPET_MIN_MM <= n["height_mm"] <= PARAPET_MAX_MM and (
        n.get("drawing_id") == top_storey.get("drawing_id")
        or (n.get("drawing_id") in placed_drawing_ids and ROOF_WORDS.search(n.get("text") or "")))]
    if not usable:
        return None, "No parapet height is printed on the top storey's plan, so no parapet is built."
    heights = sorted({n["height_mm"] for n in usable})
    if len(heights) > 1:
        shown = ", ".join(f"{h:.0f} mm" for h in heights)
        return None, f"The drawings give different parapet heights ({shown}); no parapet is built until one is chosen."
    first = usable[0]
    return first, f"Parapet {first['height_mm']:.0f} mm high from the note \"{first['text']}\" on {first['drawing_title']}."


# ---------------------------------------------------------------------------
# Parapet geometry
# ---------------------------------------------------------------------------

def _box(rect, z0: float, z1: float) -> list[list[float]]:
    x0, y0, x1, y1 = (round(v, 3) for v in rect)
    z0, z1 = round(z0, 3), round(z1, 3)
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


def _plan_rect(element: dict[str, Any]) -> tuple[float, float, float, float]:
    from app.services import building_model as bm

    joined = element.get("joined_mesh")
    return bm._rect({"mesh": joined}) if joined else bm._rect(element)


def _merge_runs(rects: list[tuple[tuple[float, float, float, float], str]]) -> list[tuple[list[float], list[str]]]:
    """Merge rectangles on the same band (same two faces) whose lengths touch or overlap."""
    groups: dict[tuple, list[tuple[tuple[float, float, float, float], str]]] = {}
    for rect, source in rects:
        along_x = (rect[2] - rect[0]) >= (rect[3] - rect[1])
        key = (along_x, round(rect[1] / BAND_TOLERANCE_MM), round(rect[3] / BAND_TOLERANCE_MM)) if along_x else \
              (along_x, round(rect[0] / BAND_TOLERANCE_MM), round(rect[2] / BAND_TOLERANCE_MM))
        groups.setdefault(key, []).append((rect, source))
    merged = []
    for (along_x, _, _), items in groups.items():
        lo, hi = (0, 2) if along_x else (1, 3)
        items.sort(key=lambda item: item[0][lo])
        current, sources = list(items[0][0]), [items[0][1]]
        for rect, source in items[1:]:
            if rect[lo] <= current[hi] + 1.0:
                current[hi] = max(current[hi], rect[hi])
                for k in ((1, 3) if along_x else (0, 2)):
                    current[k] = min(current[k], rect[k]) if k in (0, 1) else max(current[k], rect[k])
                sources.append(source)
            else:
                merged.append((current, sources))
                current, sources = list(rect), [source]
        merged.append((current, sources))
    # A column or short piece lying inside a longer run (e.g. a corner column) adds nothing.
    def inside(a, b):
        return a is not b and b[0][0] - 1 <= a[0][0] and b[0][1] - 1 <= a[0][1] and a[0][2] <= b[0][2] + 1             and a[0][3] <= b[0][3] + 1
    return [m for m in merged if not any(inside(m, other) for other in merged)]


def build_parapets(storeys: list[dict[str, Any]], elements: list[dict[str, Any]],
                   note: dict[str, Any]) -> list[dict[str, Any]]:
    """Parapet pieces on the top storey's exterior walls, columns and openings, from the roof level up."""
    top = max(storeys, key=lambda s: s["base_elevation_mm"])
    if not top.get("storey_height_mm"):
        return []
    roof_mm = float(top["base_elevation_mm"]) + float(top["storey_height_mm"])
    height = float(note["height_mm"])
    outer = [e for e in elements if e["storey"]["placement_id"] == top["placement_id"] and e.get("is_external")
             and e.get("element_type") in PARAPET_ELEMENT_TYPES]
    lines = [e for e in outer if e.get("element_type") != "column"]
    # Columns carry the parapet only where they sit in an outer wall line, not when free-standing.
    outer = lines + [c for c in outer if c.get("element_type") == "column"
                     and any(_touching(_plan_rect(c), _plan_rect(w)) for w in lines)]
    by_id = {e["element_id"]: e for e in outer}
    pieces = []
    for n, (rect, sources) in enumerate(_merge_runs([(_plan_rect(e), e["element_id"]) for e in outer])):
        finishes = [by_id[i].get("component") for i in sources if by_id[i].get("element_type") == "wall"] or                    [by_id[i].get("host_component") for i in sources if by_id[i].get("host_component")]
        host = max(set(finishes), key=finishes.count) if finishes else None
        length = max(rect[2] - rect[0], rect[3] - rect[1])
        thickness = min(rect[2] - rect[0], rect[3] - rect[1])
        pieces.append({
            "element_id": f"parapet:{top['placement_id']}:{n}",
            "component": "parapet",
            "element_type": "parapet",
            "detected_by": "parapet_note",
            "wall_type": None,
            "dimensions_mm": {"length": round(length, 1), "thickness": round(thickness, 1), "height": height},
            "lod": {"category": "parapet", "minimum_lod": 500},
            "height_status": "drawing_note",
            "height_verification": None,
            "provenance": {
                "height_from_note": {k: note.get(k) for k in ("text", "drawing_id", "drawing_title", "candidate_id",
                                                               "evidence_id")},
                "plan_from": "the top storey's exterior walls, columns and openings",
                "source_element_ids": sources,
                "base_level_key": top.get("top_level_key"),
            },
            "source_note": note.get("text"),
            "opening": None,
            "between": None,
            "host_component": host,
            "drawing_id": top.get("drawing_id"),
            "drawing_title": top.get("drawing_title"),
            "storey": {key: top[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
            "mesh": {"vertices": _box(rect, roof_mm, roof_mm + height), "faces": BOX_FACES},
            "reviewed_by": None,
            "is_external": True,
        })
    return pieces


def finish_envelope(storeys: list[dict[str, Any]], elements: list[dict[str, Any]], slabs: list[dict[str, Any]],
                    parapet_notes: list[dict[str, Any]], placed_drawing_ids: set[str], *,
                    facts: dict[str, Any] | None = None, axes: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Mark exterior elements and add the roof (gable from a roof note, else the flat top slab with any
    noted parapet), the plinth and slab thickness, in place; returns what was done for the summary.
    """
    from app.services import envelope_details

    facts = facts or {}
    exterior = mark_exterior(storeys, elements)
    roof_pieces, roof_reason = (envelope_details.gable_roof(storeys, elements, slabs, facts, axes or {})
                                if storeys else ([], None))
    top = max(storeys, key=lambda s: s["base_elevation_mm"]) if storeys else None
    if roof_pieces:
        parapets, reason = [], "No parapet: the roof is pitched."
    else:
        note, reason = choose_parapet(parapet_notes, top, placed_drawing_ids)
        parapets = build_parapets(storeys, elements, note) if note else []
        if note and not parapets:
            reason = "A parapet height is printed, but the top storey has no height, so the roof level is unknown."
    plinth = envelope_details.plinth(storeys, elements, slabs, facts)
    envelope_details.apply_slab_thickness(slabs, facts)
    elements.extend(parapets + roof_pieces + plinth)
    roof = next((s for s in slabs if s.get("role") == "roof"), None)
    return {
        "exterior_elements": exterior,
        "parapets": len(parapets),
        "roof": "gable" if roof_pieces else (roof["element_id"] if roof else None),
        "roof_form": "gable" if roof_pieces else "flat",
        "roof_note": roof_reason,
        "parapet_note": reason,
        "plinth": len(plinth),
        "slab_thickness_mm": (facts.get("slab_thickness_mm") or {}).get("value_mm"),
        "envelope_notes_read": sorted({t for t in (facts.get("roof") or {}).get("texts", [])}
                                      | {v for v in (facts.get("gutters"), facts.get("downpipes")) if v}),
    }
