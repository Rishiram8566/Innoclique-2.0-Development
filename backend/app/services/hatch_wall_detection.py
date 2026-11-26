"""
Stage 5.4 walls from the sheet's own hatch legend.

Architectural plans draw a wall as two parallel boundary lines with the wall
material's hatch between them, and print a hatch legend ("250mm THERMAL
INSULATED BLOCK WALL", "HOLLOW BLOCK WALL", ...) with a swatch of each hatch.
This module reads the evidence from the drawing itself instead of assuming
it:

* legend rows are text containing "WALL" with a small rectangle (the swatch)
  immediately to its left; the swatch's hatch gives a signature of stroke
  colour and line angle;
* a wall is a pair of parallel axis-aligned boundary lines, 80-600 mm apart
  (no long line between them), overlapping at least 600 mm, whose strip is
  filled with a hatch matching one legend signature along most of its length;
  where readings nest (a dashed grid line along a wall), the widest is kept.

Nothing is confirmed here. Each candidate records the legend label it matched,
the share of its hatch that matched, and whether it lies inside the page's
structural outline (the detected columns); only those inside are suggested.
A reviewer confirms walls per placed page; confirmed walls run the full
storey height, like columns.
"""

from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services import bim_geometry_builder as wall_builder

MIN_WALL_LENGTH_MM = 600.0
MIN_THICKNESS_MM = 80.0
MAX_THICKNESS_MM = 600.0
AXIS_TOLERANCE_PT = 0.05
STRIP_MARGIN_PT = 0.15
GRID_CELL_PT = 4.0
COVERAGE_BIN_MM = 1000.0
MIN_COVERAGE = 0.6
MIN_HATCH_PRIMITIVES = 3
SWATCH_MAX_GAP_PT = 40.0
SWATCH_WIDTH_PT = (8.0, 120.0)
SIGNATURE_MIN_SHARE = 0.15
SIGNATURE_MIN_PRIMITIVES = 10
BLACK_MATCH_SHARE = 0.6
COLOUR_MATCH_SHARE = 0.45
KEY_MIN_SHARE = 0.1
KEY_REQUIRED_SHARE = 0.3      # swatch keys this common must show up in the strip
MERGE_TOLERANCE_PT = 0.3
MERGE_GAP_PT = 0.6
CONTAINED_SHARE = 0.8
THROUGH_LINE_MM = 1000.0       # a line running this far past both ends of a pair passes through the wall
LEGEND_THICKNESS = re.compile(r"(\d{2,3})\s*(?:MM)?\s*(?:THK|THICK|MM)\b", re.IGNORECASE)
STRUCTURE_MARGIN_MM = 2000.0
CONCRETE_MIN_LENGTH_MM = 2500.0
CONNECT_TOLERANCE_MM = 150.0
WALL_MIN_LOD = 300
BLACK = (0.0, 0.0, 0.0)

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]


# ---------------------------------------------------------------------------
# One pass over a page's primitives
# ---------------------------------------------------------------------------

def hatch_key(stroke: Any, start: list[float], end: list[float]) -> tuple[Any, str]:
    """(stroke colour, angle class) of one line: '0', '45', '90', '135', 'other' or 'dot'."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    if math.hypot(dx, dy) < 0.3:
        angle = "dot"
    else:
        degrees = math.degrees(math.atan2(dy, dx)) % 180
        snap = min((0, 45, 90, 135, 180), key=lambda t: abs(degrees - t))
        angle = "other" if abs(degrees - snap) > 8 else str(snap % 180)
    colour = tuple(round(float(v), 2) for v in stroke) if isinstance(stroke, (list, tuple)) else None
    return colour, angle


@dataclass
class PageScan:
    """What wall and column detection need from one page, read in a single pass."""
    quads: list[dict[str, Any]] = field(default_factory=list)
    horizontal: list[tuple[float, float, float]] = field(default_factory=list)   # (y, x0, x1)
    vertical: list[tuple[float, float, float]] = field(default_factory=list)     # (x, y0, y1)
    strokes: list[tuple[float, float, tuple]] = field(default_factory=list)      # midpoint x, y, hatch key
    curves: list[tuple[float, float, float, float]] = field(default_factory=list)  # bezier bboxes (door swings)
    axis: list[tuple[str, float, float, float]] = field(default_factory=list)     # every axis-aligned line: (h|v, pos, a0, a1)
    _grids: dict[bool, dict] = field(default_factory=dict)

    def grid(self, swapped: bool) -> dict[tuple[int, int], list[tuple[float, float, tuple]]]:
        """Spatial hash of stroke midpoints; swapped=True indexes (y, x) for vertical strips."""
        if swapped not in self._grids:
            cells: dict[tuple[int, int], list] = defaultdict(list)
            for x, y, key in self.strokes:
                a, b = (y, x) if swapped else (x, y)
                cells[(int(a // GRID_CELL_PT), int(b // GRID_CELL_PT))].append((a, b, key))
            self._grids[swapped] = cells
        return self._grids[swapped]


def as_quad(primitive: dict[str, Any]) -> dict[str, Any]:
    """PDF 're' rectangles arrive as type 'rectangle' with a bbox; give them quad points like 'qu' paths."""
    if primitive.get("type") == "rectangle" and isinstance(primitive.get("bbox"), (list, tuple)) and len(primitive["bbox"]) == 4:
        x0, y0, x1, y1 = (float(v) for v in primitive["bbox"])
        return {**primitive, "type": "quad", "points": [[x0, y0], [x0, y1], [x1, y1], [x1, y0]], "from_rectangle": True}
    return primitive


def scan_primitives(primitives, *, min_line_pt: float) -> PageScan:
    scan = PageScan()
    for primitive in primitives:
        primitive = as_quad(primitive)
        kind = primitive.get("type")
        if kind == "quad" and isinstance(primitive.get("points"), list):
            scan.quads.append(primitive)
            points = primitive["points"]
            segments = [(points[i], points[(i + 1) % 4]) for i in range(4)]
        elif kind == "line" and "start" in primitive and "end" in primitive:
            segments = [(primitive["start"], primitive["end"])]
        elif kind == "bezier" and isinstance(primitive.get("points"), list) and primitive["points"]:
            xs = [float(p[0]) for p in primitive["points"]]
            ys = [float(p[1]) for p in primitive["points"]]
            scan.curves.append((min(xs), min(ys), max(xs), max(ys)))
            continue
        else:
            continue
        for start, end in segments:
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length >= min_line_pt and abs(dy) <= AXIS_TOLERANCE_PT:
                scan.horizontal.append((round((start[1] + end[1]) / 2, 3), min(start[0], end[0]), max(start[0], end[0])))
            elif length >= min_line_pt and abs(dx) <= AXIS_TOLERANCE_PT:
                scan.vertical.append((round((start[0] + end[0]) / 2, 3), min(start[1], end[1]), max(start[1], end[1])))
            if kind == "line" and length >= 0.3 and abs(dy) <= AXIS_TOLERANCE_PT:
                scan.axis.append(("h", round((start[1] + end[1]) / 2, 3), min(start[0], end[0]), max(start[0], end[0])))
            elif kind == "line" and length >= 0.3 and abs(dx) <= AXIS_TOLERANCE_PT:
                scan.axis.append(("v", round((start[0] + end[0]) / 2, 3), min(start[1], end[1]), max(start[1], end[1])))
            if kind == "line" and not primitive.get("fill"):
                scan.strokes.append(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2,
                                     hatch_key(primitive.get("stroke"), start, end)))
    return scan


def _read(sidecar: Path):
    with Path(sidecar).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


@lru_cache(maxsize=2)
def _cached_scan(path: str, mtime_ns: int, size: int, min_line_pt: float) -> PageScan:
    return scan_primitives(_read(Path(path)), min_line_pt=min_line_pt)


def page_scan(sidecar: Path, *, millimetres_per_pdf_point: float) -> PageScan:
    stat = Path(sidecar).stat()
    return _cached_scan(str(sidecar), stat.st_mtime_ns, stat.st_size,
                        round(MIN_WALL_LENGTH_MM / millimetres_per_pdf_point, 6))


_LINE = re.compile(r'"type":\s*"line",\s*"start":\s*\[([^,\]]+),\s*([^\]]+)\],\s*"end":\s*\[([^,\]]+),\s*([^\]]+)\]')


@lru_cache(maxsize=16)
def _cached_axis(path: str, mtime_ns: int, size: int) -> tuple:
    out = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if '"line"' not in line:
                continue
            match = _LINE.search(line)
            if match:
                x0, y0, x1, y1 = (float(v) for v in match.groups())
            else:
                primitive = json.loads(line)
                if primitive.get("type") != "line" or "start" not in primitive or "end" not in primitive:
                    continue
                (x0, y0), (x1, y1) = primitive["start"][:2], primitive["end"][:2]
            dx, dy = x1 - x0, y1 - y0
            if math.hypot(dx, dy) < 0.3:
                continue
            if abs(dy) <= AXIS_TOLERANCE_PT:
                out.append(("h", round((y0 + y1) / 2, 3), min(x0, x1), max(x0, x1)))
            elif abs(dx) <= AXIS_TOLERANCE_PT:
                out.append(("v", round((x0 + x1) / 2, 3), min(y0, y1), max(y0, y1)))
    return tuple(out)


def axis_lines(sidecar: Path) -> tuple:
    """
    The page's straight horizontal and vertical lines, as page_scan(...).axis gives them, read without
    the full scan (which also builds the hatch strokes and is several times slower on a large sheet).
    The result is small and stays cached for the sidecar's current version.
    """
    stat = Path(sidecar).stat()
    return _cached_axis(str(sidecar), stat.st_mtime_ns, stat.st_size)


# ---------------------------------------------------------------------------
# Legend signatures
# ---------------------------------------------------------------------------

def _inside(grid, x0, y0, x1, y1):
    found = []
    for cx in range(int(x0 // GRID_CELL_PT), int(x1 // GRID_CELL_PT) + 1):
        for cy in range(int(y0 // GRID_CELL_PT), int(y1 // GRID_CELL_PT) + 1):
            for a, b, key in grid.get((cx, cy), ()):
                if x0 < a < x1 and y0 < b < y1:
                    found.append((a, key))
    return found


def legend_signatures(scan: PageScan, legend_labels: list[tuple[str, list[float]]]) -> dict[str, dict[str, Any]]:
    """Wall legend rows: text with a swatch rectangle just left of it, and that swatch's hatch."""
    grid = scan.grid(False)
    result: dict[str, dict[str, Any]] = {}
    for text, bbox in legend_labels:
        text_mid_y = (bbox[1] + bbox[3]) / 2
        swatch = None
        for quad in scan.quads:
            xs = [p[0] for p in quad["points"]]
            ys = [p[1] for p in quad["points"]]
            width = max(xs) - min(xs)
            if (
                min(ys) - 1 <= text_mid_y <= max(ys) + 1
                and 0 <= bbox[0] - max(xs) <= SWATCH_MAX_GAP_PT
                and SWATCH_WIDTH_PT[0] <= width <= SWATCH_WIDTH_PT[1]
                and (swatch is None or max(xs) > swatch[2])
            ):
                swatch = (min(xs), min(ys), max(xs), max(ys))
        if swatch is None:
            continue
        counts = Counter(key for _, key in _inside(grid, swatch[0] + 0.3, swatch[1] + 0.3,
                                                    swatch[2] - 0.3, swatch[3] - 0.3))
        total = sum(counts.values())
        if total < SIGNATURE_MIN_PRIMITIVES:
            continue
        signature = {key: n / total for key, n in counts.items() if n / total >= SIGNATURE_MIN_SHARE}
        if signature and text.strip() not in result:
            result[text.strip()] = {"swatch_pdf": [round(v, 3) for v in swatch], "signature": signature}
    return result


def stated_thickness_mm(label: str) -> float | None:
    """Thickness printed in a legend label ("230 THK. BRICK WALL", "250mm THERMAL ..."), else None."""
    match = LEGEND_THICKNESS.search(label or "")
    return float(match.group(1)) if match else None


def match_hatch(
    found: list[tuple[float, tuple]], signatures: dict[str, dict[str, Any]], thickness_mm: float | None = None,
) -> tuple[str, float] | None:
    """
    Best legend entry whose hatch fills this strip, or None.

    Drawings often use one hatch for several wall types (e.g. 230 and 115 mm
    brick); on a tie the entry whose printed thickness is nearest the strip's
    measured thickness wins.
    """
    if not found:
        return None
    counts = Counter(key for _, key in found)
    colours = Counter(key[0] for _, key in found)
    total = sum(counts.values())
    best = None
    for label, entry in signatures.items():
        signature = entry["signature"]
        signature_colours = {key[0] for key in signature}
        every_key = all(counts[key] / total >= KEY_MIN_SHARE for key, share in signature.items()
                        if share >= KEY_REQUIRED_SHARE)
        if signature_colours == {BLACK}:
            # Black hatch is common (text, paving), so the angle mix must match too.
            share = sum(counts[key] for key in signature) / total
            ok = share >= BLACK_MATCH_SHARE and every_key
        else:
            # A distinct hatch colour: most of the strip is that colour and the swatch's angle shows up.
            share = sum(colours[c] for c in signature_colours) / total
            ok = share >= COLOUR_MATCH_SHARE and every_key
        if not ok:
            continue
        stated = stated_thickness_mm(label)
        closeness = -abs(stated - thickness_mm) if stated is not None and thickness_mm is not None else float("-inf")
        rank = (round(share, 2), closeness)
        if best is None or rank > best[2]:
            best = (label, round(share, 3), rank)
    return (best[0], best[1]) if best else None


# ---------------------------------------------------------------------------
# Wall strips
# ---------------------------------------------------------------------------

def _strips(lines, grid, signatures, *, t_min, t_max, min_len, bin_pt, scale=1.0):
    """
    Hatch-filled strips between parallel line pairs: (lo, hi, a0, a1, label, share, coverage).

    A long line between the pair blocks it (the wall has an inner layer), unless
    it runs well past the pair at both ends - a grid or setting-out line drawn
    through the wall, not part of it.
    """
    reach = THROUGH_LINE_MM / scale
    lines = sorted(set(lines))
    positions = [line[0] for line in lines]
    found_strips = []
    for i, (p, a0, a1) in enumerate(lines):
        j = bisect.bisect_right(positions, p + t_min)
        while j < len(lines) and positions[j] <= p + t_max:
            q, b0, b1 = lines[j]
            j += 1
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo < min_len:
                continue
            between = lines[i + 1:j - 1]
            if any(p < r < q and min(c1, hi) - max(c0, lo) > 0.5 * (hi - lo)
                   and not (c0 < min(a0, b0) - reach and c1 > max(a1, b1) + reach)
                   for r, c0, c1 in between):
                continue
            inside = _inside(grid, lo, p + STRIP_MARGIN_PT, hi, q - STRIP_MARGIN_PT)
            if len(inside) < MIN_HATCH_PRIMITIVES:
                continue
            bins = max(1, int((hi - lo) // bin_pt))
            coverage = len({min(bins - 1, int((a - lo) // bin_pt)) for a, _ in inside}) / bins
            matched = match_hatch(inside, signatures, (q - p) * scale)
            if matched and coverage >= MIN_COVERAGE:
                found_strips.append((p, q, lo, hi, matched[0], matched[1], round(coverage, 3)))
    return found_strips


def _drop_contained(strips):
    """
    Keep the widest reading of each hatched area.

    A short line inside a wall (a dashed grid line drawn along it, a hatch
    border) yields narrower strips inside the real one; a strip mostly
    covered by a wider kept strip is dropped.
    """
    kept: list[list] = []
    for strip in sorted(strips, key=lambda s: -((s[1] - s[0]) * (s[3] - s[2]))):
        p, q, lo, hi = strip[:4]
        area = (q - p) * (hi - lo)
        covered = sum(
            max(0.0, min(q, k[1]) - max(p, k[0])) * max(0.0, min(hi, k[3]) - max(lo, k[2])) for k in kept
        )
        if covered < CONTAINED_SHARE * area:
            kept.append(strip)
    return kept


def _merge(strips):
    """Join touching collinear strips of the same wall type (a boundary line broken at a junction)."""
    merged: list[list] = []
    for strip in sorted(strips, key=lambda s: (s[4], round(s[0], 1), round(s[1], 1), s[2])):
        p, q, lo, hi, label, share, coverage = strip
        last = merged[-1] if merged else None
        if (
            last and last[4] == label and abs(last[0] - p) < MERGE_TOLERANCE_PT
            and abs(last[1] - q) < MERGE_TOLERANCE_PT and lo <= last[3] + MERGE_GAP_PT
        ):
            span_last, span_new = last[3] - last[2], hi - lo
            last[5] = round((last[5] * span_last + share * span_new) / (span_last + span_new), 3)
            last[6] = round((last[6] * span_last + coverage * span_new) / (span_last + span_new), 3)
            last[3] = max(last[3], hi)
        else:
            merged.append([p, q, lo, hi, label, share, coverage])
    return merged


def wall_id(drawing_id: str, page_number: int, orientation: str, bbox: list[float]) -> str:
    return f"{drawing_id}:p{page_number}:hatchwall:{orientation[0]}:" + ",".join(f"{v:.2f}" for v in bbox)


def detect_hatch_walls(
    scan: PageScan, *, drawing_id: str, page_number: int,
    legend_labels: list[tuple[str, list[float]]], millimetres_per_pdf_point: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(wall candidates, legend signatures used)."""
    scale = float(millimetres_per_pdf_point)
    signatures = legend_signatures(scan, legend_labels)
    if not signatures:
        return [], {}
    options = dict(t_min=MIN_THICKNESS_MM / scale, t_max=MAX_THICKNESS_MM / scale,
                   min_len=MIN_WALL_LENGTH_MM / scale, bin_pt=COVERAGE_BIN_MM / scale, scale=scale)
    walls = []
    for orientation, lines, swapped in (("horizontal", scan.horizontal, False), ("vertical", scan.vertical, True)):
        strips = _drop_contained(_merge(_strips(lines, scan.grid(swapped), signatures, **options)))
        for p, q, lo, hi, label, share, coverage in strips:
            bbox = [lo, p, hi, q] if orientation == "horizontal" else [p, lo, q, hi]
            bbox = [round(v, 3) for v in bbox]
            walls.append({
                "wall_id": wall_id(drawing_id, page_number, orientation, bbox),
                "element_type": "hatch_wall_candidate",
                "drawing_id": drawing_id,
                "page_number": page_number,
                "orientation": orientation,
                "bbox_pdf_points": bbox,
                "thickness_mm": round((q - p) * scale, 1),
                "length_mm": round((hi - lo) * scale, 1),
                "wall_type": label,
                "hatch_match_share": share,
                "hatch_coverage": coverage,
                "legend_swatch_pdf": signatures[label]["swatch_pdf"],
                "verification_required": True,
            })
    walls.sort(key=lambda w: (w["bbox_pdf_points"][1], w["bbox_pdf_points"][0]))
    return walls, signatures


# ---------------------------------------------------------------------------
# Suggestions: walls inside the structural outline
# ---------------------------------------------------------------------------

def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _distance_outside(point, hull) -> float:
    """0 inside the (counter-clockwise) hull, else distance to its boundary."""
    if len(hull) < 3:
        return math.inf
    inside = all(
        (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= 0
        for a, b in zip(hull, hull[1:] + hull[:1])
    )
    if inside:
        return 0.0
    best = math.inf
    for a, b in zip(hull, hull[1:] + hull[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1)))
        best = min(best, math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy))
    return best


def is_concrete(wall_type: str) -> bool:
    return "CONCRETE" in wall_type.upper()


def _touch(a, b, gap: float) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def mark_suggestions(walls: list[dict[str, Any]], columns: list[dict[str, Any]], *, millimetres_per_pdf_point: float) -> None:
    """
    Suggest walls a reviewer can confirm in one go.

    * Every suggested wall lies inside the outline of the page's detected
      columns (2 m margin) - outside it the same hatches mark landscape works.
    * Masonry hatches (block walls) are walls by definition.
    * The concrete hatch also marks equipment plinths and trenches, so a
      concrete strip is suggested only when it is at least 2.5 m long and
      connected (end or side within 150 mm) to a column, a masonry wall or
      another suggested concrete wall.
    """
    scale = millimetres_per_pdf_point
    corners = [(c["bbox_pdf_points"][i], c["bbox_pdf_points"][j]) for c in columns for i, j in ((0, 1), (2, 1), (2, 3), (0, 3))]
    hull = convex_hull(corners)
    margin_pt = STRUCTURE_MARGIN_MM / scale
    touch_pt = CONNECT_TOLERANCE_MM / scale
    inside = {}
    for wall in walls:
        x0, y0, x1, y1 = wall["bbox_pdf_points"]
        distance = 0.0 if len(hull) < 3 else _distance_outside(((x0 + x1) / 2, (y0 + y1) / 2), hull)
        inside[wall["wall_id"]] = distance
        wall["suggested"] = False
        if distance > margin_pt:
            wall["suggestion_reason"] = (f"{round(distance * scale)} mm outside the column outline; "
                                         "likely landscape or site work - check before confirming.")
        elif not is_concrete(wall["wall_type"]):
            wall["suggested"] = True
            wall["suggestion_reason"] = ("Masonry wall hatch inside the column outline." if len(hull) >= 3
                                         else "Masonry wall hatch (no column outline on this page to compare with).")
        else:
            wall["suggestion_reason"] = (
                "Concrete hatch shorter than 2.5 m - may be a plinth or kerb; check before confirming."
                if wall["length_mm"] < CONCRETE_MIN_LENGTH_MM else
                "Concrete hatch not connected to a column or wall - may be a plinth or trench; check before confirming."
            )
    anchors = [c["bbox_pdf_points"] for c in columns] + [w["bbox_pdf_points"] for w in walls if w["suggested"]]
    pending = [w for w in walls if not w["suggested"] and is_concrete(w["wall_type"])
               and inside[w["wall_id"]] <= margin_pt and w["length_mm"] >= CONCRETE_MIN_LENGTH_MM]
    changed = True
    while changed:
        changed = False
        for wall in list(pending):
            if any(_touch(wall["bbox_pdf_points"], anchor, touch_pt) for anchor in anchors):
                wall["suggested"] = True
                wall["suggestion_reason"] = "Concrete wall connected to the column and wall network."
                anchors.append(wall["bbox_pdf_points"])
                pending.remove(wall)
                changed = True


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

def component_name(wall_type: str) -> str:
    return " ".join(wall_type.lower().split())


def build_hatch_wall_mesh(candidate: dict[str, Any], *, millimetres_per_pdf_point: float, height_mm: float) -> dict[str, Any]:
    """Closed box over the hatched strip (page-local mm, z up from 0)."""
    if not (height_mm and height_mm > 0):
        raise ValueError("Wall height must be positive")
    x0, y0, x1, y1 = (round(float(v) * millimetres_per_pdf_point, 6) for v in candidate["bbox_pdf_points"])
    vertices = [
        [x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0], [x0, y1, 0.0],
        [x0, y0, height_mm], [x1, y0, height_mm], [x1, y1, height_mm], [x0, y1, height_mm],
    ]
    if not wall_builder._mesh_is_closed(vertices, BOX_FACES):
        raise ValueError("Wall mesh is not closed")
    width, depth = round(x1 - x0, 3), round(y1 - y0, 3)
    length, thickness = max(width, depth), min(width, depth)
    return {
        "element_id": candidate["wall_id"],
        "element_type": "wall",
        "detected_by": "legend_hatch",
        "component": component_name(candidate["wall_type"]),
        "wall_type": candidate["wall_type"],
        "geometry_status": "provisional_mesh",
        "approval_status": "not_approved",
        "height_status": "storey_height_reviewer_confirmed",
        "dimensions_mm": {"length": length, "thickness": thickness, "height": round(height_mm, 3),
                          "volume": round(length * thickness * height_mm, 3)},
        "mesh": {"primitive": "triangular_mesh", "vertices": vertices, "faces": BOX_FACES,
                 "vertex_count": 8, "face_count": 12, "closed_manifold": True},
        "lod": {"category": "wall", "minimum_lod": WALL_MIN_LOD},
        "provenance": {
            "bbox_pdf_points": candidate["bbox_pdf_points"],
            "legend_label": candidate["wall_type"],
            "legend_swatch_pdf": candidate.get("legend_swatch_pdf"),
            "hatch_match_share": candidate.get("hatch_match_share"),
            "hatch_coverage": candidate.get("hatch_coverage"),
            "height_evidence": {"height_mm": round(height_mm, 3), "status": "drawing_derived",
                                "source_reference": "storey height of the page's plan placement"},
        },
        "limitations": [
            "Wall extent is the hatched strip between two boundary lines; openings show as breaks in the hatch.",
            "Wall type is the hatch-legend entry whose swatch matches the strip's hatch.",
            "Height is the full storey (slab to slab) confirmed by a reviewer.",
        ],
    }


def page_hatch_walls(
    folder: Path, drawing_id: str, page_number: int, *,
    millimetres_per_pdf_point: float | None, columns: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Detect a page's hatch walls from its sidecar and the sheet's legend text."""
    from app.services.sheet_registration import sheet_index

    if not millimetres_per_pdf_point:
        return [], {}
    sidecar = Path(folder) / "artifacts" / "primitive_sidecars" / f"{drawing_id}_page_{page_number}.jsonl"
    if not sidecar.is_file():
        return [], {}
    labels = (sheet_index(folder).get(drawing_id) or {}).get("wall_legend_labels") or []
    scan = page_scan(sidecar, millimetres_per_pdf_point=millimetres_per_pdf_point)
    walls, signatures = detect_hatch_walls(scan, drawing_id=drawing_id, page_number=page_number,
                                           legend_labels=labels, millimetres_per_pdf_point=millimetres_per_pdf_point)
    mark_suggestions(walls, columns or [], millimetres_per_pdf_point=millimetres_per_pdf_point)
    return walls, signatures
