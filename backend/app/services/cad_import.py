"""
Import a DXF floor plan (AutoCAD: Save As > DXF; DWG is converted first when a converter is installed).

A DXF carries what a PDF loses: real units, layers and named blocks. The
importer reads them, and still invents nothing:

* scale     - the file's own units ($INSUNITS); unit-less files are taken as
              millimetres only when the drawing's size makes that plausible;
* walls     - closed outlines and hatches on wall layers (A-WALL, *WALL*,
              *PARTITION*, block-work hatches such as "200 MM HB"), or parallel
              line pairs on those layers; a rectilinear outline is split into
              straight pieces; site walls (boundary, kerb, landscape layers) and
              walls away from the building are left out;
* columns   - closed outlines on column layers (S-COLS, *COL*); on a shared
              "columns & walls" layer an outline is a column when it is compact
              (at most 1.5 m, sides within 1:3), otherwise a wall;
* doors and windows - the gaps between walls, typed by the door or window
              block inserted there (layer or block name with DOOR / WIN / GLAZ);
* equipment - blocks on equipment / furniture layers (chillers, transformers,
              panels, pumps, tanks...): footprint and position exactly as drawn,
              height typical for the kind (display only, flagged);
* heights   - levels printed in the file's text or room tags (SSL / FFL; a level
              written with a decimal point, +0.45, is read in metres), the next
              plan's level when several floors are uploaded, door/window schedule
              rows, sill notes; otherwise typical values, marked assumed;
* rooms     - room names printed in the file's text (also inside blocks).

Output uses the building frame of the viewer: X = x, Y = -y (plan down), Z up, millimetres.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf import path as ezpath

from app.services import drawing_text_facts
from app.services import opening_detection as od
from app.services.wall_height_evidence_association import parse_level_annotations, parse_level_unit_note

SOURCE = "CAD drawing (DXF)"
UNIT_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 14: 100.0}
WALL_LAYER = re.compile(r"WALL|PARTITION|\bMUR|\bHB\b|BLOCK ?WORK|MASONRY|COL ?& ?WAL", re.IGNORECASE)
NOT_WALL_LAYER = re.compile(r"BOUNDARY|SITE|KERB|CURB|LANDSCAP|HARDSCAP|FENCE|EXIST|DEMOL|FUTURE|PATT|POCHE|DIM|"
                            r"ANNO|TEXT|SYMB|FINISH|TILE|CLAD|CEIL|FURN|EQ[PM]|PROPOS|DRAFT|OPTION", re.IGNORECASE)
# Blocks that are overlays on the plan, not its walls: a facade proposal, a section marker, a key plan...
NOT_WALL_BLOCK = re.compile(r"PROPOS|DRAFT|OPTION|FA[CÇ]+ADE|SHADE|CANOPY|CUTTING|SECTION|KEY ?PLAN|LEGEND|GRID",
                            re.IGNORECASE)
WALL_PATTERN_LAYER = re.compile(r"PATT|HATCH|POCHE", re.IGNORECASE)
COLUMN_LAYER = re.compile(r"(^|[-_ ])COL(S|UMN|UMNS)?($|[-_ ])|COLUMN", re.IGNORECASE)
# Layers of the services disciplines (E- electrical, M- mechanical, DR- drainage, FA- fire alarm ...): never walls or doors.
MEP_LAYER = re.compile(r"^(?:E|M|P|FA|FF|FP|FS|D|DR|EL|ELV|PL|HV|HVAC|IT|SEC)[-_ ]", re.IGNORECASE)
DOOR_NAME = re.compile(r"DOOR|(^|[-_ ])DR($|[-_ ])", re.IGNORECASE)
DOOR_TAG = re.compile(r"DOOR ?NO|TAG|MARK|SCHED", re.IGNORECASE)
WINDOW_NAME = re.compile(r"WIN|GLAZ|WINDOW|VENT", re.IGNORECASE)
GRID_TEXT = re.compile(r"^(?:[A-Z]{1,2}|\d{1,2})$")
ROOF_TITLE = re.compile(r"\bROOF\b", re.IGNORECASE)
MIN_WALL_MM, MIN_T_MM, MAX_T_MM = 300.0, 50.0, 600.0
MAX_OUTLINE_T_MM = 1000.0          # a drawn wall outline or hatch may be a thick retaining wall
COLUMN_MAX_MM = 1500.0
ASSUMED_STOREY_MM = 3000.0
SITE_MARGIN_MM = 1000.0
STAIR_REACH_MM = 9000.0
BOX_FACES = od.BOX_FACES

EQUIP_LAYER = re.compile(r"EQ[PM]|EQUI?P|CHLR|CHILLER|PUMP|TANK|GENSET|GENERATOR|\bAHU\b|TRANSFORMER|SWGR|"
                         r"SWITCHGEAR|PANEL|FURN", re.IGNORECASE)
EQUIP_NAME = re.compile(r"CHILLER|PUMP|TRANSFORMER|\d\s?KVA|GENSET|GENERATOR|\bAHU\b|AIR HANDLING|TANK|PANEL|\bACB\b|PFCC|\bMCC\b|"
                        r"SMDB|\bMDB\b|LVDB|\bUPS\b|BATTER|RACK|CHAIR|DESK|TABLE|SOFA|\bBED\b", re.IGNORECASE)
EQUIP_SKIP = re.compile(r"FUTURE|\bFUT\b|BIKE|DEVC|DEVICE|DVIC|TAG|SYMB|ANNO|TEXT|PLINTH|TROLL|LCD|MONITOR|KEYBOARD|MOUSE|"
                        r"PHONE|SUPPORT|CHAMBER|PIPE|ACCS|GRID|FNDN|HIDDEN", re.IGNORECASE)
EQUIP_SIZE_MM = (300.0, 15000.0)
LARGE_EQUIPMENT = {"cooling tower"}       # a bank of cooling-tower cells is drawn as one block
LARGE_EQUIPMENT_MM = 45000.0
# kind, pattern on "block name + layer", typical height (display only), body colour.
EQUIP_KINDS = [
    ("cooling tower", re.compile(r"COOLING ?TOWER|CTWR|(?:^|[^A-Z])CT[_ -]", re.I), 5500.0, "#cfd6dc"),
    ("chiller", re.compile(r"CHILLER|CHLR", re.I), 2600.0, "#2f5f9e"),
    ("transformer", re.compile(r"TRANSFORMER|\d\s?KVA|\bTX\b", re.I), 2300.0, "#5f6f64"),
    ("generator set", re.compile(r"GENSET|GENERATOR", re.I), 2400.0, "#d0a020"),
    ("pump set", re.compile(r"PUMP", re.I), 1100.0, "#2c6fb5"),
    ("air handling unit", re.compile(r"\bF?AHU\b|AIR HANDL|\bFCU\b", re.I), 2000.0, "#a7b0b8"),
    ("tank", re.compile(r"TANK|\bDT\b", re.I), 1800.0, "#8fb2c8"),
    ("UPS / battery rack", re.compile(r"\bUPS\b|BATTER", re.I), 2000.0, "#4a4f57"),
    ("server rack", re.compile(r"RACK|SERVER", re.I), 2100.0, "#2b2f36"),
    ("hose reel cabinet", re.compile(r"HOSE|FHC", re.I), 1500.0, "#b8322b"),
    ("chair", re.compile(r"CHAIR", re.I), 900.0, "#3a3a3a"),
    ("desk", re.compile(r"DESK|TABLE|WORKSTATION", re.I), 750.0, "#8a6a4a"),
    ("electrical panel", re.compile(r"ELEC|POWR|PANEL|\bACB\b|PFCC|\bMCC\b|SMDB|\bMDB\b|LVDB|SWGR|SWITCH", re.I),
     2200.0, "#c4c8cc"),
]
EQUIP_DEFAULT = ("equipment", 1500.0, "#9aa3ab")
EQUIP_PLINTH = {"cooling tower", "chiller", "transformer", "generator set", "pump set", "air handling unit", "tank", "electrical panel",
                "UPS / battery rack", "equipment"}
PLINTH_COLOUR = "#3b3f44"
FAN_STACK_MM = 1800.0             # display height of a cooling-tower fan stack (not on the plan)
FAN_STACK_COLOUR = "#5b6670"
PLINTH_MM = 100.0


def _own(layer: str) -> str:
    """A bound xref's layer "XREF_X$0$A-ARCH-WALL" is the plan's own "A-ARCH-WALL"."""
    return str(layer or "").split("$0$")[-1].strip()


def _box(x0, y0, x1, y1, z0, z1):
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


def _triangulate(poly: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear clipping for a simple polygon (counter-clockwise or clockwise)."""
    pts = list(poly)
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    order = list(range(len(pts))) if area > 0 else list(reversed(range(len(pts))))
    triangles = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(order) > 3 and guard < 10000:
        guard += 1
        for i in range(len(order)):
            a, b, c = order[i - 1], order[i], order[(i + 1) % len(order)]
            if cross(pts[a], pts[b], pts[c]) <= 0:
                continue
            if any(cross(pts[a], pts[b], pts[p]) > 0 and cross(pts[b], pts[c], pts[p]) > 0 and cross(pts[c], pts[a], pts[p]) > 0
                   for p in order if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            order.pop(i)
            break
        else:
            break
    if len(order) == 3:
        triangles.append(tuple(order))
    return triangles


def _prism(poly, z0, z1):
    n = len(poly)
    vertices = [[x, y, z0] for x, y in poly] + [[x, y, z1] for x, y in poly]
    faces = []
    for a, b, c in _triangulate(poly):
        faces.append([a, c, b])
        faces.append([a + n, b + n, c + n])
    for i in range(n):
        j = (i + 1) % n
        faces += [[i, j, j + n], [i, j + n, i + n]]
    return vertices, faces


def _clean(poly):
    """Drop repeated points and the closing point."""
    out = []
    for p in poly:
        p = (float(p[0]), float(p[1]))
        if not out or abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
        out.pop()
    return out


def _closed_shapes(entity) -> list[list[tuple[float, float]]]:
    kind = entity.dxftype()
    if kind == "LWPOLYLINE" and entity.closed:
        return [_clean([(p[0], p[1]) for p in entity.get_points("xy")])]
    if kind == "POLYLINE" and entity.is_closed:
        return [_clean([(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices])]
    if kind == "HATCH":
        shapes = []
        try:
            paths = ezpath.from_hatch(entity)
        except Exception:                      # a damaged boundary: skip it
            return []
        for path in paths:
            pts = _clean([(v.x, v.y) for v in path.flattening(10.0)])
            if len(pts) >= 3:
                shapes.append(pts)
        return shapes
    return []


def _segments(entity, *, closed_too: bool = False):
    kind = entity.dxftype()
    if kind == "LINE":
        return [((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y))]
    if kind == "LWPOLYLINE" and (closed_too or not entity.closed):
        pts = [(p[0], p[1]) for p in entity.get_points("xy")]
        if entity.closed and pts:
            pts.append(pts[0])
        return list(zip(pts, pts[1:]))
    return []


def _rect_of(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2
    is_rect = area >= 0.97 * (x1 - x0) * (y1 - y0)
    return (x0, y0, x1, y1), is_rect


def _rectilinear_pieces(poly, tol: float = 1.0):
    """
    An outline whose edges all run along the axes, as rectangles (horizontal bands merged upward);
    None when an edge is slanted or curved.
    """
    pts = _clean(poly)
    n = len(pts)
    if n < 4:
        return None
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    if any(abs(a[0] - b[0]) > tol and abs(a[1] - b[1]) > tol for a, b in edges):
        return None
    vertical = [(a[0], min(a[1], b[1]), max(a[1], b[1])) for a, b in edges if abs(a[1] - b[1]) > tol]
    ys = sorted({round(p[1], 1) for p in pts})
    pieces: list[list[float]] = []
    for y0, y1 in zip(ys, ys[1:]):
        mid = (y0 + y1) / 2
        xs = sorted(x for x, e0, e1 in vertical if e0 < mid < e1)
        for k in range(0, len(xs) - 1, 2):
            x0, x1 = xs[k], xs[k + 1]
            match = next((p for p in pieces if abs(p[0] - x0) <= tol and abs(p[2] - x1) <= tol and abs(p[3] - y0) <= tol), None)
            if match:
                match[3] = y1
            else:
                pieces.append([x0, y0, x1, y1])
    return [tuple(p) for p in pieces if p[2] - p[0] > tol and p[3] - p[1] > tol]


def _square_chamfers(poly, max_len: float, tol: float = 1.0):
    """
    Short slanted edges (45-degree chamfers at wall corners) replaced by a square corner, the one that keeps
    more of the wall; None when a slanted edge is longer (a real angled or curved wall).
    """
    pts = _clean(poly)
    n = len(pts)

    def area(points):
        return abs(sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
                       for i in range(len(points)))) / 2

    out = list(pts)
    k = 0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if abs(a[0] - b[0]) <= tol or abs(a[1] - b[1]) <= tol:
            continue
        if math.dist(a, b) > max_len:
            return None
        position = out.index(a) + 1
        options = [(b[0], a[1]), (a[0], b[1])]
        best = max(options, key=lambda c: area(out[:position] + [c] + out[position:]))
        out.insert(position, best)
        k += 1
    return _clean(out) if k else pts


def _subtract(intervals, cuts):
    """Intervals minus the cut intervals."""
    out = list(intervals)
    for c0, c1 in cuts:
        nxt = []
        for lo, hi in out:
            if c1 <= lo or c0 >= hi:
                nxt.append((lo, hi))
                continue
            if lo < c0:
                nxt.append((lo, c0))
            if c1 < hi:
                nxt.append((c1, hi))
        out = nxt
    return out


def _pair_runs(lines, f):
    """
    lines: (position across, start along, end along), sorted. Parallel lines MIN_T..MAX_T apart overlapping
    >= MIN_WALL -> (along0, pos, along1, pos2). A wall side drawn as several pieces pairs with each of them;
    each stretch of a line pairs with its nearest partner, and a stretch already used as a wall's far side does
    not start another wall.
    """
    out = []
    used: list[list[tuple[float, float]]] = [[] for _ in lines]
    for i, (p, a0, a1) in enumerate(lines):
        free = [iv for iv in _subtract([(a0, a1)], used[i]) if (iv[1] - iv[0]) * f >= MIN_WALL_MM]
        if not free:
            continue
        partners = []
        for j in range(i + 1, len(lines)):
            gap = (lines[j][0] - p) * f
            if gap > MAX_T_MM:
                break
            if gap >= MIN_T_MM:
                partners.append((gap, j))
        covered: list[tuple[float, float]] = []
        for _, j in sorted(partners):
            q, b0, b1 = lines[j]
            for lo, hi in free:
                lo2, hi2 = max(lo, b0), min(hi, b1)
                if (hi2 - lo2) * f < MIN_WALL_MM:
                    continue
                for s0, s1 in _subtract([(lo2, hi2)], covered):
                    if (s1 - s0) * f < MIN_WALL_MM:
                        continue
                    out.append((s0, p, s1, q))
                    covered.append((s0, s1))
                    used[j].append((s0, s1))
    return out


def _line_pairs(segments, f):
    """Axis-aligned parallel wall lines 50-600 mm apart overlapping >= 300 mm -> rectangles (file units)."""
    horizontal, vertical = [], []
    for (x0, y0), (x1, y1) in segments:
        if abs(y0 - y1) * f < 1 and abs(x1 - x0) * f >= MIN_WALL_MM:
            horizontal.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) * f < 1 and abs(y1 - y0) * f >= MIN_WALL_MM:
            vertical.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    rects = [(s0, p, s1, q) for s0, p, s1, q in _pair_runs(sorted(horizontal), f)]
    rects += [(p, s0, q, s1) for s0, p, s1, q in _pair_runs(sorted(vertical), f)]
    return rects


ANGLE_TOLERANCE_DEG = 0.3


def _angled_line_pairs(segments, f):
    """
    Parallel wall lines at any other angle (a slanted retaining wall), 50-600 mm apart -> four-corner outlines
    (file units). Lines within ANGLE_TOLERANCE_DEG of each other count as parallel.
    """
    slanted = []
    for (x0, y0), (x1, y1) in segments:
        length = math.hypot(x1 - x0, y1 - y0)
        if length * f < MIN_WALL_MM or abs(y0 - y1) * f < 1 or abs(x0 - x1) * f < 1:
            continue
        slanted.append((math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0, (x0, y0), (x1, y1)))
    slanted.sort()
    groups: list[list] = []
    for item in slanted:
        if groups and item[0] - groups[-1][-1][0] <= ANGLE_TOLERANCE_DEG:
            groups[-1].append(item)
        else:
            groups.append([item])
    quads = []
    for group in groups:
        if len(group) < 2:
            continue
        angle = math.radians(sum(g[0] for g in group) / len(group))
        d = (math.cos(angle), math.sin(angle))
        n = (-d[1], d[0])
        lines = []
        for _, a, b in group:
            pos = ((a[0] + b[0]) / 2) * n[0] + ((a[1] + b[1]) / 2) * n[1]
            s0, s1 = sorted((a[0] * d[0] + a[1] * d[1], b[0] * d[0] + b[1] * d[1]))
            lines.append((pos, s0, s1))
        for s0, p0, s1, p1 in _pair_runs(sorted(lines), f):
            quads.append([(s * d[0] + o * n[0], s * d[1] + o * n[1]) for s, o in ((s0, p0), (s1, p0), (s1, p1), (s0, p1))])
    return quads


def _plan_rects(poly, band: float = 400.0) -> list[tuple[float, float, float, float]]:
    """
    Small axis-aligned rectangles covering a slanted or curved outline, band by band (mm). The mesh keeps the
    true shape; slab outlines, room bounds and stair searches use these instead of the outline's bounding box,
    which for a long slanted wall would cover a whole room.
    """
    pts = _clean(poly)
    n = len(pts)
    ys = [p[1] for p in pts]
    y_lo, y_hi = min(ys), max(ys)
    rects = []
    y = y_lo
    while y < y_hi - 1e-6:
        y_next = min(y + band, y_hi)
        xs_band = []
        for scan in (y + 1e-3, (y + y_next) / 2, y_next - 1e-3):
            crossings = []
            for i in range(n):
                (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
                if (ay <= scan < by) or (by <= scan < ay):
                    crossings.append(ax + (scan - ay) * (bx - ax) / (by - ay))
            crossings.sort()
            xs_band += [(crossings[k], crossings[k + 1]) for k in range(0, len(crossings) - 1, 2)]
        xs_band += [(px, px) for px, py in pts if y <= py <= y_next]
        xs_band.sort()
        merged: list[list[float]] = []
        for a, b in xs_band:
            if merged and a <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        rects += [(a, y, b, y_next) for a, b in merged if b - a > 1.0]
        y = y_next
    return rects


def _overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _building_boxes(rects: list[tuple], tol: float = 3000.0, reach: float = 10000.0) -> list[tuple]:
    """
    Bounding boxes of the groups of touching walls and columns that make up the building: the largest group,
    and every group within `reach` of one already kept. A stray outline far from all of them (a site wall, a
    detail drawn beside the plan) is left out.
    """
    if not rects:
        return []
    parent = list(range(len(rects)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = 4000.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, r in enumerate(rects):
        for gx in range(int((r[0] - tol) // cell), int((r[2] + tol) // cell) + 1):
            for gy in range(int((r[1] - tol) // cell), int((r[3] + tol) // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    for members in grid.values():
        for a in range(len(members)):
            ra = rects[members[a]]
            for b in range(a + 1, len(members)):
                rb = rects[members[b]]
                if ra[0] - tol <= rb[2] and rb[0] - tol <= ra[2] and ra[1] - tol <= rb[3] and rb[1] - tol <= ra[3]:
                    i, j = find(members[a]), find(members[b])
                    if i != j:
                        parent[i] = j
    area: Counter = Counter()
    groups: dict[int, list[tuple]] = {}
    for i, r in enumerate(rects):
        root = find(i)
        area[root] += (r[2] - r[0]) * (r[3] - r[1])
        groups.setdefault(root, []).append(r)
    boxes = {root: (min(r[0] for r in g), min(r[1] for r in g), max(r[2] for r in g), max(r[3] for r in g))
             for root, g in groups.items()}
    kept = [area.most_common(1)[0][0]]
    changed = True
    while changed:
        changed = False
        for root, box in boxes.items():
            if root in kept:
                continue
            if any(box[0] - reach <= boxes[k][2] and boxes[k][0] - reach <= box[2] and box[1] - reach <= boxes[k][3]
                   and boxes[k][1] - reach <= box[3] for k in kept):
                kept.append(root)
                changed = True
    return [boxes[k] for k in kept]


def _texts(msp, f, depth_limit: int = 3) -> list[tuple[str, list[float]]]:
    """(text, bbox in mm, CAD y-up) for TEXT, MTEXT and block attributes, also inside blocks."""
    out: list[tuple[str, list[float]]] = []
    doc = msp.doc
    has_text_cache: dict[str, bool] = {}

    def block_has_text(name: str, depth: int = 0) -> bool:
        if name in has_text_cache:
            return has_text_cache[name]
        has_text_cache[name] = False
        block = doc.blocks.get(name) if doc is not None else None
        found = False
        if block is not None:
            for e in block:
                kind = e.dxftype()
                if kind in ("TEXT", "MTEXT", "ATTDEF") or (kind == "INSERT" and depth < depth_limit
                                                           and block_has_text(e.dxf.name, depth + 1)):
                    found = True
                    break
        has_text_cache[name] = found
        return found

    def add(item):
        kind = item.dxftype()
        if kind == "ATTDEF":
            return
        text = item.plain_text() if kind == "MTEXT" else item.dxf.text
        text = " ".join(str(text or "").split())
        if not text:
            return
        height = float(getattr(item.dxf, "char_height", None) or getattr(item.dxf, "height", 0) or 100.0)
        insert = item.dxf.insert
        width = 0.6 * height * len(text)
        x0, y0 = insert.x, insert.y
        if kind == "MTEXT":
            column = float(item.dxf.get("width", 0) or 0)
            if column > 0:
                width = min(width, column)
            ap = int(item.dxf.get("attachment_point", 1) or 1)        # 1 top-left ... 5 middle-centre ... 9 bottom-right
            x0 -= ((ap - 1) % 3) * width / 2
            y0 -= (1 - (ap - 1) // 3 / 2) * height
        else:
            halign, valign = int(item.dxf.get("halign", 0) or 0), int(item.dxf.get("valign", 0) or 0)
            if halign or valign:
                align = item.dxf.get("align_point", None) or insert
                x0 = align.x - (width / 2 if halign in (1, 4) else width if halign == 2 else 0)
                y0 = align.y - (height / 2 if valign == 2 else height if valign == 3 else 0)
        out.append((text, [x0 * f, y0 * f, (x0 + width) * f, (y0 + height) * f]))

    def walk(entities, depth):
        for e in entities:
            kind = e.dxftype()
            if kind == "INSERT":
                for attrib in e.attribs:
                    add(attrib)
                if depth < depth_limit and block_has_text(e.dxf.name):
                    try:
                        walk(e.virtual_entities(), depth + 1)
                    except Exception:            # a block that cannot be exploded keeps only its attributes
                        pass
            elif kind in ("TEXT", "MTEXT"):
                add(e)

    walk(msp, 0)
    return out


def _levels(texts):
    """Base and top slab levels (mm) from the file's own level text; (base, top, note)."""
    unit = next((u for u in (parse_level_unit_note(t) for t, _ in texts) if u), None)
    found: dict[str, Counter] = {}
    implied = False
    for text, _ in texts:
        for level in parse_level_annotations(text):
            if level.get("value_status") != "parsed":
                continue
            printed = str(level.get("printed", ""))
            value_unit = level.get("unit") or unit
            if not value_unit and re.search(r"\d\.\d", printed) and abs(level["value"]) < 300:
                value_unit, implied = "m", True        # +0.45: a level written with a decimal point is in metres
            if not value_unit:
                continue
            value = float(level["value"])
            if value > 0 and re.search(r"-\s*\d", printed):
                value = -value                         # the parser kept the digits but not a detached minus sign
            mm = value * (1000.0 if value_unit == "m" else 1.0)
            found.setdefault(level["label"].replace(".", "").replace(" ", "").upper(), Counter())[round(mm, 1)] += 1
    how = " (read in metres from the decimal point)" if implied else ""
    for label in ("SSL", "TOS", "TOC", "FFL", "RL"):
        counts = found.get(label)
        if not counts:
            continue
        base = max(counts, key=lambda v: (counts[v], -v))            # the level the plan prints most (lowest on a tie)
        above = sorted(v for v in counts if v > base)
        if above:
            return base, above[0], f"{label} {base / 1000:+.3f} to {above[0] / 1000:+.3f} printed in the file{how}"
        if label in ("SSL", "FFL"):
            return base, None, f"{label} {base / 1000:+.3f} printed in the file{how}; no level above it"
    return None, None, "no level printed in the file"


QUICK_SSL = re.compile(rb"\bS\.?\s?S\.?\s?L\b\.?\s*[=:@]?\s*([+-])?\s*(\d{1,3}\.\d{1,3})")


def quick_slab_level(path: Path, *, highest: bool = False) -> float | None:
    """
    The slab level (mm) a DXF prints most often (S.S.L= +0.45), read from the raw file without parsing it.
    highest: a roof plan's own level is the highest it prints (it often shows the floor below as an underlay).
    """
    counts: Counter = Counter()
    for match in QUICK_SSL.finditer(Path(path).read_bytes()):
        value = float(match.group(2)) * (-1 if match.group(1) == b"-" else 1)
        counts[round(value * 1000.0, 1)] += 1
    if not counts:
        return None
    if highest:
        return max(counts)
    return max(counts, key=lambda v: (counts[v], -v))


def is_roof_plan(title: str) -> bool:
    return bool(ROOF_TITLE.search(title or ""))


def _equipment_kind(name: str, layer: str):
    key = f"{name} {layer}"
    for kind, pattern, height, colour in EQUIP_KINDS:
        if pattern.search(key):
            return kind, height, colour
    return EQUIP_DEFAULT


def _drawn_equipment(msp, f, keep) -> list[dict[str, Any]]:
    """Equipment and furniture blocks: footprint as drawn (rotated box), kind from the block / layer name."""
    doc = msp.doc
    box_cache: dict[str, tuple | None] = {}

    def block_box(name):
        if name not in box_cache:
            block = doc.blocks.get(name)
            box = ezbbox.extents(block) if block is not None else None
            box_cache[name] = ((box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)
                               if box is not None and box.has_data else None)
        return box_cache[name]

    def size_of(box):
        return tuple(sorted((box[2] - box[0], box[3] - box[1])))

    # Named equipment blocks (also future ones) give their outline's kind to unnamed blocks of the same size
    # (a pump drawn as an anonymous copy of the "PRPUMP" block is a pump).
    named_sizes = []
    for insert in msp.query("INSERT"):
        short = insert.dxf.name.split("$0$")[-1]
        if EQUIP_NAME.search(short) and not short.startswith(("A$C", "*")):
            box = block_box(insert.dxf.name)
            if box is not None:
                named_sizes.append((size_of(box), short))

    def same_outline(box):
        a, b = size_of(box)
        for (c, d), short in named_sizes:
            if abs(a - c) <= 0.05 * c and abs(b - d) <= 0.05 * d:
                return short
        return None

    items = []
    for insert in msp.query("INSERT"):
        name, layer = insert.dxf.name, _own(insert.dxf.layer)
        short = name.split("$0$")[-1]
        if EQUIP_SKIP.search(layer) or EQUIP_SKIP.search(short):
            continue
        box = block_box(name)
        if box is None:
            continue
        like = None
        if not (EQUIP_LAYER.search(layer) or EQUIP_NAME.search(short)):
            if not (short.startswith(("A$C", "*")) and (layer == "0" or EQUIP_LAYER.search(layer))):
                continue
            like = same_outline(box)
            if like is None:
                continue
        matrix = insert.matrix44()
        corners = [matrix.transform((x, y, 0.0)) for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))]
        poly = [(c.x * f, c.y * f) for c in corners]
        sides = sorted((math.dist(poly[0], poly[1]), math.dist(poly[1], poly[2])))
        kind, height, colour = _equipment_kind(like or short, layer)
        fans = _fan_rings(insert, f) if kind == "cooling tower" else []
        longest = LARGE_EQUIPMENT_MM if kind in LARGE_EQUIPMENT else EQUIP_SIZE_MM[1]
        if sides[0] < EQUIP_SIZE_MM[0] or sides[1] > longest:
            continue
        cx, cy = sum(p[0] for p in poly) / 4, sum(p[1] for p in poly) / 4
        if not keep(cx, cy):
            continue
        items.append({"kind": kind, "height": height, "colour": colour, "poly": poly, "sides": sides, "fans": fans,
                      "block": short if like is None else f"{short} (same outline as {like})", "layer": layer})
    return items


def _fan_rings(insert, f) -> list[tuple[float, float, float]]:
    """A cooling tower's fan stacks: the largest of each set of concentric circles (radius >= 1 m) in its block."""
    circles = []

    def walk(entities, depth):
        for e in entities:
            if e.dxftype() == "CIRCLE" and e.dxf.radius * f >= 1000.0:
                circles.append((e.dxf.center.x * f, e.dxf.center.y * f, e.dxf.radius * f))
            elif e.dxftype() == "INSERT" and depth < 3:
                try:
                    walk(e.virtual_entities(), depth + 1)
                except Exception:              # a nested block that cannot be exploded adds no fans
                    continue

    try:
        walk(insert.virtual_entities(), 0)
    except Exception:
        return []
    rings: list[tuple[float, float, float]] = []
    for x, y, r in sorted(circles, key=lambda c: -c[2]):
        if not any(abs(x - a) < 300 and abs(y - b) < 300 for a, b, _ in rings):
            rings.append((x, y, r))
    return rings


def _cylinder(cx, cy, r, z0, z1, n: int = 32):
    """A closed vertical cylinder (building frame), as vertices and triangles."""
    ring = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    vertices = [[x, y, z0] for x, y in ring] + [[x, y, z1] for x, y in ring] + [[cx, cy, z0], [cx, cy, z1]]
    faces = []
    for k in range(n):
        j = (k + 1) % n
        faces += [[k, j, j + n], [k, j + n, k + n], [2 * n, j, k], [2 * n + 1, k + n, j + n]]
    return vertices, faces


def _equipment_mesh(poly_building, base, kind, height, colour, fans=()):
    vertices, faces, parts = [], [], []
    bands = [(0.0, PLINTH_MM, PLINTH_COLOUR), (PLINTH_MM, height, colour)] if kind in EQUIP_PLINTH else [(0.0, height, colour)]
    for z0, z1, part_colour in bands:
        v, fs = _prism(poly_building, base + z0, base + z1)
        offset = len(vertices)
        vertices += [[round(x, 1), round(y, 1), round(z, 1)] for x, y, z in v]
        shifted = [[offset + i for i in face] for face in fs]
        faces += shifted
        parts.append({"color": part_colour, "opacity": 1.0, "faces": shifted})
    for fx, fy, r in fans:                        # fan stacks stand on top of a cooling tower, where drawn
        v, fs = _cylinder(fx, fy, r, base + height, base + height + FAN_STACK_MM)
        offset = len(vertices)
        vertices += [[round(x, 1), round(y, 1), round(z, 1)] for x, y, z in v]
        shifted = [[offset + i for i in face] for face in fs]
        faces += shifted
        parts.append({"color": FAN_STACK_COLOUR, "opacity": 1.0, "faces": shifted})
    return {"vertices": vertices, "faces": faces}, parts


def _roof_plan_result(shared, title, model_id, base_mm, equipment) -> dict[str, Any]:
    """
    A roof plan is not a storey: its level closes the floor below. The equipment drawn on it (cooling towers,
    air-handling units, drives) stands on that roof; it is returned to be placed on the top floor's roof.
    """
    level = f"{base_mm / 1000:+.3f}" if base_mm is not None else "not printed"
    notes = [f"{title}: roof plan (level {level}); it closes the floor below."]
    roof_elements = []
    if base_mm is None:
        if equipment:
            notes.append(f"{len(equipment)} roof-top equipment block(s) not placed: the plan prints no roof level.")
    else:
        kinds: Counter = Counter()
        for k, item in enumerate(equipment):
            poly = [(x, -y) for x, y in item["poly"]]
            mesh, parts = _equipment_mesh(poly, base_mm, item["kind"], item["height"], item["colour"],
                                          [(x, -y, r) for x, y, r in item.get("fans") or []])
            kinds[item["kind"]] += 1
            roof_elements.append({
                "element_id": f"{model_id}:roof-equipment:{k}", "element_type": "furniture", "component": item["kind"],
                "detected_by": "cad_block", "wall_type": None,
                "dimensions_mm": {"length": round(item["sides"][1]), "thickness": round(item["sides"][0]),
                                  "height": item["height"]},
                "lod": {"category": "furniture", "minimum_lod": 500},
                "height_status": "display_only", "height_verification": None,
                "provenance": {"source": SOURCE, "file": title, "block": item["block"], "layer": item["layer"],
                               "footprint": "the block's outline as drawn on the roof plan",
                               "level": f"roof level {level} printed on the roof plan",
                               "basis": f"height {item['height']:.0f} mm typical for a {item['kind']}; not on the plan "
                                        "(display only)"},
                "opening": None, "between": None, "host_component": None,
                "drawing_id": model_id, "drawing_title": title, "mesh": mesh, "mesh_parts": parts,
                "reviewed_by": SOURCE, "is_external": True,
                "furniture": {"room": "roof", "room_type": item["kind"], "drawn": True},
            })
        if roof_elements:
            notes.append("Roof-top equipment: " + ", ".join(f"{v} {k}" for k, v in kinds.most_common())
                         + f" at roof level {level}; footprints as drawn, heights typical (display only). "
                         "Roof-top rooms and parapets on this plan are not built from it.")
    return {**shared, "storeys": [], "elements": [], "rooms": [], "stair_context": {},
            "roof_elements": roof_elements, "notes": notes}


def import_dxf(path: Path, *, model_id: str, title: str, top_mm: float | None = None,
               roof_plan: bool | None = None) -> dict[str, Any]:
    """
    One DXF floor plan as one storey. top_mm: the level above (the next floor's slab, from the other plans
    uploaded with it). roof_plan: the file is a roof plan; its level closes the storey below and its own
    geometry (parapets, roof-top structures) is not built as a storey.
    """
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    units = int(doc.header.get("$INSUNITS", 0) or 0)
    notes = []
    if units in UNIT_MM:
        f = UNIT_MM[units]
    else:
        f = 1.0
        notes.append("The file has no length unit; millimetres assumed (check the model's size).")
    roof_plan = is_roof_plan(title) if roof_plan is None else roof_plan

    texts = _texts(msp, f)
    base_mm, printed_top, level_note = _levels(texts)
    from app.services.envelope_details import grid_axes
    from app.services.roof_assembly import parapet_notes_from_texts

    entry: dict[str, Any] = {"labels": {}}
    for text, box in texts:
        drawing_text_facts.collect(entry, text, box)
        if GRID_TEXT.match(text):
            entry["labels"].setdefault(text, []).append(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    shared = {"source_type": "dxf", "model_id": model_id, "title": title, "units": "mm",
              "parapet_notes": parapet_notes_from_texts([t for t, _ in texts], drawing_id=model_id, drawing_title=title),
              "envelope_texts": entry.get("envelope_texts", []), "grid_axes": grid_axes(entry["labels"]),
              "base_mm": base_mm, "top_mm_hint": top_mm, "roof_plan": roof_plan}

    walls, columns, wall_lines, inserts = [], [], [], []
    skipped_layers: Counter = Counter()
    skipped_outlines = [0]

    def add_wall(poly, source):
        # A wall is long and thin: twice its area over its perimeter is about its thickness. A room, trench or
        # floor outline drawn on a wall layer is much thicker than any wall, so it is not one.
        area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                       for i in range(len(poly)))) / 2
        perimeter = sum(math.dist(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
        if 2 * area / max(perimeter, 1e-9) * f > MAX_OUTLINE_T_MM:
            skipped_outlines[0] += 1
            return
        (x0, y0, x1, y1), is_rect = _rect_of(poly)
        squared = None if is_rect else _square_chamfers(poly, 600.0 / f)
        pieces = [(x0, y0, x1, y1)] if is_rect else ((_rectilinear_pieces(squared) if squared else None) or None)
        if pieces is None:                                    # curved or slanted: keep the outline as drawn
            walls.append({"poly": [(x * f, y * f) for x, y in poly], "is_rect": False, "source": source})
            return
        for r in pieces:
            w, h = (r[2] - r[0]) * f, (r[3] - r[1]) * f
            if min(w, h) < MIN_T_MM or min(w, h) > MAX_OUTLINE_T_MM or max(w, h) < 2 * MIN_T_MM:
                continue
            walls.append({"poly": [(r[0] * f, r[1] * f), (r[2] * f, r[1] * f), (r[2] * f, r[3] * f), (r[0] * f, r[3] * f)],
                          "is_rect": True, "source": source})

    def entities():
        """Model space, with the content of large wall blocks (a structural-wall block inserted on a wall layer)."""
        for e in msp:
            yield e, _own(e.dxf.layer)
            if e.dxftype() != "INSERT":
                continue
            layer, short = _own(e.dxf.layer), e.dxf.name.split("$0$")[-1]
            if not WALL_LAYER.search(layer) or NOT_WALL_LAYER.search(layer) or EQUIP_NAME.search(short) \
                    or DOOR_NAME.search(short) or WINDOW_NAME.search(short) or NOT_WALL_BLOCK.search(short):
                continue
            try:
                box = ezbbox.extents(e.virtual_entities())
                if not box.has_data or max(box.extmax.x - box.extmin.x, box.extmax.y - box.extmin.y) * f < 3000:
                    continue
                for sub in e.virtual_entities():
                    if sub.dxftype() not in ("INSERT", "DIMENSION", "TEXT", "MTEXT"):
                        own = _own(sub.dxf.layer)
                        yield sub, (layer if own == "0" else own)
            except Exception:                      # a block that cannot be exploded is left as it is
                continue

    for e, layer in entities():
        kind = e.dxftype()
        if kind == "INSERT":
            name = e.dxf.name
            if MEP_LAYER.match(layer) or DOOR_TAG.search(layer) or DOOR_TAG.search(name):
                continue
            role = "door" if DOOR_NAME.search(name) or DOOR_NAME.search(layer) else \
                   "window" if WINDOW_NAME.search(name) or WINDOW_NAME.search(layer) else None
            if role:
                box = ezbbox.extents(e.virtual_entities())
                if box.has_data:
                    inserts.append({"role": role, "name": name.split("$0$")[-1],
                                    "bbox": [box.extmin.x * f, box.extmin.y * f, box.extmax.x * f, box.extmax.y * f]})
            continue
        if kind in ("DIMENSION", "TEXT", "MTEXT", "LEADER", "MULTILEADER"):
            continue
        if MEP_LAYER.match(layer):
            continue
        if COLUMN_LAYER.search(layer):
            for poly in _closed_shapes(e):
                if len(poly) < 3:
                    continue
                (x0, y0, x1, y1), _ = _rect_of(poly)
                a, b = sorted(((x1 - x0) * f, (y1 - y0) * f))
                if 150.0 <= b <= COLUMN_MAX_MM and b <= 3 * a:
                    columns.append({"poly": [(x * f, y * f) for x, y in poly]})
                elif WALL_LAYER.search(layer) or "WAL" in layer.upper():
                    add_wall(poly, layer)                         # a "columns & walls" layer: the long outlines are walls
            continue
        if WALL_LAYER.search(layer) and not NOT_WALL_LAYER.search(layer):
            shapes = _closed_shapes(e)
            if kind == "HATCH" and WALL_PATTERN_LAYER.search(layer):
                shapes = []                                 # pattern hatches repeat the outlines; skip them
            for poly in shapes:
                if len(poly) >= 3:
                    add_wall(poly, layer)
            wall_lines += _segments(e)
        elif WALL_LAYER.search(layer):
            skipped_layers[layer] += 1

    def footprint(w):
        return [_rect_of(w["poly"])[0]] if w["is_rect"] else _plan_rects(w["poly"])

    # Line pairs not already covered by a drawn outline or hatch.
    outline_rects = [r for w in walls for r in footprint(w)]
    for rect in _line_pairs(wall_lines, f):
        r = tuple(v * f for v in rect)
        area = (r[2] - r[0]) * (r[3] - r[1])
        if any(_overlap_area(r, o) >= 0.5 * area for o in outline_rects):
            continue
        walls.append({"is_rect": True, "source": "line pair",
                      "poly": [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]})
    # Slanted walls: parallel line pairs at other angles.
    outline_rects = [r for w in walls for r in footprint(w)]
    angled = 0
    for quad in _angled_line_pairs(wall_lines, f):
        poly = [(x * f, y * f) for x, y in quad]
        pieces = _plan_rects(poly)
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in pieces)
        if area and sum(_overlap_area(r, o) for r in pieces for o in outline_rects) >= 0.5 * area:
            continue
        walls.append({"is_rect": False, "source": "angled line pair", "poly": poly})
        angled += 1
    # Identical pieces (an outline drawn twice, or hatched and outlined) once.
    unique, seen = [], set()
    for w in walls:
        key = tuple(round(v / 5) for v in _rect_of(w["poly"])[0]) + (w["is_rect"],)
        if key not in seen:
            seen.add(key)
            unique.append(w)
    walls = unique
    unique_columns, seen = [], set()
    for c in columns:
        key = tuple(round(v / 5) for v in _rect_of(c["poly"])[0])
        if key not in seen:
            seen.add(key)
            unique_columns.append(c)
    columns = unique_columns

    # The building: the groups of touching walls and columns around the largest one. Far-off outlines are site walls.
    clusters = _building_boxes([_rect_of(w["poly"])[0] for w in walls] + [_rect_of(c["poly"])[0] for c in columns])
    dropped = 0
    if clusters:
        m = SITE_MARGIN_MM

        def keep(x, y):
            return any(c[0] - m <= x <= c[2] + m and c[1] - m <= y <= c[3] + m for c in clusters)

        def keep_rect(r):
            return any(r[0] < c[2] + m and c[0] - m < r[2] and r[1] < c[3] + m and c[1] - m < r[3] for c in clusters)

        before = len(walls) + len(columns)
        walls = [w for w in walls if keep_rect(_rect_of(w["poly"])[0])]
        columns = [c for c in columns if keep_rect(_rect_of(c["poly"])[0])]
        dropped = before - len(walls) - len(columns)
    else:
        def keep(x, y):
            return True

    top_source = "the next plan's level" if top_mm is not None else None
    top = top_mm if top_mm is not None else printed_top
    storey_height = (top - base_mm) if base_mm is not None and top is not None and 2000 <= top - base_mm <= 12000 else None
    if storey_height and top_source:
        level_note = f"{level_note}; top {top / 1000:+.3f} from {top_source}"
    height_status = "drawing_derived" if storey_height else "assumed_typical"
    wall_height = storey_height or ASSUMED_STOREY_MM
    base = base_mm or 0.0
    if not storey_height:
        notes.append(f"Storey height {ASSUMED_STOREY_MM:.0f} mm assumed ({level_note}).")

    placement_id = f"{model_id}:storey"
    storey = {"placement_id": placement_id, "base_level_key": f"SSL@{base:+.0f}mm" if base_mm is not None else "Model base",
              "base_elevation_mm": base}
    elements: list[dict[str, Any]] = []

    def to_building(vertices):
        return [[round(x, 1), round(-y, 1), round(z + base, 1)] for x, y, z in vertices]

    def add(element_id, kind, vertices, faces, component, min_lod, dims, extra=None):
        elements.append({
            "element_id": element_id, "element_type": kind, "component": component, "detected_by": "dxf",
            "reviewed_by": SOURCE, "dimensions_mm": dims, "mesh": {"vertices": to_building(vertices), "faces": faces},
            "lod": {"category": kind, "minimum_lod": min_lod}, "storey": storey, "drawing_id": model_id,
            "drawing_title": title, "height_status": height_status,
            "provenance": {"source": SOURCE, "file": title, "storey_height": level_note}, **(extra or {}),
        })

    for i, column in enumerate(columns):
        (x0, y0, x1, y1), _ = _rect_of(column["poly"])
        add(f"{model_id}:column:{i}", "column", _box(x0, y0, x1, y1, 0.0, wall_height), BOX_FACES, "column", 100,
            {"length": round(max(x1 - x0, y1 - y0), 1), "thickness": round(min(x1 - x0, y1 - y0), 1),
             "height": wall_height, "volume": 0.0})
    rect_walls = []
    for i, w in enumerate(walls):
        (x0, y0, x1, y1), is_rect = _rect_of(w["poly"])
        thickness = min(x1 - x0, y1 - y0)
        component = "partition wall" if thickness < 150 else "wall"
        element_id = f"{model_id}:wall:{i}"
        dims = {"length": round(max(x1 - x0, y1 - y0), 1), "thickness": round(thickness, 1), "height": wall_height, "volume": 0.0}
        if is_rect:
            add(element_id, "wall", _box(x0, y0, x1, y1, 0.0, wall_height), BOX_FACES, component, 300, dims)
            rect_walls.append({"id": element_id, "bbox": [x0, y0, x1, y1], "component": component})
        else:
            poly = w["poly"]
            area = abs(sum(poly[k][0] * poly[(k + 1) % len(poly)][1] - poly[(k + 1) % len(poly)][0] * poly[k][1]
                           for k in range(len(poly)))) / 2
            perimeter = sum(math.dist(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly)))
            half = perimeter / 2                         # a long rectangle: length + thickness = P / 2, length x thickness = A
            thickness = (half - math.sqrt(max(half * half - 4 * area, 0.0))) / 2
            dims = {"length": round(half - thickness, 1), "thickness": round(thickness, 1),
                    "height": wall_height, "volume": 0.0}
            component = "partition wall" if thickness < 150 else "wall"
            vertices, faces = _prism(poly, 0.0, wall_height)
            plan = [[round(r[0], 1), round(-r[3], 1), round(r[2], 1), round(-r[1], 1)] for r in _plan_rects(poly)]
            add(element_id, "wall", vertices, faces, component, 300, dims,
                extra={"plan_rects": plan, "plan_shape": "slanted or curved outline"})

    # Doors and windows: the gaps between walls, typed by the block inserted there.
    def classify(axis, a0, a1, c0, c1):
        # Door blocks are often inserted at the wall face and swing into the room, so look one wall
        # thickness either side of the gap (but not past its ends).
        reach = max(c1 - c0, 100.0)
        gap = (a0 + 1, c0 - reach, a1 - 1, c1 + reach) if axis == "h" else (c0 - reach, a0 + 1, c1 + reach, a1 - 1)
        for item in inserts:
            b = item["bbox"]
            if b[0] < gap[2] and gap[0] < b[2] and b[1] < gap[3] and gap[1] < b[3]:
                return item["role"], [f"{item['role']} block {item['name']}"]
        return "opening", []

    column_rects = [{"id": f"{model_id}:column:{i}", "bbox": list(_rect_of(c["poly"])[0])} for i, c in enumerate(columns)]
    counts = {"door": 0, "window": 0, "opening": 0}
    for opening in od.detect_openings(None, rect_walls, column_rects, millimetres_per_pdf_point=1.0, classifier=classify):
        heights = od.opening_heights(opening, entry, millimetres_per_pdf_point=1.0)
        element = od.build_opening_element(opening, heights, element_id=f"{model_id}:{opening['kind']}:{counts[opening['kind']]}",
                                           millimetres_per_pdf_point=1.0, wall_height_mm=wall_height)
        element.update({"detected_by": "wall_gap", "reviewed_by": SOURCE, "storey": storey, "drawing_id": model_id,
                        "drawing_title": title, "mesh": {"vertices": to_building(element["mesh"]["vertices"]),
                                                         "faces": element["mesh"]["faces"]}})
        elements.append(element)
        counts[opening["kind"]] += 1

    # Equipment and furniture drawn as blocks inside the building.
    equipment = _drawn_equipment(msp, f, keep)
    if roof_plan:
        return _roof_plan_result(shared, title, model_id, base_mm, equipment)
    kinds: Counter = Counter()
    for k, item in enumerate(equipment):
        poly = [(x, -y) for x, y in item["poly"]]
        mesh, parts = _equipment_mesh(poly, base, item["kind"], item["height"], item["colour"],
                                      [(x, -y, r) for x, y, r in item.get("fans") or []])
        kinds[item["kind"]] += 1
        elements.append({
            "element_id": f"{model_id}:equipment:{k}", "element_type": "furniture", "component": item["kind"],
            "detected_by": "cad_block", "wall_type": None,
            "dimensions_mm": {"length": round(item["sides"][1]), "thickness": round(item["sides"][0]), "height": item["height"]},
            "lod": {"category": "furniture", "minimum_lod": 500},
            "height_status": "display_only", "height_verification": None,
            "provenance": {"source": SOURCE, "file": title, "block": item["block"], "layer": item["layer"],
                           "footprint": "the block's outline as drawn in the CAD file",
                           "basis": f"height {item['height']:.0f} mm typical for a {item['kind']}; not on the plan (display only)"},
            "opening": None, "between": None, "host_component": None,
            "drawing_id": model_id, "drawing_title": title, "storey": storey,
            "mesh": mesh, "mesh_parts": parts, "reviewed_by": SOURCE, "is_external": False,
            "furniture": {"room": None, "room_type": item["kind"], "drawn": True},
        })

    rooms = []
    sizes = entry.get("sizes") or []
    for name, box in entry.get("rooms") or []:
        height = max(box[3] - box[1], 1.0)
        size = next(([w, d] for (w, d), sbox in sizes
                     if 0 <= box[1] - sbox[3] <= 3 * height and abs(sbox[0] - box[0]) <= 3 * height), None)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        rooms.append({"name": name, "size_mm": size, "position_mm": [round(cx, 1), round(-cy, 1), base],
                      "storey": storey, "drawing_id": model_id, "source": "room label in the CAD file"})
    if rect_walls:
        xs = [v for w in rect_walls for v in (w["bbox"][0], w["bbox"][2])]
        ys = [v for w in rect_walls for v in (w["bbox"][1], w["bbox"][3])]
        rooms = [r for r in rooms if min(xs) < r["position_mm"][0] < max(xs) and min(ys) < -r["position_mm"][1] < max(ys)]
    unique_rooms, seen = [], set()
    for r in rooms:                                          # the same label drawn twice (sheet + xref)
        key = (r["name"], round(r["position_mm"][0] / 500), round(r["position_mm"][1] / 500))
        if key not in seen:
            seen.add(key)
            unique_rooms.append(r)
    rooms = unique_rooms

    # Lines near the stair and lift labels, for the stair and lift readers (building frame, mm).
    from app.services.stair_detection import LIFT_WORDS, STAIR_WORDS

    anchors = [(r["position_mm"][0], r["position_mm"][1]) for r in rooms
               if any(w in r["name"] for w in STAIR_WORDS + LIFT_WORDS)]
    lift_anchors = [(r["position_mm"][0], r["position_mm"][1]) for r in rooms if any(w in r["name"] for w in LIFT_WORDS)]

    def near(x, y, points):
        return any(abs(x - ax) <= STAIR_REACH_MM and abs(y - ay) <= STAIR_REACH_MM for ax, ay in points)

    def block_lines(entities, depth):
        for sub in entities:
            kind = sub.dxftype()
            if kind in ("LINE", "LWPOLYLINE"):
                yield sub
            elif kind == "INSERT" and depth < 2 and not MEP_LAYER.match(_own(sub.dxf.layer)):
                try:
                    yield from block_lines(sub.virtual_entities(), depth + 1)
                except Exception:                  # a block that cannot be exploded adds nothing
                    continue

    def line_sources():
        for e in msp.query("LINE LWPOLYLINE"):
            if not MEP_LAYER.match(_own(e.dxf.layer)):
                yield e, anchors
        # A lift is often a block (shaft, car and doors): its lines count near a LIFT label only.
        if lift_anchors:
            for insert in msp.query("INSERT"):
                if MEP_LAYER.match(_own(insert.dxf.layer)) or EQUIP_NAME.search(insert.dxf.name.split("$0$")[-1]):
                    continue
                try:
                    for sub in block_lines(insert.virtual_entities(), 1):
                        yield sub, lift_anchors
                except Exception:
                    continue

    stair_lines = []
    if anchors:
        for e, around in line_sources():
            for (x0, y0), (x1, y1) in _segments(e, closed_too=True):
                x0, y0, x1, y1 = x0 * f, -y0 * f, x1 * f, -y1 * f
                if not near((x0 + x1) / 2, (y0 + y1) / 2, around):
                    continue
                if abs(y0 - y1) <= 1 and abs(x1 - x0) >= 300:
                    stair_lines.append(["h", round((y0 + y1) / 2, 1), round(min(x0, x1), 1), round(max(x0, x1), 1)])
                elif abs(x0 - x1) <= 1 and abs(y1 - y0) >= 300:
                    stair_lines.append(["v", round((x0 + x1) / 2, 1), round(min(y0, y1), 1), round(max(y0, y1), 1)])
    arrows = [[text, round((box[0] + box[2]) / 2, 1), round(-(box[1] + box[3]) / 2, 1)]
              for text, box in entry.get("stair_arrows") or []]

    storey_row = {
        "placement_id": placement_id, "drawing_id": model_id, "drawing_title": title, "page_number": 1,
        "base_level_key": storey["base_level_key"], "base_elevation_mm": base,
        "top_level_key": f"SSL@{top:+.0f}mm" if storey_height else None,
        "storey_height_mm": storey_height or None, "reference_label": "CAD drawing origin",
        "walls_served": len(elements), "walls_withheld": 0,
    }
    notes.insert(0, f"{len(walls)} walls, {len(columns)} columns, {counts['door']} doors, {counts['window']} windows, "
                    f"{len(equipment)} drawn equipment / furniture blocks and {len(rooms)} rooms read from {title}; "
                    f"heights: {level_note}.")
    if dropped:
        notes.append(f"{dropped} wall / column outline(s) away from the building (site walls) left out.")
    if angled:
        notes.append(f"{angled} slanted wall run(s) read from parallel lines at an angle.")
    if skipped_outlines[0]:
        notes.append(f"{skipped_outlines[0]} closed outline(s) on wall layers are far thicker than a wall (room, trench or "
                     "floor outlines); not read as walls.")
    if skipped_layers:
        notes.append("Not read as walls (site, pattern or annotation layers): "
                     + ", ".join(sorted(skipped_layers)[:6]) + ("..." if len(skipped_layers) > 6 else "") + ".")
    if equipment:
        notes.append("Equipment from the drawn blocks: " + ", ".join(f"{v} {k}" for k, v in kinds.most_common())
                     + "; footprints as drawn, heights typical (display only).")
    return {**shared, "storeys": [storey_row], "elements": elements, "rooms": rooms, "notes": notes,
            "stair_context": {"lines": stair_lines, "arrows": arrows}}
