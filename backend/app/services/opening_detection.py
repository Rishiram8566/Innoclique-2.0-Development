"""
Doors, windows and plain openings, from the gaps in confirmed walls.

An opening is a gap between two collinear pieces of wall (or a wall and a
column on the same line) that is 450-4000 mm wide, with no column or other
wall inside it. The drawing's own symbols say what it is:

* door   - a swing arc about the opening's width beside it, or a leaf line of
           that length standing at one jamb;
* window - at least two lines along the wall inside the gap (the glazing);
* opening - neither: a passage without a door.

Width and position come from the walls. Heights come from the drawing when it
prints them: a door/window schedule row for the nearest tag ("W1 1500 X 1200
SILL 900"), else a sill or head note; otherwise typical values are used and
the element says so (height_source "assumed_typical").
"""

from __future__ import annotations

import math
from typing import Any

MIN_OPENING_MM = 450.0
MAX_OPENING_MM = 4000.0
TAG_SEARCH_MM = 1500.0
ASSUMED = {"door_head_mm": 2100.0, "window_sill_mm": 900.0, "window_head_mm": 2100.0, "opening_head_mm": 2100.0}
OPENING_MIN_LOD = 300
BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]


def _segments(walls, columns):
    """Group wall and column rectangles into lines: {(axis, key): [(a0, a1, c0, c1, kind, item)]}."""
    lines: list[dict[str, Any]] = []
    for wall in walls:
        x0, y0, x1, y1 = wall["bbox"]
        axis = "h" if (x1 - x0) >= (y1 - y0) else "v"
        a0, a1, c0, c1 = (x0, x1, y0, y1) if axis == "h" else (y0, y1, x0, x1)
        centre, thickness = (c0 + c1) / 2, c1 - c0
        for line in lines:
            if line["axis"] == axis and abs(line["centre"] - centre) <= 0.5 * max(line["thickness"], thickness) \
                    and abs(line["thickness"] - thickness) <= 0.5 * max(line["thickness"], thickness):
                line["items"].append((a0, a1, c0, c1, "wall", wall))
                break
        else:
            lines.append({"axis": axis, "centre": centre, "thickness": thickness,
                          "items": [(a0, a1, c0, c1, "wall", wall)]})
    for line in lines:
        for column in columns:
            x0, y0, x1, y1 = column["bbox"]
            a0, a1, c0, c1 = (x0, x1, y0, y1) if line["axis"] == "h" else (y0, y1, x0, x1)
            if c0 <= line["centre"] <= c1:
                line["items"].append((a0, a1, c0, c1, "column", column))
        line["items"].sort(key=lambda item: (item[0], item[1]))
    return lines


def _overlaps(r, s, shrink=0.2):
    return r[0] + shrink < s[2] and s[0] + shrink < r[2] and r[1] + shrink < s[3] and s[1] + shrink < r[3]


def _blocks(gap, bbox, axis) -> bool:
    """An obstacle fills a gap when it overlaps it across (almost) the whole wall thickness."""
    if not _overlaps(gap, bbox):
        return False
    lo, hi = (1, 3) if axis == "h" else (0, 2)            # the cross direction of the wall
    band = gap[hi] - gap[lo]
    covered = min(gap[hi], bbox[hi]) - max(gap[lo], bbox[lo])
    return band <= 0 or covered >= 0.8 * band


def _rect(axis, a0, a1, c0, c1):
    return (a0, c0, a1, c1) if axis == "h" else (c0, a0, c1, a1)


def classify(scan, axis: str, a0: float, a1: float, c0: float, c1: float) -> tuple[str, list[str]]:
    """'door', 'window' or 'opening' from the drawing's symbols in and beside the gap (PDF points)."""
    width = a1 - a0
    thickness = c1 - c0
    evidence = []
    # Door swing: an arc whose bounding box is about width x width, next to the gap.
    for x0, y0, x1, y1 in scan.curves:
        along = (x0, x1) if axis == "h" else (y0, y1)
        across = (y0, y1) if axis == "h" else (x0, x1)
        size_a, size_c = along[1] - along[0], across[1] - across[0]
        # A single leaf swings about the opening's width; each leaf of a double door about half of it.
        # A swing is a quarter circle: a near-square outline with one corner at a jamb, against the wall face.
        tolerance = 0.15 * width + 0.5
        square = max(size_a, size_c) <= 1.3 * min(size_a, size_c) if min(size_a, size_c) > 0 else False
        at_jamb = min(abs(along[0] - a0), abs(along[1] - a1)) <= tolerance
        on_face = min(abs(across[0] - c1), abs(across[1] - c0)) <= tolerance
        if (0.4 * width <= size_a <= 1.3 * width and 0.4 * width <= size_c <= 1.3 * width
                and square and at_jamb and on_face):
            evidence.append("swing arc")
            break
    # Door leaf: a line about the opening's width, perpendicular to the wall, standing at a jamb.
    perpendicular = "v" if axis == "h" else "h"
    for orient, pos, b0, b1 in scan.axis:
        if orient != perpendicular:
            continue
        length = b1 - b0
        at_jamb = min(abs(pos - a0), abs(pos - a1)) <= 0.15 * width + 0.5
        touches_wall = (c0 - 0.5 <= b0 <= c1 + 0.5) or (c0 - 0.5 <= b1 <= c1 + 0.5)
        if (0.7 * width <= length <= 1.3 * width or 0.4 * width <= length <= 0.6 * width) and at_jamb and touches_wall:
            evidence.append("door leaf")
            break
    if "swing arc" in evidence:
        return "door", evidence
    # Window: two or more lines along the wall, inside the wall band, spanning most of the gap. Checked
    # before a lone leaf line, which can also be the face of a wall meeting the jamb.
    glazing = 0
    for orient, pos, b0, b1 in scan.axis:
        if orient == axis and c0 - 0.3 <= pos <= c1 + 0.3 and min(b1, a1) - max(b0, a0) >= 0.7 * width:
            glazing += 1
    if glazing >= 2:
        return "window", [f"{glazing} glazing lines"]
    if evidence:
        return "door", evidence
    return "opening", []


def detect_openings(scan, walls: list[dict[str, Any]], columns: list[dict[str, Any]], *,
                    millimetres_per_pdf_point: float, classifier=None) -> list[dict[str, Any]]:
    """
    walls / columns: {"id", "bbox", ...} in drawing units (PDF points, or mm for CAD with scale 1).
    classifier(axis, a0, a1, c0, c1) -> (kind, evidence); defaults to reading the symbols in `scan`.
    """
    classifier = classifier or (lambda axis, a0, a1, c0, c1: classify(scan, axis, a0, a1, c0, c1))
    scale = float(millimetres_per_pdf_point)
    obstacles = [(w["bbox"], w["id"]) for w in walls] + [(c["bbox"], c["id"]) for c in columns]
    found = []
    for line in _segments(walls, columns):
        # Merge overlapping pieces (a column standing inside a wall run) into occupied stretches;
        # the gaps between stretches are the candidate openings.
        stretches: list[list] = []
        for item in line["items"]:
            if stretches and item[0] <= stretches[-1][1] + 0.05:
                if item[1] > stretches[-1][1]:
                    stretches[-1][1], stretches[-1][2] = item[1], item
            else:
                stretches.append([item[0], item[1], item, item])
        pairs = [(left[2], right[3]) for left, right in zip(stretches, stretches[1:])]
        for (pa0, pa1, pc0, pc1, pkind, prev), (na0, na1, nc0, nc1, nkind, nxt) in pairs:
            if pkind == "column" and nkind == "column":
                continue                                    # a bay between columns is not a wall opening
            gap0, gap1 = pa1, na0
            width_mm = (gap1 - gap0) * scale
            if not MIN_OPENING_MM <= width_mm <= MAX_OPENING_MM:
                continue
            walls_here = [it for it in ((pc0, pc1, pkind), (nc0, nc1, nkind)) if it[2] == "wall"]
            c0 = min(c for c, _, _ in walls_here)
            c1 = max(c for _, c, _ in walls_here)
            gap = _rect(line["axis"], gap0, gap1, c0, c1)
            if any(_blocks(gap, bbox, line["axis"]) for bbox, _ in obstacles):
                continue                                    # a column or crossing wall fills the gap
            kind, evidence = classifier(line["axis"], gap0, gap1, c0, c1)
            found.append({
                "kind": kind,
                "axis": line["axis"],
                "bbox_pdf_points": [round(v, 3) for v in gap],
                "width_mm": round(width_mm, 1),
                "thickness_mm": round((c1 - c0) * scale, 1),
                "between": [prev["id"], nxt["id"]],
                "host_wall": prev if pkind == "wall" else nxt,
                "symbol_evidence": evidence,
            })
    return found


def opening_heights(opening: dict[str, Any], sheet_entry: dict[str, Any] | None, *,
                    millimetres_per_pdf_point: float) -> dict[str, Any]:
    """Sill and head heights (mm above the storey base) and where they came from."""
    entry = sheet_entry or {}
    kind = opening["kind"]
    # Grid bubbles come in pairs on one line (same x top and bottom, or same y left and right);
    # a lone "D" beside a door is a door tag even though it looks like a grid label.
    grid_positions = set()
    for label, points in (entry.get("labels") or {}).items():
        for i, (x, y) in enumerate(points):
            if any(j != i and (abs(x - x2) <= 0.5 or abs(y - y2) <= 0.5) and math.hypot(x - x2, y - y2) > 50
                   for j, (x2, y2) in enumerate(points)):
                grid_positions.add((label, round(x, 1), round(y, 1)))
    x0, y0, x1, y1 = opening["bbox_pdf_points"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    wanted = ("D",) if kind == "door" else ("W", "V") if kind == "window" else ()
    best = None
    for tag, box in entry.get("opening_tags") or []:
        if not tag.startswith(wanted) or not wanted:
            continue
        tx, ty = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if (tag, round(tx, 1), round(ty, 1)) in grid_positions:
            continue                                         # a grid bubble "D", not a door tag
        distance = math.hypot(tx - cx, ty - cy) * millimetres_per_pdf_point
        if distance <= TAG_SEARCH_MM and (best is None or distance < best[0]):
            best = (distance, tag)
    tag = best[1] if best else None
    row = (entry.get("opening_schedule") or {}).get(tag) if tag else None
    sills = sorted(set(entry.get("sill_notes") or []))
    heads = sorted(set(entry.get("head_notes") or []))
    result: dict[str, Any] = {"tag": tag}
    if kind == "door":
        if row:
            result.update(sill_mm=0.0, head_mm=row["height_mm"], height_source=f"schedule row: {row['text']}")
        elif len(heads) == 1:
            result.update(sill_mm=0.0, head_mm=heads[0], height_source="drawing note (door/lintel height)")
        else:
            result.update(sill_mm=0.0, head_mm=ASSUMED["door_head_mm"], height_source="assumed_typical")
    elif kind == "window":
        if row and row.get("sill_mm") is not None:
            result.update(sill_mm=row["sill_mm"], head_mm=row["sill_mm"] + row["height_mm"],
                          height_source=f"schedule row: {row['text']}")
        elif row and len(sills) == 1:
            result.update(sill_mm=sills[0], head_mm=sills[0] + row["height_mm"],
                          height_source=f"schedule row: {row['text']}; sill from drawing note")
        elif len(sills) == 1:
            head = heads[0] if len(heads) == 1 else ASSUMED["window_head_mm"]
            result.update(sill_mm=sills[0], head_mm=head,
                          height_source="drawing note (sill)" + ("" if len(heads) == 1 else "; head assumed_typical"))
        else:
            result.update(sill_mm=ASSUMED["window_sill_mm"], head_mm=ASSUMED["window_head_mm"], height_source="assumed_typical")
    else:
        head = heads[0] if len(heads) == 1 else ASSUMED["opening_head_mm"]
        result.update(sill_mm=0.0, head_mm=head,
                      height_source="drawing note (lintel height)" if len(heads) == 1 else "assumed_typical")
    if row:
        result["schedule_width_mm"] = row["width_mm"]
    return result


def build_opening_element(opening: dict[str, Any], heights: dict[str, Any], *, element_id: str,
                          millimetres_per_pdf_point: float, wall_height_mm: float) -> dict[str, Any]:
    """Opening as a box over the gap, full wall height; sill/head say where the door or window sits."""
    scale = float(millimetres_per_pdf_point)
    x0, y0, x1, y1 = (round(v * scale, 6) for v in opening["bbox_pdf_points"])
    head = min(float(heights["head_mm"]), wall_height_mm)
    sill = min(float(heights["sill_mm"]), head)
    vertices = [
        [x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0], [x0, y1, 0.0],
        [x0, y0, wall_height_mm], [x1, y0, wall_height_mm], [x1, y1, wall_height_mm], [x0, y1, wall_height_mm],
    ]
    host = opening["host_wall"]
    return {
        "element_id": element_id,
        "element_type": opening["kind"],
        "detected_by": "wall_gap",
        "component": opening["kind"],
        "host_component": host.get("component"),
        "between": opening["between"],
        "opening": {
            "axis": opening["axis"],
            "width_mm": opening["width_mm"],
            "thickness_mm": opening["thickness_mm"],
            "sill_mm": round(sill, 1),
            "head_mm": round(head, 1),
            "wall_height_mm": round(wall_height_mm, 1),
            "tag": heights.get("tag"),
            "height_source": heights["height_source"],
            "symbol_evidence": opening["symbol_evidence"],
        },
        "geometry_status": "provisional_mesh",
        "height_status": "assumed_typical" if heights["height_source"] == "assumed_typical" else "drawing_derived",
        "dimensions_mm": {"length": opening["width_mm"], "thickness": opening["thickness_mm"],
                          "height": round(head - sill, 1), "volume": 0.0},
        "mesh": {"primitive": "triangular_mesh", "vertices": vertices, "faces": BOX_FACES,
                 "vertex_count": 8, "face_count": 12, "closed_manifold": True},
        "lod": {"category": "opening", "minimum_lod": OPENING_MIN_LOD},
        "provenance": {
            "bbox_pdf_points": opening["bbox_pdf_points"],
            "between": opening["between"],
            "symbol_evidence": opening["symbol_evidence"],
            "height_source": heights["height_source"],
        },
        "limitations": [
            "Width and position are the gap between two confirmed walls (or a wall and a column).",
            "Door or window is read from the drawing's symbol in the gap.",
            "Heights are assumed typical values unless the drawing prints a schedule or note.",
        ],
    }
