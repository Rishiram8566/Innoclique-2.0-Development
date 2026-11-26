"""
Stage 5.4 columns (LOD 100).

Column candidates come from the drawings themselves:

* grid lines are read from grid-bubble labels: the same label printed at
  both ends of a line (top and bottom -> a vertical line at that x; left and
  right -> a horizontal line at that y);
* a column candidate is a square outline ("quad" primitive) of plausible
  column size centred on a grid intersection or, for perimeter columns drawn
  with one face on the grid line, containing it.

Nothing is confirmed here. A reviewer confirms columns per placed page; a
confirmed column runs the page's full storey height (slab to slab) and stops
being served if that placement is revoked or changed.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.services import bim_geometry_builder as wall_builder

GRID_ALIGN_TOLERANCE_PT = 0.5
GRID_SPAN_FRACTION = 0.3
INTERSECTION_TOLERANCE_PT = 4.0
FACE_TOLERANCE_PT = 1.0
COLUMN_MIN_MM = 300.0
COLUMN_MAX_MM = 1200.0
COLUMN_MAX_ASPECT = 1.43
# Third pass, only for intersections still empty: smaller or more elongated columns (e.g. 230 x 300,
# 230 x 450 in residential frames). Kept separate so earlier passes keep their results and ids.
SMALL_COLUMN_MIN_MM = 200.0
SMALL_COLUMN_MAX_ASPECT = 2.0
COLUMN_MIN_LOD = 100

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]


def grid_lines(sheet_entry: dict[str, Any] | None, page_size: tuple[float, float]) -> dict[str, list[tuple[str, float]]]:
    """
    {'vertical': [(label, x)], 'horizontal': [(label, y)]} from labels printed at both ends of a line.

    A label can define more than one line (e.g. grid "B" and a section-cut
    marker "B"), so every qualifying pair is kept rather than overwritten.
    """
    width, height = page_size
    vertical: list[tuple[str, float]] = []
    horizontal: list[tuple[str, float]] = []
    for label, points in ((sheet_entry or {}).get("labels") or {}).items():
        for i, (x1, y1) in enumerate(points):
            for x2, y2 in points[i + 1:]:
                if abs(x1 - x2) <= GRID_ALIGN_TOLERANCE_PT and abs(y1 - y2) >= GRID_SPAN_FRACTION * height:
                    vertical.append((label, (x1 + x2) / 2))
                if abs(y1 - y2) <= GRID_ALIGN_TOLERANCE_PT and abs(x1 - x2) >= GRID_SPAN_FRACTION * width:
                    horizontal.append((label, (y1 + y2) / 2))

    def dedupe(lines: list[tuple[str, float]]) -> list[tuple[str, float]]:
        kept: list[tuple[str, float]] = []
        for label, position in sorted(lines, key=lambda item: item[1]):
            if not kept or abs(kept[-1][1] - position) > GRID_ALIGN_TOLERANCE_PT:
                kept.append((label, position))
        return kept

    return {"vertical": dedupe(vertical), "horizontal": dedupe(horizontal)}


def _nearest(value: float, lines: list[tuple[str, float]]) -> tuple[str | None, float]:
    best = min(lines, key=lambda item: abs(item[1] - value), default=(None, math.inf))
    return best[0], abs(best[1] - value) if best[0] is not None else math.inf


def _line_position(lines: list[tuple[str, float]], value: float) -> float:
    return min(lines, key=lambda item: abs(item[1] - value))[1]


def column_id(drawing_id: str, page_number: int, path_index: int, item_index: int) -> str:
    return f"{drawing_id}:p{page_number}:column:{path_index}-{item_index}"


def _column_outline(primitive, millimetres_per_pdf_point, *, min_mm=COLUMN_MIN_MM, max_aspect=COLUMN_MAX_ASPECT):
    """(xs, ys, width_mm, height_mm) of a quad of column size and shape, else None."""
    from app.services.hatch_wall_detection import as_quad

    primitive = as_quad(primitive)
    if primitive.get("type") != "quad" or not isinstance(primitive.get("points"), list):
        return None
    xs = [float(p[0]) for p in primitive["points"]]
    ys = [float(p[1]) for p in primitive["points"]]
    width_pt, height_pt = max(xs) - min(xs), max(ys) - min(ys)
    if width_pt <= 0 or height_pt <= 0:
        return None
    width_mm, height_mm = width_pt * millimetres_per_pdf_point, height_pt * millimetres_per_pdf_point
    if not (min_mm <= width_mm <= COLUMN_MAX_MM and min_mm <= height_mm <= COLUMN_MAX_MM):
        return None
    if max(width_mm, height_mm) / min(width_mm, height_mm) > max_aspect:
        return None
    return xs, ys, width_mm, height_mm


def _lines_through(lines, low, high):
    return [item for item in lines if low - FACE_TOLERANCE_PT <= item[1] <= high + FACE_TOLERANCE_PT]


def detect_columns(
    primitives, *, drawing_id: str, page_number: int, grid: dict[str, dict[str, float]],
    millimetres_per_pdf_point: float,
) -> list[dict[str, Any]]:
    """
    Square outlines of column size on grid intersections.

    First pass: outlines centred on an intersection (within 4 pt). Second pass,
    for intersections still empty: outlines that contain the intersection -
    perimeter columns are usually drawn with one face on the grid line rather
    than centred on it.
    """
    if not grid["vertical"] or not grid["horizontal"]:
        return []
    found: dict[tuple, dict[str, Any]] = {}

    def record(primitive, xs, ys, width_mm, height_mm, key, labels, offsets, alignment):
        found[key] = {
            "column_id": column_id(drawing_id, page_number, primitive["path_index"], primitive["item_index"]),
            "element_type": "column_candidate",
            "drawing_id": drawing_id,
            "page_number": page_number,
            "grid_label": f"{labels[0]}/{labels[1]}",
            "grid_position_pdf": [key[0], key[1]],
            "grid_alignment": alignment,
            "bbox_pdf_points": [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)],
            "size_mm": [round(width_mm, 1), round(height_mm, 1)],
            "offset_from_grid_pt": [round(offsets[0], 3), round(offsets[1], 3)],
            "source": {"path_index": primitive["path_index"], "item_index": primitive["item_index"], "type": "quad"},
            "verification_required": True,
        }

    primitives = list(primitives)
    outlines = []
    for primitive in primitives:
        outline = _column_outline(primitive, millimetres_per_pdf_point)
        if outline is None:
            continue
        outlines.append((primitive, outline))
        xs, ys, width_mm, height_mm = outline
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        x_label, dx = _nearest(cx, grid["vertical"])
        y_label, dy = _nearest(cy, grid["horizontal"])
        if dx > INTERSECTION_TOLERANCE_PT or dy > INTERSECTION_TOLERANCE_PT:
            continue
        # One column per grid intersection: an outline drawn twice (or slightly
        # offset) at the same intersection is the same column; keep the first.
        key = (round(_line_position(grid["vertical"], cx), 1), round(_line_position(grid["horizontal"], cy), 1))
        if key not in found:
            record(primitive, xs, ys, width_mm, height_mm, key, (x_label, y_label), (dx, dy), "centred")

    for primitive, (xs, ys, width_mm, height_mm) in outlines:
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        through_x = _lines_through(grid["vertical"], min(xs), max(xs))
        through_y = _lines_through(grid["horizontal"], min(ys), max(ys))
        if len(through_x) != 1 or len(through_y) != 1:
            continue    # no intersection inside, or ambiguous (two grid lines through one outline)
        (x_label, x_line), (y_label, y_line) = through_x[0], through_y[0]
        key = (round(x_line, 1), round(y_line, 1))
        if key not in found:
            record(primitive, xs, ys, width_mm, height_mm, key, (x_label, y_label),
                   (abs(cx - x_line), abs(cy - y_line)), "face_on_grid")

    for primitive in primitives:
        outline = _column_outline(primitive, millimetres_per_pdf_point,
                                  min_mm=SMALL_COLUMN_MIN_MM, max_aspect=SMALL_COLUMN_MAX_ASPECT)
        if outline is None:
            continue
        xs, ys, width_mm, height_mm = outline
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        through_x = _lines_through(grid["vertical"], min(xs), max(xs))
        through_y = _lines_through(grid["horizontal"], min(ys), max(ys))
        if len(through_x) != 1 or len(through_y) != 1:
            continue
        (x_label, x_line), (y_label, y_line) = through_x[0], through_y[0]
        key = (round(x_line, 1), round(y_line, 1))
        if key not in found:
            record(primitive, xs, ys, width_mm, height_mm, key, (x_label, y_label),
                   (abs(cx - x_line), abs(cy - y_line)), "small_or_elongated")
    return sorted(found.values(), key=lambda c: (c["grid_position_pdf"][1], c["grid_position_pdf"][0]))


def read_page_primitives(sidecar: Path):
    with Path(sidecar).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def build_column_mesh(candidate: dict[str, Any], *, millimetres_per_pdf_point: float, height_mm: float) -> dict[str, Any]:
    """Closed box from the column outline (page-local mm, z up from 0)."""
    if not (height_mm and height_mm > 0):
        raise ValueError("Column height must be positive")
    x0, y0, x1, y1 = (float(v) * millimetres_per_pdf_point for v in candidate["bbox_pdf_points"])
    x0, y0, x1, y1 = (round(v, 6) for v in (x0, y0, x1, y1))
    vertices = [
        [x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0], [x0, y1, 0.0],
        [x0, y0, height_mm], [x1, y0, height_mm], [x1, y1, height_mm], [x0, y1, height_mm],
    ]
    if not wall_builder._mesh_is_closed(vertices, BOX_FACES):
        raise ValueError("Column mesh is not closed")
    width, depth = round(x1 - x0, 3), round(y1 - y0, 3)
    return {
        "element_id": candidate["column_id"],
        "element_type": "column",
        "component": "column",
        "grid_label": candidate.get("grid_label"),
        "geometry_status": "provisional_mesh",
        "approval_status": "not_approved",
        "height_status": "storey_height_reviewer_confirmed",
        "dimensions_mm": {"width": width, "depth": depth, "height": round(height_mm, 3),
                          "length": max(width, depth), "thickness": min(width, depth),
                          "volume": round(width * depth * height_mm, 3)},
        "mesh": {"primitive": "triangular_mesh", "vertices": vertices, "faces": BOX_FACES,
                 "vertex_count": 8, "face_count": 12, "closed_manifold": True},
        "lod": {"category": "column", "minimum_lod": COLUMN_MIN_LOD},
        "provenance": {
            "source_outline": candidate["source"],
            "bbox_pdf_points": candidate["bbox_pdf_points"],
            "grid_label": candidate.get("grid_label"),
            "offset_from_grid_pt": candidate.get("offset_from_grid_pt"),
            "height_evidence": {"height_mm": round(height_mm, 3), "status": "drawing_derived",
                                "source_reference": "storey height of the page's plan placement"},
        },
        "limitations": [
            "Column position and size come from a square outline on a grid intersection.",
            "Height is the full storey (slab to slab) confirmed by a reviewer.",
            "Reinforcement, capitals and beams are not in the architectural drawings.",
        ],
    }


def page_columns(folder: Path, drawing_id: str, page_number: int, *, page_size: tuple[float, float],
                 millimetres_per_pdf_point: float | None) -> list[dict[str, Any]]:
    """Detect a page's column candidates from its sidecar and the sheet's grid bubbles."""
    from app.services.hatch_wall_detection import page_scan
    from app.services.sheet_registration import sheet_index

    if not millimetres_per_pdf_point:
        return []
    sidecar = Path(folder) / "artifacts" / "primitive_sidecars" / f"{drawing_id}_page_{page_number}.jsonl"
    if not sidecar.is_file():
        return []
    grid = grid_lines(sheet_index(folder).get(drawing_id), page_size)
    quads = page_scan(sidecar, millimetres_per_pdf_point=millimetres_per_pdf_point).quads
    return detect_columns(quads, drawing_id=drawing_id, page_number=page_number,
                          grid=grid, millimetres_per_pdf_point=millimetres_per_pdf_point)
