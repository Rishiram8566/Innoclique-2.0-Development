"""
LOD 500 furniture and fittings, laid out by room type from the drawing's room labels.

The room comes from the drawing: its label (LIVING, BEDROOM, TOILET, KITCHEN ...),
its walls (the nearest walls, columns and openings round the label) and the doors
and windows in those walls. The furniture is indicative: typical sizes, placed by
simple layout rules. Every piece says so, because no drawing gives these sizes.

* Living: sofa against a wall with no door (avoiding windows where possible), a
  coffee table on a rug in front, a TV unit and screen on the opposite wall,
  armchairs when there is room, a painting, and curtains on the windows.
* Dining: a table with chairs, centred.
* Bedroom: a bed (double from 3 m wide, else single) with its headboard on a wall
  with no door, bedside tables, a wardrobe on another free wall, a painting above
  the bed, and curtains.
* Toilet and bath: a WC on a wall away from the door, a washbasin with a mirror.
* Kitchen: base units and worktop along the longest free wall (L-shaped when the
  next wall is free), wall cupboards away from windows, a hob and hood, a sink
  (under a window if one is there), and a refrigerator.
* Dress and wardrobe rooms get wardrobes; utility and laundry a washing machine;
  office and study a desk and chair; meeting rooms a table and chairs; reception a
  sofa and desk.

Furniture keeps clear of door swings, stairs, lifts and columns, and stays inside
the room. A piece that does not fit is left out.
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Any

from app.services.stair_detection import room_bounds

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]
ROOM_TYPES = [
    # Full names, common abbreviations (KIT, T&B, M.BED, LVG ...) and regional names (MAJLIS, D/R = drawing room).
    ("toilet", re.compile(r"\b(?:TOILET|BATH(?:ROOM)?|BTH|W\.?C|WASH ?ROOM|POWDER(?: ROOM)?|T ?& ?B|T/B|TLT|TOI|"
                          r"LAV(?:ATORY)?|RESTROOM|EN-?SUITE|SHOWER)\b")),
    ("kitchen", re.compile(r"\b(?:KITCHEN(?:ETTE)?|KITCH|KIT|PANTRY)\b")),
    # BR only with a number or prefix (BR 2, BR-1, M.BR): a bare "BR" is often another room (battery room).
    ("bedroom", re.compile(r"\b(?:BED ?ROOM|BED|BR ?-?\d+|M\.?BR|BDR|BDRM|MBR|M\.?B\.?R|GUEST ROOM|NURSERY|"
                           r"KIDS(?: ROOM)?|CHILDREN|MAIDS? ROOM)\b")),
    ("living", re.compile(r"\b(?:LIVING|LVG|LIV|LOUNGE|FAMILY|DRAWING ROOM|D/R|D\.R|SITTING|SIT ?OUT|MAJLIS|DEN|"
                          r"TV ROOM)\b")),
    ("dining", re.compile(r"\b(?:DINING|DNG|DIN)\b")),
    ("dress", re.compile(r"\b(?:DRESS|WARDROBE|CLOSET|WIC|W\.I\.C|WALK-?IN)\b")),
    ("utility", re.compile(r"\b(?:UTILITY|UTL|LAUNDRY|WASH AREA)\b")),
    ("office", re.compile(r"\b(?:OFFICE|STUDY|CABIN|LIBRARY|WORK ?ROOM)\b")),
    ("meeting", re.compile(r"\b(?:MEETING|CONFERENCE|BOARD ?ROOM)\b")),
    ("reception", re.compile(r"\b(?:RECEPTION|WAITING)\b")),
]
# "HALL" is the living room on many residential plans, but LIFT HALL, CHILLER HALL or ENTRANCE HALL are not:
# it counts only when every other word on the label is one of these.
HALL_COMPANIONS = {"HALL", "MAIN", "CENTRAL", "LIVING", "FAMILY", "DRAWING", "DINING", "CUM", "AND", "ROOM"}
ROOM_SIZE_MM = (1000.0, 15000.0)
MINOR_DRAWN = {"hose reel cabinet", "chair", "desk"}   # drawn in passing; they do not stand for the room's equipment
PLANT_ROOM_MAX_MM = 40000.0     # chiller halls, generator and pump rooms run larger than any habitable room
FACE_TOLERANCE_MM = 120.0
DOOR_MARGIN_MM = 150.0
DOOR_CLEAR_MM = 1000.0          # kept clear inside a door (its swing) or a wide opening
BASIS = "indicative: typical size, placed by room type; not shown on the drawing"

C = {
    "fabric": "#5d6b7d", "cushion": "#c9b79c", "wood": "#7a5234", "wood_light": "#b98c5c", "white": "#f2f0ea",
    "black": "#1f2124", "steel": "#b9bec4", "granite": "#34353a", "sheet": "#ece7dc", "pillow": "#fbfaf7",
    "blanket": "#6f839c", "rug": "#9c4f36", "screen": "#0d1014", "ceramic": "#f7f7f4", "mirror": "#cfe3ee",
    "curtain": "#cdbb9c", "frame": "#2e2a26", "chair": "#3f4a55", "lamp": "#f4e6c4", "cabinet": "#d9d2c5",
}
ART = ["#d98c5f", "#5f8fd9", "#7fb07a", "#c9a227", "#9a6fb0"]


def _types():
    from app.services import equipment

    return equipment.ROOM_TYPES + ROOM_TYPES


def _layouts():
    from app.services import equipment

    return {**LAYOUTS, **equipment.LAYOUTS}


def room_types(name: str) -> list[str]:
    upper = (name or "").upper()
    from app.services.equipment import NOT_HERE

    if NOT_HERE.search(upper):                   # GENERATOR ROOM BELOW: a pointer to another level, not a room
        return []
    kinds = []
    for kind, pattern in _types():
        if pattern.search(upper) and kind not in kinds:
            kinds.append(kind)
    words = set(re.findall(r"[A-Z]+", upper))
    if "HALL" in words and words <= HALL_COMPANIONS and "living" not in kinds:
        kinds.insert(0, "living")
    # A toilet label that names the room it serves (MASTER BEDROOM TOILET, ATTACHED T&B) is a toilet.
    if "toilet" in kinds:
        kinds = [k for k in kinds if k not in ("bedroom", "living", "dining", "dress")]
    return kinds


# ---------------------------------------------------------------------------
# A room in building mm, and its walls as local frames (u along the wall, v into the room)
# ---------------------------------------------------------------------------

class Room:
    SIDES = ("n", "s", "w", "e")

    def __init__(self, rect, openings, blockers):
        self.x0, self.y0, self.x1, self.y1 = rect
        self.openings = openings            # side -> [(kind, u0, u1, head_mm or None, sill_mm or None)]
        self.blockers = list(blockers)      # world rects nothing may overlap
        self.placed: list[tuple[float, float, float, float]] = []

    def length(self, side):
        return (self.x1 - self.x0) if side in ("n", "s") else (self.y1 - self.y0)

    def depth(self, side):
        return (self.y1 - self.y0) if side in ("n", "s") else (self.x1 - self.x0)

    def world(self, side, u, v):
        if side == "n":
            return self.x0 + u, self.y0 + v
        if side == "s":
            return self.x0 + u, self.y1 - v
        if side == "w":
            return self.x0 + v, self.y0 + u
        return self.x1 - v, self.y0 + u

    def rect(self, side, u0, u1, v0, v1):
        (ax, ay), (bx, by) = self.world(side, u0, v0), self.world(side, u1, v1)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def opposite(self, side):
        return {"n": "s", "s": "n", "w": "e", "e": "w"}[side]

    def neighbours(self, side):
        return ("w", "e") if side in ("n", "s") else ("n", "s")

    def door_zones(self):
        zones = []
        for side in self.SIDES:
            for kind, u0, u1, _, _ in self.openings.get(side, []):
                if kind in ("door", "opening"):
                    zones.append(self.rect(side, u0 - 50, u1 + 50, 0, min(max(u1 - u0, 600), DOOR_CLEAR_MM)))
        return zones

    def fits(self, rect, *, allow=()) -> bool:
        x0, y0, x1, y1 = rect
        if x0 < self.x0 - 1 or y0 < self.y0 - 1 or x1 > self.x1 + 1 or y1 > self.y1 + 1:
            return False
        for other in self.blockers + self.door_zones() + [p for p in self.placed if p not in allow]:
            if x0 < other[2] - 1 and other[0] < x1 - 1 and y0 < other[3] - 1 and other[1] < y1 - 1:
                return False
        return True

    def free_spans(self, side, avoid=("door", "opening")):
        """Stretches of a wall with no door (and, if asked, no window), as (u0, u1)."""
        cuts = sorted((u0 - DOOR_MARGIN_MM, u1 + DOOR_MARGIN_MM) for kind, u0, u1, _, _ in self.openings.get(side, [])
                      if kind in avoid)
        spans, start = [], 0.0
        for a, b in cuts:
            if a > start:
                spans.append((start, a))
            start = max(start, b)
        if start < self.length(side):
            spans.append((start, self.length(side)))
        return [s for s in spans if s[1] - s[0] > 0]

    def slot(self, side, width, depth, *, avoid=("door", "opening"), prefer=None) -> float | None:
        """Centre (u) of a free stretch that takes `width`, nearest `prefer` (default: the wall's middle)."""
        target = self.length(side) / 2 if prefer is None else prefer
        best = None
        for a, b in self.free_spans(side, avoid):
            if b - a < width:
                continue
            u = min(max(target, a + width / 2), b - width / 2)
            if not self.fits(self.rect(side, u - width / 2, u + width / 2, 0, depth)):
                continue
            if best is None or abs(u - target) < abs(best - target):
                best = u
        return best

    def best_wall(self, width, depth, *, avoid=("door", "opening"), shun=("window",), exclude=()) -> tuple | None:
        """The wall that takes a piece best: free of doors, preferably of windows too, longest free stretch."""
        options = []
        for side in self.SIDES:
            if side in exclude or self.depth(side) < depth + 300:
                continue
            for avoid_set, penalty in ((tuple(avoid) + tuple(shun), 0), (tuple(avoid), 1)):
                u = self.slot(side, width, depth, avoid=avoid_set)
                if u is not None:
                    longest = max((b - a for a, b in self.free_spans(side, avoid_set)), default=0)
                    options.append((penalty, -longest, side, u))
                    break
        if not options:
            return None
        options.sort()
        return options[0][2], options[0][3]


# ---------------------------------------------------------------------------
# Pieces: boxes in a wall's local frame (u along, v into the room, z up from the floor)
# ---------------------------------------------------------------------------

class Piece:
    def __init__(self, name, room: Room, side: str, size):
        self.name, self.room, self.side, self.size = name, room, side, size
        self.boxes: list[tuple] = []        # (u0, u1, v0, v1, z0, z1, colour key or hex, opacity)
        self.cyls: list[tuple] = []         # equipment cylinders (see equipment.cylinder_points)

    def add(self, u0, u1, v0, v1, z0, z1, colour, opacity=1.0):
        self.boxes.append((u0, u1, v0, v1, z0, z1, colour, opacity))
        return self

    def footprint(self):
        u0 = min(b[0] for b in self.boxes); u1 = max(b[1] for b in self.boxes)
        v0 = min(b[2] for b in self.boxes); v1 = max(b[3] for b in self.boxes)
        return self.room.rect(self.side, u0, u1, v0, v1)


def sofa(room, side, u, width=2100.0, depth=850.0, colour="fabric"):
    p = Piece("sofa", room, side, (width, depth, 800))
    a, b = u - width / 2, u + width / 2
    p.add(a, b, 0, depth, 0, 420, colour)                       # base
    p.add(a, b, 0, 220, 420, 800, colour)                       # back
    p.add(a, a + 160, 220, depth, 420, 620, colour)             # arms
    p.add(b - 160, b, 220, depth, 420, 620, colour)
    seats = max(1, round((width - 320) / 600))
    seat_w = (width - 320) / seats
    for k in range(seats):
        s0 = a + 160 + k * seat_w
        p.add(s0 + 10, s0 + seat_w - 10, 230, depth - 20, 420, 490, "cushion")
    return p


def coffee_table(room, side, u, v0, width=1100.0, depth=550.0):
    p = Piece("coffee table", room, side, (width, depth, 440))
    a, b = u - width / 2, u + width / 2
    p.add(a, b, v0, v0 + depth, 400, 440, "wood")
    for (x, y) in ((a + 40, v0 + 40), (b - 80, v0 + 40), (a + 40, v0 + depth - 80), (b - 80, v0 + depth - 80)):
        p.add(x, x + 40, y, y + 40, 0, 400, "black")
    return p


def rug(room, side, u, v0, width, depth, colour="rug"):
    return Piece("rug", room, side, (width, depth, 10)).add(u - width / 2, u + width / 2, v0, v0 + depth, 0, 10, colour)


def tv_unit(room, side, u, width=1800.0):
    p = Piece("tv unit", room, side, (width, 450, 1650))
    p.add(u - width / 2, u + width / 2, 0, 450, 0, 500, "wood_light")
    p.add(u - 650, u + 650, 0, 50, 950, 1680, "screen")         # wall-mounted screen
    return p


def armchair(room, side, u, v0, colour="fabric"):
    p = Piece("armchair", room, side, (800, 800, 800))
    p.add(u - 400, u + 400, v0, v0 + 800, 0, 420, colour)
    p.add(u - 400, u + 400, v0, v0 + 180, 420, 800, colour)
    p.add(u - 380, u + 380, v0 + 190, v0 + 780, 420, 480, "cushion")
    return p


def bed(room, side, u, width):
    p = Piece("bed", room, side, (width, 2050, 1100))
    a, b = u - width / 2, u + width / 2
    p.add(a, b, 0, 80, 0, 1100, "wood")                         # headboard
    p.add(a, b, 80, 2050, 0, 350, "wood")                       # frame
    p.add(a + 20, b - 20, 90, 2040, 350, 560, "sheet")          # mattress
    pillows = 2 if width > 1200 else 1
    pw = (width - 120) / pillows
    for k in range(pillows):
        s0 = a + 60 + k * pw
        p.add(s0 + 20, s0 + pw - 20, 120, 470, 560, 660, "pillow")
    p.add(a + 10, b - 10, 900, 2050, 560, 590, "blanket")       # blanket over the lower part
    return p


def bedside_table(room, side, u):
    p = Piece("bedside table", room, side, (450, 400, 800))
    p.add(u - 225, u + 225, 0, 400, 0, 500, "wood")
    p.add(u - 90, u + 90, 110, 290, 500, 540, "black")          # lamp base
    p.add(u - 120, u + 120, 80, 320, 540, 800, "lamp")          # shade
    return p


def wardrobe(room, side, u, width):
    p = Piece("wardrobe", room, side, (width, 600, 2100))
    a, b = u - width / 2, u + width / 2
    p.add(a, b, 0, 600, 0, 2100, "wood_light")
    doors = max(2, round(width / 600))
    for k in range(1, doors):
        x = a + k * width / doors
        p.add(x - 4, x + 4, 600, 606, 30, 2070, "wood")           # door joints
        p.add(x - 30, x - 14, 600, 625, 950, 1250, "black")       # handles
        p.add(x + 14, x + 30, 600, 625, 950, 1250, "black")
    return p


def clear_of_windows(room, side, u, width, z0, z1) -> bool:
    """No window on this wall behind a wall-mounted piece at this height."""
    for w0, w1, head, sill in _windows(room, side):
        if w1 > u - width / 2 and w0 < u + width / 2 and (sill or 0) < z1 and (head or 2100) > z0:
            return False
    return True


def painting(room, side, u, z0, seed, width=900.0, height=600.0):
    p = Piece("painting", room, side, (width, 40, height))
    p.add(u - width / 2, u + width / 2, 0, 30, z0, z0 + height, "frame")
    p.add(u - width / 2 + 50, u + width / 2 - 50, 30, 40, z0 + 50, z0 + height - 50, ART[seed % len(ART)])
    return p


def curtains(room, side, u0, u1, head):
    """Two drawn-back panels and a rod across a window, just inside the wall."""
    p = Piece("curtains", room, side, (u1 - u0 + 300, 80, head + 200))
    top = head + 200
    panel = max(250.0, (u1 - u0) * 0.28)
    p.add(u0 - 150, u0 - 150 + panel, 60, 120, 100, top - 40, "curtain")
    p.add(u1 + 150 - panel, u1 + 150, 60, 120, 100, top - 40, "curtain")
    p.add(u0 - 200, u1 + 200, 80, 100, top - 40, top - 20, "steel")
    return p


def wc(room, side, u):
    p = Piece("wc", room, side, (400, 700, 800))
    p.add(u - 225, u + 225, 0, 180, 380, 800, "ceramic")       # cistern
    p.add(u - 170, u + 170, 180, 620, 0, 380, "ceramic")       # pan
    p.add(u - 190, u + 190, 170, 680, 380, 410, "white")       # seat
    return p


def washbasin(room, side, u):
    p = Piece("washbasin", room, side, (550, 450, 2000))
    p.add(u - 100, u + 100, 0, 220, 0, 800, "ceramic")         # pedestal
    p.add(u - 275, u + 275, 0, 450, 800, 870, "ceramic")       # basin
    p.add(u - 20, u + 20, 40, 160, 870, 1000, "steel")         # tap
    p.add(u - 300, u + 300, 0, 15, 1200, 2000, "mirror", 0.7)  # mirror
    return p


def counter(room, side, u0, u1, *, uppers=True, window_spans=()):
    p = Piece("kitchen counter", room, side, (u1 - u0, 600, 2150))
    p.add(u0, u1, 0, 580, 100, 850, "cabinet")                 # base units
    p.add(u0, u1, 30, 560, 0, 100, "black")                    # plinth
    p.add(u0, u1, 0, 620, 850, 890, "granite")                 # worktop
    doors = max(1, round((u1 - u0) / 600))
    for k in range(1, doors):
        x = u0 + k * (u1 - u0) / doors
        p.add(x - 3, x + 3, 580, 584, 120, 830, "wood")
    if uppers:
        a = u0
        for w0, w1 in sorted(window_spans) + [(u1, u1)]:
            b = min(w0 - 50, u1)
            if b - a >= 400:
                p.add(a, b, 0, 350, 1450, 2150, "cabinet")
            a = max(a, w1 + 50)
    return p


def hob_and_hood(room, side, u):
    p = Piece("hob and hood", room, side, (600, 520, 2150))
    p.add(u - 300, u + 300, 40, 560, 890, 900, "black")
    p.add(u - 300, u + 300, 0, 500, 1650, 1750, "steel")      # hood canopy
    p.add(u - 120, u + 120, 0, 250, 1750, 2150, "steel")      # flue
    return p


def kitchen_sink(room, side, u):
    p = Piece("kitchen sink", room, side, (800, 500, 1100))
    p.add(u - 400, u + 400, 50, 550, 885, 895, "steel")
    p.add(u - 20, u + 20, 60, 180, 895, 1100, "steel")         # tap
    return p


def fridge(room, side, u):
    return Piece("refrigerator", room, side, (700, 700, 1800)).add(u - 350, u + 350, 0, 700, 0, 1800, "steel") \
        .add(u + 250, u + 280, 700, 730, 900, 1500, "black")


def dining_set(room, side, u, v_mid, length, width):
    p = Piece("dining table", room, side, (length, width, 780))
    a, b, c0, c1 = u - length / 2, u + length / 2, v_mid - width / 2, v_mid + width / 2
    p.add(a, b, c0, c1, 740, 780, "wood")
    for (x, y) in ((a + 50, c0 + 50), (b - 110, c0 + 50), (a + 50, c1 - 110), (b - 110, c1 - 110)):
        p.add(x, x + 60, y, y + 60, 0, 740, "wood")
    seats = max(1, int(length // 650))
    chairs = []
    for k in range(seats):
        x = a + (k + 0.5) * length / seats
        for side_v, back in ((c0 - 480, c0 - 480), (c1 + 30, c1 + 410)):
            chair = Piece("chair", room, side, (450, 450, 900))
            chair.add(x - 225, x + 225, side_v, side_v + 450, 0, 470, "chair")
            chair.add(x - 225, x + 225, back, back + 40, 470, 900, "chair")
            chairs.append(chair)
    return [p] + chairs


def washing_machine(room, side, u):
    return Piece("washing machine", room, side, (600, 600, 850)).add(u - 300, u + 300, 0, 600, 0, 850, "white") \
        .add(u - 180, u + 180, 600, 610, 300, 660, "black")


def desk(room, side, u, width=1400.0):
    p = Piece("desk", room, side, (width, 700, 1150))
    p.add(u - width / 2, u + width / 2, 0, 700, 720, 750, "wood")
    p.add(u - width / 2, u - width / 2 + 40, 0, 700, 0, 720, "wood")
    p.add(u + width / 2 - 40, u + width / 2, 0, 700, 0, 720, "wood")
    p.add(u - 280, u + 280, 80, 110, 800, 1150, "screen")
    chair = Piece("office chair", room, side, (550, 550, 1000))
    chair.add(u - 260, u + 260, 800, 1320, 0, 480, "chair").add(u - 240, u + 240, 1280, 1330, 480, 1000, "chair")
    return [p, chair]


def reception_desk(room, side, u, width=1800.0):
    return Piece("reception desk", room, side, (width, 700, 1100)).add(u - width / 2, u + width / 2, 0, 700, 0, 1100,
                                                                         "wood_light")


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def _try(room: Room, pieces: list[Piece], out: list[Piece], allow=(), mounted: bool = False) -> bool:
    """Place pieces if they fit. Wall-mounted pieces (paintings, curtains) may hang over furniture."""
    rects = [p.footprint() for p in pieces]
    if all(room.fits(r, allow=list(room.placed) if mounted else allow) for r in rects):
        out.extend(pieces)
        if not mounted:
            room.placed.extend(rects)
        return True
    return False


def _windows(room: Room, side: str):
    return [(u0, u1, head, sill) for kind, u0, u1, head, sill in room.openings.get(side, []) if kind == "window"]


def _curtains(room, out):
    for side in room.SIDES:
        for u0, u1, head, _ in _windows(room, side):
            _try(room, [curtains(room, side, u0, u1, head or 2100)], out, mounted=True)


def furnish_living(room, out, seed):
    width = 2100 if max(room.length(s) for s in room.SIDES) >= 3200 else 1600
    wall = room.best_wall(width, 850)
    if wall:
        side, u = wall
        if _try(room, [sofa(room, side, u, width)], out):
            depth = room.depth(side)
            if depth >= 2000:
                rug_piece = rug(room, side, u, 900, min(2400, width + 300), min(1700, depth - 1500))
                _try(room, [rug_piece], out)
                _try(room, [coffee_table(room, side, u, 1250)], out, allow=[rug_piece.footprint()])
            if depth >= 3000:
                far = room.opposite(side)
                u_far = room.slot(far, 1800, 450, avoid=("door", "opening", "window"), prefer=u)
                if u_far is None:                      # no clear wall: in front of a window rather than nothing
                    u_far = room.slot(far, 1800, 450, prefer=u)
                if u_far is not None:
                    _try(room, [tv_unit(room, far, u_far)], out)
            for du in (-(width / 2 + 550), width / 2 + 550):
                _try(room, [armchair(room, side, u + du, 1150)], out, allow=room.placed[1:2])
            # A painting on a side wall, else above the sofa.
            for wall_side in room.neighbours(side):
                v = room.slot(wall_side, 900, 40, avoid=("door", "opening", "window"))
                if v is not None and _try(room, [painting(room, wall_side, v, 1400, seed)], out, mounted=True):
                    break
            else:
                if clear_of_windows(room, side, u, 900, 1300, 1900):
                    _try(room, [painting(room, side, u, 1300, seed)], out, mounted=True)
    _curtains(room, out)


def furnish_dining(room, out, seed):
    side = "n" if room.length("n") >= room.length("w") else "w"
    length, depth = room.length(side), room.depth(side)
    table_l = 1600 if length >= 3000 else 1200
    table_w = 900 if depth >= 2600 else 800
    if length >= table_l + 600 and depth >= table_w + 1500:
        _try(room, dining_set(room, side, length / 2, depth / 2, table_l, table_w), out)


def furnish_bedroom(room, out, seed):
    double = min(room.length("n"), room.length("w")) >= 3000
    width = 1800 if double else 1000
    wall = room.best_wall(width + 900, 2100) or room.best_wall(width, 2100)
    if not wall:
        return
    side, u = wall
    if not _try(room, [bed(room, side, u, width)], out):
        return
    for du in (-(width / 2 + 240), width / 2 + 240):
        _try(room, [bedside_table(room, side, u + du)], out)
    # A painting above the bed, unless a window is there: then on the nearest clear wall.
    art = min(1200, width)
    if not (clear_of_windows(room, side, u, art, 1250, 1850)
            and _try(room, [painting(room, side, u, 1250, seed, width=art, height=600)], out, mounted=True)):
        for other in room.neighbours(side):
            v = room.slot(other, 900, 40, avoid=("door", "opening", "window"))
            if v is not None and _try(room, [painting(room, other, v, 1400, seed)], out, mounted=True):
                break
    for other in [room.opposite(side)] + list(room.neighbours(side)):
        length = max((b - a for a, b in room.free_spans(other, ("door", "opening", "window"))), default=0)
        w = min(1800.0, length - 100)
        if w >= 1000:
            v = room.slot(other, w, 600, avoid=("door", "opening", "window"))
            if v is not None and _try(room, [wardrobe(room, other, v, w)], out):
                break
    _curtains(room, out)


def furnish_toilet(room, out, seed):
    door_sides = {s for s in room.SIDES for k, *_ in room.openings.get(s, []) if k in ("door", "opening")}
    order = sorted(room.SIDES, key=lambda s: (s in door_sides, s not in {room.opposite(d) for d in door_sides}))
    placed_wc = None
    for side in order:
        if room.depth(side) < 1100:
            continue
        for u in (450.0, room.length(side) - 450.0, room.length(side) / 2):
            if _try(room, [wc(room, side, u)], out):
                placed_wc = (side, u)
                break
        if placed_wc:
            break
    for side in order:
        if room.depth(side) < 900:
            continue
        prefer = None if not placed_wc or side != placed_wc[0] else (
            room.length(side) - 450 if placed_wc[1] < room.length(side) / 2 else 450)
        u = room.slot(side, 650, 450, avoid=("door", "opening", "window"), prefer=prefer)
        if u is not None and _try(room, [washbasin(room, side, u)], out):
            break


def furnish_kitchen(room, out, seed):
    options = []
    for side in room.SIDES:
        if room.depth(side) < 1500:
            continue
        for a, b in room.free_spans(side):
            window = any(w1 > a and w0 < b for w0, w1, _, _ in _windows(room, side))
            options.append((window and b - a >= 2400, b - a, side, a, b))
    options.sort(reverse=True)
    for _, _, side, a, b in options:                  # a long free wall, one with a window (for the sink) first
        a, b = a + 10, min(b - 10, a + 4200)
        fridge_u = None
        if b - a >= 2600:
            fridge_u = b - 350
            b -= 720
        while b - a >= 1200:
            windows = [(w0, w1) for w0, w1, _, _ in _windows(room, side) if w1 > a and w0 < b]
            main = counter(room, side, a, b, window_spans=windows)
            if _try(room, [main], out):
                break
            b -= 300                                   # shorten away from whatever is in the way
        else:
            continue
        sink_u = (windows[0][0] + windows[0][1]) / 2 if windows else a + 500
        sink_u = min(max(sink_u, a + 450), b - 450)
        hob_u = b - 450 if abs(sink_u - (b - 450)) > 900 else a + 450
        _try(room, [kitchen_sink(room, side, sink_u)], out, allow=list(room.placed))
        _try(room, [hob_and_hood(room, side, hob_u)], out, allow=list(room.placed))
        if not (fridge_u is not None and _try(room, [fridge(room, side, fridge_u)], out)):
            spot = room.best_wall(700, 700, shun=())
            if spot:
                _try(room, [fridge(room, *spot)], out)
        # L-shape on a free neighbouring wall.
        for other in room.neighbours(side):
            for sa, sb in room.free_spans(other):
                start = 620 if sa < 620 else sa
                length = min(sb, 2400) - start
                if length >= 1200 and _try(room, [counter(room, other, start, start + length, uppers=False)], out):
                    return
        return


def furnish_dress(room, out, seed):
    for side in sorted(room.SIDES, key=lambda s: -room.length(s))[:2]:
        length = max((b - a for a, b in room.free_spans(side, ("door", "opening", "window"))), default=0)
        w = min(2400.0, length - 100)
        if w >= 900 and room.depth(side) >= 1500:
            u = room.slot(side, w, 600, avoid=("door", "opening", "window"))
            if u is not None:
                _try(room, [wardrobe(room, side, u, w)], out)


def furnish_utility(room, out, seed):
    wall = room.best_wall(600, 600)
    if wall:
        _try(room, [washing_machine(room, *wall)], out)


def furnish_office(room, out, seed):
    wall = room.best_wall(1400, 1400, shun=())
    if wall:
        _try(room, desk(room, *wall), out)


def furnish_meeting(room, out, seed):
    side = "n" if room.length("n") >= room.length("w") else "w"
    length, depth = room.length(side), room.depth(side)
    table_l, table_w = min(4800.0, length - 1800), 1200.0
    if table_l >= 1600 and depth >= table_w + 1600:
        _try(room, dining_set(room, side, length / 2, depth / 2, table_l, table_w), out)


def furnish_reception(room, out, seed):
    wall = room.best_wall(1800, 700, shun=())
    if wall:
        _try(room, [reception_desk(room, *wall)], out)
    wall = room.best_wall(2100, 850)
    if wall:
        _try(room, [sofa(room, *wall)], out)


LAYOUTS = {"living": furnish_living, "dining": furnish_dining, "bedroom": furnish_bedroom, "toilet": furnish_toilet,
           "kitchen": furnish_kitchen, "dress": furnish_dress, "utility": furnish_utility, "office": furnish_office,
           "meeting": furnish_meeting, "reception": furnish_reception}


def _split(rect, kinds):
    """A room named for two uses (LIVING / DINING) is split in two along its length."""
    x0, y0, x1, y1 = rect
    if len(kinds) < 2:
        return [(kinds[0], rect)] if kinds else []
    if x1 - x0 >= y1 - y0:
        mid = (x0 + x1) / 2
        return [(kinds[0], (x0, y0, mid, y1)), (kinds[1], (mid, y0, x1, y1))]
    mid = (y0 + y1) / 2
    return [(kinds[0], (x0, y0, x1, mid)), (kinds[1], (x0, mid, x1, y1))]


# ---------------------------------------------------------------------------
# Building model hook
# ---------------------------------------------------------------------------

def _rect_of(element):
    xs = [v[0] for v in element["mesh"]["vertices"]]
    ys = [v[1] for v in element["mesh"]["vertices"]]
    return min(xs), min(ys), max(xs), max(ys)


def _openings_by_side(rect, elements):
    x0, y0, x1, y1 = rect
    sides: dict[str, list] = {s: [] for s in Room.SIDES}
    for e in elements:
        kind = e.get("element_type")
        if kind not in ("door", "window", "opening"):
            continue
        r = _rect_of(e)
        o = e.get("opening") or {}
        head = o.get("head_mm"); sill = o.get("sill_mm")
        t = FACE_TOLERANCE_MM
        for side, face_near, along in (("n", abs(r[3] - y0) <= t or (r[1] <= y0 <= r[3]), (r[0], r[2], x0, x1)),
                                       ("s", abs(r[1] - y1) <= t or (r[1] <= y1 <= r[3]), (r[0], r[2], x0, x1)),
                                       ("w", abs(r[2] - x0) <= t or (r[0] <= x0 <= r[2]), (r[1], r[3], y0, y1)),
                                       ("e", abs(r[0] - x1) <= t or (r[0] <= x1 <= r[2]), (r[1], r[3], y0, y1))):
            a0, a1, lo, hi = along
            if face_near and a1 > lo + 50 and a0 < hi - 50:
                sides[side].append((kind, max(a0, lo) - lo, min(a1, hi) - lo, head, sill))
    return sides


def _mesh(piece: Piece, base_z: float) -> tuple[dict, list[dict]]:
    vertices, parts = [], {}
    for u0, u1, v0, v1, z0, z1, colour, opacity in piece.boxes:
        x0, y0, x1, y1 = piece.room.rect(piece.side, u0, u1, v0, v1)
        offset = len(vertices)
        for z in (z0, z1):
            vertices += [[round(x0, 1), round(y0, 1), round(base_z + z, 1)], [round(x1, 1), round(y0, 1), round(base_z + z, 1)],
                         [round(x1, 1), round(y1, 1), round(base_z + z, 1)], [round(x0, 1), round(y1, 1), round(base_z + z, 1)]]
        key = (C.get(colour, colour), opacity)
        parts.setdefault(key, []).extend([[offset + i for i in face] for face in BOX_FACES])
    if piece.cyls:
        from app.services.equipment import cylinder_points

        for cyl in piece.cyls:
            pts, cyl_faces, colour = cylinder_points(piece, cyl, base_z)
            offset = len(vertices)
            vertices += pts
            parts.setdefault((colour, 1.0), []).extend([[offset + i for i in face] for face in cyl_faces])
    faces = [f for fs in parts.values() for f in fs]
    mesh_parts = [{"color": c, "opacity": o, "faces": fs} for (c, o), fs in parts.items()]
    return {"vertices": vertices, "faces": faces}, mesh_parts


def open_double_height_rooms(rooms: list[dict[str, Any]], slabs: list[dict[str, Any]]) -> list[str]:
    """
    The floor slab under the upper half of a double-height plant room is open: the room rises through it.
    Run after furnish_building (which finds those rooms); returns notes.
    """
    from app.services.stair_detection import subtract_rect

    notes = []
    for room in rooms:
        if not room.get("double_height") or not room.get("bounds_mm"):
            continue
        pid = (room.get("storey") or {}).get("placement_id")
        slab = next((s for s in slabs if s["element_id"] == f"slab:{pid}:base" and s.get("footprint")), None)
        if slab is None:
            continue
        slab["footprint"]["rects_mm"] = subtract_rect(slab["footprint"]["rects_mm"], room["bounds_mm"])
        slab.setdefault("openings", []).append({"placement_id": pid, "rect_mm": room["bounds_mm"],
                                                "basis": f"double-height {room['name'].lower()} (labelled on the level below too)"})
        notes.append(f"{room['name']}: floor left open over the double-height room below.")
    return notes


def _on_roof(element) -> bool:
    """Equipment drawn on a roof plan stands on the roof, above the rooms of the top floor."""
    return (element.get("furniture") or {}).get("room") == "roof"


def _same_room_below(room_label, kinds, rect, rooms, by_storey) -> dict | None:
    """The label of this plant room on the level directly below, when it marks the same (double-height) room."""
    from app.services.equipment import DOUBLE_HEIGHT_KINDS, SAME_SPOT_MM

    if not any(k in DOUBLE_HEIGHT_KINDS for k in kinds):
        return None
    here = by_storey[room_label["storey"]["placement_id"]]
    lower = [s for s in by_storey.values() if s["base_elevation_mm"] < here["base_elevation_mm"]]
    if not lower:
        return None
    below = max(lower, key=lambda s: s["base_elevation_mm"])
    name = (room_label.get("name") or "").strip().upper()
    cx, cy = room_label["position_mm"][0], room_label["position_mm"][1]
    for other in rooms:
        if (other.get("storey") or {}).get("placement_id") != below["placement_id"]:
            continue
        if (other.get("name") or "").strip().upper() != name:
            continue
        ox, oy = other["position_mm"][0], other["position_mm"][1]
        if math.hypot(ox - cx, oy - cy) <= SAME_SPOT_MM or (rect and rect[0] <= ox <= rect[2] and rect[1] <= oy <= rect[3]):
            return other
    return None


def furnish_building(storeys: list[dict[str, Any]], elements: list[dict[str, Any]],
                     rooms: list[dict[str, Any]], drawn_lines=None) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Furniture and equipment for every labelled room whose walls are found; returns (elements, notes).
    drawn_lines(placement_id) -> thin rectangles of the plan's long drawn lines: used for a room whose label is
    not enclosed by detected walls (the drawing's own wall lines then bound it).
    """
    by_storey = {s["placement_id"]: s for s in storeys}
    out_elements, notes = [], []
    counts: dict[str, int] = {}
    for n, room_label in enumerate(rooms):
        kinds = room_types(room_label.get("name", ""))
        storey = by_storey.get((room_label.get("storey") or {}).get("placement_id"))
        if storey is None:
            continue
        mine = [e for e in elements if e["storey"]["placement_id"] == storey["placement_id"]]
        walls = [r for e in mine if e.get("element_type") in ("wall", "column", "door", "window", "opening")
                 for r in ([tuple(x) for x in e["plan_rects"]] if e.get("plan_rects") else [_rect_of(e)])]
        cx, cy = room_label["position_mm"][0], room_label["position_mm"][1]
        rect = room_bounds(cx, cy, walls)
        from app.services.equipment import LAYOUTS as PLANT

        largest = PLANT_ROOM_MAX_MM if any(k in PLANT for k in kinds) else ROOM_SIZE_MM[1]
        ok = lambda r: r is not None and all(ROOM_SIZE_MM[0] <= v <= largest for v in (r[2] - r[0], r[3] - r[1]))  # noqa: E731
        basis = "detected walls"
        if not ok(rect) and drawn_lines is not None:
            rect = room_bounds(cx, cy, walls + drawn_lines(storey["placement_id"]))
            basis = "the plan's drawn wall lines"
        found = ok(rect)
        if found:
            room_label["bounds_basis"] = basis
        if found:
            room_label["bounds_mm"] = [round(v, 1) for v in rect]      # the room's inner wall faces
        if not kinds:
            continue
        if not found:
            notes.append(f"{room_label['name']}: walls round the label not found; not furnished.")
            continue
        drawn = [e for e in mine if e.get("detected_by") == "cad_block" and not _on_roof(e)
                 and rect[0] <= sum(_rect_of(e)[0::2]) / 2 <= rect[2] and rect[1] <= sum(_rect_of(e)[1::2]) / 2 <= rect[3]]
        for e in drawn:
            e["furniture"]["room"] = room_label["name"]
            e["provenance"]["room"] = room_label["name"]
        main = [e for e in drawn if e["component"] not in MINOR_DRAWN]
        if main:
            notes.append(f"{room_label['name']}: {len(main)} item(s) of equipment drawn in the CAD file; "
                         "no indicative layout added.")
            continue
        if _same_room_below(room_label, kinds, rect, rooms, by_storey):
            room_label["double_height"] = True
            notes.append(f"{room_label['name']} on {storey.get('drawing_title') or 'the upper level'}: the level below labels "
                         "the same plant room here, so it is read as one double-height room; equipped on the lower floor.")
            continue
        blockers = [_rect_of(e) for e in mine if e.get("element_type") in ("column", "stair", "lift shaft", "railing")
                    or (e.get("detected_by") == "cad_block" and not _on_roof(e))]
        base = float(storey["base_elevation_mm"])
        seed = zlib.crc32(f"{room_label['name']}{cx:.0f}{cy:.0f}".encode())
        for kind, part in _split(rect, kinds):
            openings = _openings_by_side(part, mine)
            room = Room(part, openings, blockers)
            pieces: list[Piece] = []
            _layouts()[kind](room, pieces, seed)
            for k, piece in enumerate(pieces):
                mesh, mesh_parts = _mesh(piece, base)
                width, depth, height = piece.size
                out_elements.append({
                    "element_id": f"furniture:{storey['placement_id']}:{n}:{kind}:{k}",
                    "element_type": "furniture", "component": piece.name, "detected_by": "room_furnishing",
                    "wall_type": None,
                    "dimensions_mm": {"length": round(width), "thickness": round(depth), "height": round(height)},
                    "lod": {"category": "furniture", "minimum_lod": 500},
                    "height_status": "display_only", "height_verification": None,
                    "provenance": {"room": room_label["name"], "room_type": kind, "basis": BASIS},
                    "opening": None, "between": None, "host_component": None,
                    "drawing_id": storey.get("drawing_id"), "drawing_title": storey.get("drawing_title"),
                    "storey": {key: storey[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
                    "mesh": mesh, "mesh_parts": mesh_parts, "reviewed_by": None, "is_external": False,
                    "furniture": {"room": room_label["name"], "room_type": kind},
                })
            counts[kind] = counts.get(kind, 0) + len(pieces)
    if counts:
        notes.insert(0, "Furniture (indicative, typical sizes): " + ", ".join(f"{v} in {k}" for k, v in sorted(counts.items())))
    return out_elements, notes
