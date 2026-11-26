"""
LOD 500 equipment for technical rooms, laid out from the room labels like the furniture.

A label decides the room type: chiller hall, generator room, pump room (fire, booster,
auxiliary), electrical / LV / MV / RMU / switchgear / VFD room, transformer room,
UPS / DC charger room, battery room, IT / server / telecom room, control / security
room, AHU room, water treatment (RO, UF, backwash, tank) room, prayer room, store.
A label that only points at another level (GENERATOR ROOM BELOW, VOID OVER) is not a room.

Everything is indicative: typical equipment of typical sizes, set out along the room's
free walls or centred with service aisles, clear of doors, stairs, lifts and columns.
A piece that does not fit is left out. Every element says it is indicative.
"""

from __future__ import annotations

import math
import re

from app.services import furnishing as fu
from app.services.furnishing import Piece, Room, _try

ROOM_TYPES = [
    ("chiller", re.compile(r"\bCHILLERS?\b")),
    ("generator", re.compile(r"\b(?:GENERATOR|GENSET|DG ROOM|D\.G\.)\b")),
    ("pump", re.compile(r"\bPUMPS?\b")),
    ("transformer_room", re.compile(r"\bTRANSFORMERS?\b")),
    ("ups", re.compile(r"\bUPS\b|DC CHARGER")),
    ("battery", re.compile(r"\bBATTERY\b")),
    ("electrical", re.compile(r"\b(?:ELECTRICAL|LV|MV|HV|RMU|SWITCHGEAR|SWITCH ROOM|MDB|SMDB|PANEL ROOM|VFD|RVSS|MCC)\b")),
    ("it", re.compile(r"\b(?:IT|SERVER|DATA|TELECOM|COMMS?|HUB|MDF|IDF|ICT)\b")),
    ("control", re.compile(r"\b(?:CONTROL|SECURITY|BMS|CCTV|FIRE COMMAND)\b")),
    ("ahu", re.compile(r"\b(?:AHU|VAHU|FAHU|AIR HANDLING)\b")),
    ("water", re.compile(r"\b(?:RO|UF|BACK ?WASH|WATER TREATMENT|TANK ROOM|FILTRATION)\b")),
    ("prayer", re.compile(r"\b(?:PRAYER|MUSALLA|POOJA)\b")),
    ("store", re.compile(r"\b(?:STORE|STORAGE|JANITOR|JAN)\b")),
    ("bedroom", re.compile(r"\b(?:ACCOMMODATION|DORM(?:ITORY)?|BUNK)\b")),
]
NOT_HERE = re.compile(r"\b(?:BELOW|ABOVE|OVER|VOID|OPEN TO)\b")

C = {
    "panel": "#c9cdd2", "panel_dark": "#5b6168", "steel": "#9aa3ab", "black": "#1d1f22", "genset": "#d1a21c",
    "chiller": "#2f6aa3", "pump": "#3c78b5", "fire": "#c0392b", "plinth": "#a9a49a", "battery": "#3a3f45",
    "cell": "#7f8a94", "rack": "#202326", "led": "#3fa7ff", "ahu": "#a8b4bf", "tank": "#e9edf0", "rug1": "#2e6b4f",
    "rug2": "#8c2f39", "shelf": "#8d949b", "desk": "#6b5236", "screen": "#0d1014", "transformer": "#7d8a6a",
    "cyan": "#5aa9c9",
}


def _cyl(piece: Piece, u, v, z0, z1, r, colour, axis="z", length=None):
    piece.cyls.append((u, v, z0, z1, r, colour, axis, length))


def _row(room: Room, out, side, width, depth, make, *, gap=0.0, avoid=("door", "opening"), max_n=99, v0=0.0):
    """As many pieces as fit in a row along the longest free stretch of one wall; returns how many."""
    spans = sorted(room.free_spans(side, avoid), key=lambda s: -(s[1] - s[0]))
    if not spans or room.depth(side) < depth + 900:
        return 0
    a, b = spans[0]
    n = min(max_n, int((b - a - 200 + gap) // (width + gap)))
    if n <= 0:
        return 0
    start = a + ((b - a) - (n * width + (n - 1) * gap)) / 2
    placed = 0
    for k in range(n):
        u = start + k * (width + gap) + width / 2
        if _try(room, [make(room, side, u, v0)], out):
            placed += 1
    return placed


def _longest(room: Room, sides=None):
    return sorted(sides or room.SIDES, key=lambda s: -room.length(s))


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------

def panel(room, side, u, v0=0.0, w=800.0, d=600.0, h=2200.0, name="switchgear panel"):
    p = Piece(name, room, side, (w, d, h))
    p.add(u - w / 2, u + w / 2, v0, v0 + d, 0, 100, C["black"])
    p.add(u - w / 2 + 5, u + w / 2 - 5, v0, v0 + d, 100, h, C["panel"])
    p.add(u - w / 2 + 60, u + w / 2 - 60, v0 + d, v0 + d + 8, 1500, 1800, C["panel_dark"])     # meters / HMI
    p.add(u - 4, u + 4, v0 + d, v0 + d + 10, 300, h - 200, C["panel_dark"])                   # door joint
    return p


def server_rack(room, side, u, v0=0.0):
    p = Piece("server rack", room, side, (600, 1100, 2100))
    p.add(u - 300, u + 300, v0, v0 + 1100, 0, 2100, C["rack"])
    for z in range(400, 2000, 220):
        p.add(u - 240, u + 240, v0 + 1100, v0 + 1106, z, z + 25, C["led"])
    return p


def battery_rack(room, side, u, v0=0.0, length=2000.0):
    p = Piece("battery rack", room, side, (length, 800, 1800))
    a, b = u - length / 2, u + length / 2
    for z in (0, 600, 1200):
        p.add(a, b, v0, v0 + 800, z, z + 60, C["steel"])
        for k in range(int(length // 260)):
            x = a + 30 + k * 260
            p.add(x, x + 220, v0 + 60, v0 + 740, z + 60, z + 460, C["battery"])
    for x in (a, b - 60):
        p.add(x, x + 60, v0, v0 + 800, 0, 1800, C["steel"])
    return p


def ups_cabinet(room, side, u, v0=0.0):
    p = panel(room, side, u, v0, 800, 900, 2000, "UPS cabinet")
    p.add(u - 300, u + 300, v0 + 900, v0 + 908, 1300, 1600, C["cyan"])
    return p


def pump_set(room, side, u, v0, colour):
    p = Piece("pump set", room, side, (1800, 900, 1100))
    p.add(u - 900, u + 900, v0, v0 + 900, 0, 200, C["plinth"])
    p.add(u - 750, u - 50, v0 + 200, v0 + 700, 200, 750, C["steel"])                         # motor
    _cyl(p, u + 350, v0 + 450, 200, 900, 300, colour)                                         # pump casing
    _cyl(p, u + 350, v0 + 450, 900, 2600, 120, colour)                                        # riser to header
    return p


def genset(room, side, u, v_mid, length, width):
    p = Piece("generator set", room, side, (length, width, 2600))
    a, b = u - length / 2, u + length / 2
    c0, c1 = v_mid - width / 2, v_mid + width / 2
    p.add(a, b, c0, c1, 0, 300, C["black"])                                                   # base frame / day tank
    p.add(a + 200, b - 1200, c0 + 150, c1 - 150, 300, 2300, C["genset"])                      # canopy / engine
    p.add(b - 1200, b - 200, c0 + 100, c1 - 100, 300, 2500, C["panel_dark"])                  # radiator
    _cyl(p, a + 900, v_mid, 2300, 3400, 180, C["steel"])                                      # exhaust
    return p


def chiller(room, side, u, v_mid, length, width):
    p = Piece("chiller", room, side, (length, width, 2500))
    a, b = u - length / 2, u + length / 2
    p.add(a, b, v_mid - width / 2, v_mid + width / 2, 0, 250, C["steel"])
    for dv in (-width * 0.22, width * 0.22):                                                  # evaporator / condenser
        _cyl(p, a + 200, v_mid + dv, 900 + (300 if dv > 0 else 0), 0, 420, C["chiller"], axis="u", length=length - 400)
    p.add(a + length * 0.35, a + length * 0.65, v_mid - width / 2, v_mid - width / 2 + 500, 250, 2200, C["chiller"])
    p.add(b - 900, b - 200, v_mid + width / 2 - 450, v_mid + width / 2, 250, 1900, C["panel"])  # starter panel
    return p


def ahu(room, side, u, v_mid, length, width):
    p = Piece("air handling unit", room, side, (length, width, 2400))
    p.add(u - length / 2, u + length / 2, v_mid - width / 2, v_mid + width / 2, 0, 150, C["black"])
    p.add(u - length / 2 + 50, u + length / 2 - 50, v_mid - width / 2 + 50, v_mid + width / 2 - 50, 150, 2400, C["ahu"])
    p.add(u + length / 2 - 900, u + length / 2 - 200, v_mid - 500, v_mid + 500, 2400, 3200, C["ahu"])  # duct up
    return p


def tank(room, side, u, v_mid, r, h, colour=C["tank"]):
    p = Piece("tank", room, side, (2 * r, 2 * r, h))
    p.add(u - r, u + r, v_mid - r, v_mid + r, 0, 150, C["plinth"])
    _cyl(p, u, v_mid, 150, h, r, colour)
    return p


def transformer(room, side, u, v_mid, w=2400.0, d=1600.0):
    p = Piece("transformer", room, side, (w, d, 2400))
    p.add(u - w / 2, u + w / 2, v_mid - d / 2, v_mid + d / 2, 0, 1900, C["transformer"])
    for k in range(int(w // 260)):
        x = u - w / 2 + 80 + k * 260
        for vv in (v_mid - d / 2 - 250, v_mid + d / 2):
            p.add(x, x + 60, vv, vv + 250, 200, 1700, C["transformer"])
    for k in (-1, 0, 1):
        _cyl(p, u + k * w / 4, v_mid, 1900, 2500, 90, "#7a4b2a")
    return p


def console(room, side, u):
    p = Piece("control console", room, side, (3000, 900, 2300))
    p.add(u - 1500, u + 1500, 0, 900, 0, 750, C["desk"])
    for k in (-1, 0, 1):
        p.add(u + k * 900 - 280, u + k * 900 + 280, 150, 190, 750, 1100, C["screen"])
    for k in (-1, 0, 1):                                                                       # video wall
        p.add(u + k * 1000 - 480, u + k * 1000 + 480, 0, 40, 1300, 1850, C["screen"])
        p.add(u + k * 1000 - 480, u + k * 1000 + 480, 0, 40, 1880, 2430, C["screen"])
    return p


def shelf(room, side, u, v0=0.0):
    p = Piece("shelving", room, side, (1000, 500, 2000))
    for z in (0, 450, 900, 1350, 1800):
        p.add(u - 500, u + 500, v0, v0 + 500, z, z + 40, C["shelf"])
    for x in (u - 500, u + 470):
        p.add(x, x + 30, v0, v0 + 500, 0, 2000, C["shelf"])
    return p


def rug(room, side, u, v0, colour):
    return Piece("prayer rug", room, side, (700, 1200, 10)).add(u - 350, u + 350, v0, v0 + 1200, 0, 12, colour)


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def _centre_rows(room: Room, out, make, length, width, aisle=1500.0, max_n=6):
    """Large units centred in the room along its long axis, with service aisles round them."""
    side = "n" if room.length("n") >= room.length("w") else "w"
    span, depth = room.length(side), room.depth(side)
    rows = max(1, min(2, int((depth - aisle) // (width + aisle))))
    per_row = max(1, min(max_n, int((span - aisle) // (length + aisle))))
    placed = 0
    for r in range(rows):
        v_mid = depth * (r + 1) / (rows + 1)
        for k in range(per_row):
            u = span * (k + 1) / (per_row + 1)
            if _try(room, [make(room, side, u, v_mid)], out):
                placed += 1
    return placed


def furnish_chiller(room, out, seed):
    _centre_rows(room, out, lambda r, s, u, v: chiller(r, s, u, v, 6000, 2200), 6000, 2200, max_n=4) or \
        _centre_rows(room, out, lambda r, s, u, v: chiller(r, s, u, v, 4000, 1800), 4000, 1800)


def furnish_generator(room, out, seed):
    _centre_rows(room, out, lambda r, s, u, v: genset(r, s, u, v, 6000, 2200), 6000, 2200, max_n=3) or \
        _centre_rows(room, out, lambda r, s, u, v: genset(r, s, u, v, 4000, 1700), 4000, 1700)


def furnish_pump(room, out, seed):
    colour = C["fire"]
    for side in _longest(room):
        if _row(room, out, side, 1800, 900, lambda r, s, u, v0: pump_set(r, s, u, 300, colour), gap=500, max_n=5):
            return


def furnish_electrical(room, out, seed):
    for side in _longest(room)[:2]:
        _row(room, out, side, 800, 600, lambda r, s, u, v0: panel(r, s, u), max_n=14)


def furnish_transformer(room, out, seed):
    _centre_rows(room, out, lambda r, s, u, v: transformer(r, s, u, v), 2400, 2100, max_n=2)


def furnish_ups(room, out, seed):
    sides = _longest(room)
    _row(room, out, sides[0], 800, 900, lambda r, s, u, v0: ups_cabinet(r, s, u), max_n=8)
    if len(sides) > 1:
        _row(room, out, sides[1], 2000, 800, lambda r, s, u, v0: battery_rack(r, s, u), gap=300, max_n=4)


def furnish_battery(room, out, seed):
    side = "n" if room.length("n") >= room.length("w") else "w"
    depth = room.depth(side)
    v = 0.0
    while v + 800 <= depth - 300:
        placed = 0
        span = room.length(side)
        for k in range(int((span - 1200) // 2300)):
            u = 700 + k * 2300 + 1000
            placed += _try(room, [battery_rack(room, side, u, v)], out)
        v += 800 + 1200
        if not placed and v > depth / 2:
            break


def furnish_it(room, out, seed):
    side = "n" if room.length("n") >= room.length("w") else "w"
    depth = room.depth(side)
    for v in (600.0, 600.0 + 1100 + 1200):                    # two rows, a 1.2 m aisle between
        if v + 1100 > depth - 600:
            break
        span = room.length(side)
        n = min(10, int((span - 1600) // 600))
        start = (span - n * 600) / 2
        for k in range(n):
            _try(room, [server_rack(room, side, start + k * 600 + 300, v)], out)


def furnish_control(room, out, seed):
    wall = room.best_wall(3000, 2600, shun=())
    if wall:
        _try(room, [console(room, *wall)], out)


def furnish_ahu(room, out, seed):
    _centre_rows(room, out, lambda r, s, u, v: ahu(r, s, u, v, 4500, 2000), 4500, 2000, max_n=2) or \
        _centre_rows(room, out, lambda r, s, u, v: ahu(r, s, u, v, 3000, 1600), 3000, 1600, max_n=2)


def furnish_water(room, out, seed):
    _centre_rows(room, out, lambda r, s, u, v: tank(r, s, u, v, 800, 2300, C["pump"]), 1600, 1600, aisle=900, max_n=4)
    for side in _longest(room):
        if _row(room, out, side, 1800, 900, lambda r, s, u, v0: pump_set(r, s, u, 300, C["pump"]), gap=500, max_n=3):
            return


def furnish_prayer(room, out, seed):
    side = "n" if room.length("n") >= room.length("w") else "w"
    span, depth = room.length(side), room.depth(side)
    for row, v in enumerate(range(800, int(depth) - 1300, 1500)):
        for k in range(int((span - 600) // 900)):
            _try(room, [rug(room, side, 450 + k * 900 + 350, v, C["rug1"] if (row + k) % 2 else C["rug2"])], out)


def furnish_store(room, out, seed):
    for side in _longest(room)[:2]:
        _row(room, out, side, 1000, 500, lambda r, s, u, v0: shelf(r, s, u), gap=50, max_n=10)


LAYOUTS = {"chiller": furnish_chiller, "generator": furnish_generator, "pump": furnish_pump,
           "electrical": furnish_electrical, "transformer_room": furnish_transformer, "ups": furnish_ups,
           "battery": furnish_battery, "it": furnish_it, "control": furnish_control, "ahu": furnish_ahu,
           "water": furnish_water, "prayer": furnish_prayer, "store": furnish_store}
# Plant rooms that commonly rise through two levels (a chiller hall, a pump room): when the level directly below
# labels the same room at the same spot, the two labels are one double-height room and its equipment stands on
# the lower floor only. Rooms that really do stack floor over floor (toilets, offices, electrical rooms) are not here.
DOUBLE_HEIGHT_KINDS = {"chiller", "pump", "generator", "water", "transformer_room"}
SAME_SPOT_MM = 3000.0


# ---------------------------------------------------------------------------
# Cylinders in a piece's mesh
# ---------------------------------------------------------------------------

def cylinder_points(piece: Piece, cyl, base_z: float, n: int = 14):
    """Vertices and faces of one cylinder: upright (axis z) or lying along the wall (axis u)."""
    u, v, z0, z1, r, colour, axis, length = cyl
    pts = []
    if axis == "z":
        for z in (z0, z1):
            for k in range(n):
                a = 2 * math.pi * k / n
                x, y = piece.room.world(piece.side, u + r * math.cos(a), v + r * math.sin(a))
                pts.append([round(x, 1), round(y, 1), round(base_z + z, 1)])
    else:
        for uu in (u, u + length):
            for k in range(n):
                a = 2 * math.pi * k / n
                x, y = piece.room.world(piece.side, uu, v + r * math.cos(a))
                pts.append([round(x, 1), round(y, 1), round(base_z + z0 + r * math.sin(a), 1)])
    faces = []
    for k in range(n):
        a, b = k, (k + 1) % n
        faces += [[a, b, n + b], [a, n + b, n + a]]
    faces += [[0, k + 1, k] for k in range(1, n - 1)] + [[n, n + k, n + k + 1] for k in range(1, n - 1)]
    return pts, faces, fu.C.get(colour, colour)
