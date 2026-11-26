"""
Stairs and lifts from a floor plan's own drawing.

Stairs: riser lines near a room label containing STAIR. Everything is worked out in
page-local millimetres (x right, y down).

* Flights: runs of three or more parallel, equal lines, evenly spaced 200-400 mm
  apart (the going). They must lie near the label and outside any wall. A line's
  length is the flight's width, and each line is a riser. Two flights side by side
  make a dog-leg; one flight is a straight stair.
* Direction: the flight nearest the "UP" text climbs first. The half landing is at
  the end of the flights away from the "UP" text. Its depth runs to the nearest wall
  beyond the flights; with no wall found it is the flight width, marked assumed.
* Rise: the storey height (this plan's base level to the next storey's) divided by
  the risers drawn. Plans often draw only part of a flight (cut at the plan level).
  When the drawn risers give more than 220 mm each, the count becomes the storey
  height over a 175 mm maximum rise (190 or 200 mm if 175 does not fit), marked
  assumed. Both flights then run from the far end towards the landing at the drawn
  going, provided the landing keeps at least half a flight width. Fewer than 120 mm
  per riser gives no stair.

Built from these: steps, a sloping waist under each flight and the landing. The
waist and landing thickness come from the slab-thickness note, or a display value
marked so. Stairs show at every LOD; a handrail (display sizes) is added at LOD 500
on each open side. The slab of the storey above gets an opening over the stair. A
stair on the top storey (nothing above to reach) is not built again.

Lifts: a room label containing LIFT or ELEVATOR, on one or more storeys.

* The shaft is the walls round the label, or else the drawn car outline (the nearest
  lines round the label, 0.9-4 m each way).
* Labels on several storeys at the same place form one shaft, from the lowest
  storey's base to the top storey's top. The slabs of the storeys above the lowest
  get an opening.
* Every LOD shows the shaft as a light glass volume, with the car at the lowest
  stop. The car's size is display-only: the shaft inset by 100 mm, 2400 mm high.
* Door and opening gaps in the shaft's walls are marked as lift landing doors.
* A printed capacity (for example "3000 KG") is kept.
"""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]
STAIR_WORDS = ("STAIR",)
LIFT_WORDS = ("LIFT", "ELEVATOR")
MIN_LINES = 3
GOING_MM = (200.0, 400.0)
GOING_TOLERANCE = 0.15
WIDTH_MM = (600.0, 2600.0)
RISE_MM = (120.0, 220.0)
TYPICAL_MAX_RISE_MM = 175.0
ASSUMED_MAX_RISES_MM = (175.0, 190.0, 200.0)   # tried in turn when the plan draws only part of the flights
SEARCH_MM = 7000.0                 # riser lines are looked for within this distance of the label
FLIGHT_NEAR_LABEL_MM = 4000.0
LANDING_WALL_MM = 4000.0
SIDE_WALL_MM = 200.0               # a flight side this close to a wall is closed; otherwise open (handrail)
WAIST_DISPLAY_MM = 150.0
RAIL_HEIGHT_DISPLAY_MM = 900.0
RAIL_SIZE_DISPLAY_MM = 50.0
BALUSTER_DISPLAY_MM = 25.0
LIFT_SIZE_MM = (900.0, 4000.0)
LIFT_CAR_INSET_DISPLAY_MM = 100.0
LIFT_CAR_HEIGHT_DISPLAY_MM = 2400.0
LIFT_DOOR_TOUCH_MM = 150.0
CAPACITY = re.compile(r"(\d{3,5})\s*KG", re.IGNORECASE)

Rect = tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Plan reading (page-local millimetres: x right, y down)
# ---------------------------------------------------------------------------

def room_bounds(cx: float, cy: float, rects: list[Rect]) -> Rect | None:
    """Inner faces of the nearest wall, column or opening left, right, above and below a point."""
    left = max((r[2] for r in rects if r[1] <= cy <= r[3] and r[2] <= cx), default=None)
    right = min((r[0] for r in rects if r[1] <= cy <= r[3] and r[0] >= cx), default=None)
    top = max((r[3] for r in rects if r[0] <= cx <= r[2] and r[3] <= cy), default=None)
    bottom = min((r[1] for r in rects if r[0] <= cx <= r[2] and r[1] >= cy), default=None)
    if None in (left, right, top, bottom):
        return None
    return left, top, right, bottom


def _inside_any(x: float, y: float, rects: list[Rect], pad: float = 10.0) -> bool:
    return any(r[0] - pad <= x <= r[2] + pad and r[1] - pad <= y <= r[3] + pad for r in rects)


def candidate_lines(lines, rects: list[Rect], centre: tuple[float, float], reach: float = SEARCH_MM):
    """Riser-length lines near the label and not part of a wall."""
    cx, cy = centre
    out = []
    for orient, pos, a0, a1 in lines:
        a0, a1 = min(a0, a1), max(a0, a1)
        if not WIDTH_MM[0] <= a1 - a0 <= WIDTH_MM[1]:
            continue
        mx, my = ((a0 + a1) / 2, pos) if orient == "h" else (pos, (a0 + a1) / 2)
        if abs(mx - cx) > reach or abs(my - cy) > reach or _inside_any(mx, my, rects):
            continue
        out.append((orient, pos, a0, a1))
    return out


def find_flights(lines) -> list[dict[str, Any]]:
    """Runs of evenly spaced, equal, parallel lines."""
    groups: dict[tuple, list[tuple[float, float, float]]] = {}
    for orient, pos, a0, a1 in lines:
        groups.setdefault((orient, round(a0 / 60), round(a1 / 60)), []).append((pos, a0, a1))
    flights = []
    for (orient, _, _), items in groups.items():
        items.sort()
        positions = []
        for pos, a0, a1 in items:                                  # drop duplicates (double-drawn lines)
            if not positions or pos - positions[-1][0] > 20:
                positions.append((pos, a0, a1))
        run: list[tuple[float, float, float]] = []
        for item in positions + [None]:
            if item is not None and run:
                gap = item[0] - run[-1][0]
                spacing = run[1][0] - run[0][0] if len(run) >= 2 else gap
                if GOING_MM[0] <= gap <= GOING_MM[1] and abs(gap - spacing) <= GOING_TOLERANCE * spacing:
                    run.append(item)
                    continue
            if len(run) >= MIN_LINES:
                gaps = [b[0] - a[0] for a, b in zip(run, run[1:])]
                flights.append({"orient": orient, "positions": [p for p, _, _ in run],
                                "a0": median(a for _, a, _ in run), "a1": median(b for _, _, b in run),
                                "going_mm": median(gaps)})
            run = [item] if item is not None else []
    return flights


def flights_in_room(lines, room: Rect) -> list[dict[str, Any]]:
    """Flights whose lines lie inside a room (kept for callers that know the room)."""
    x0, y0, x1, y1 = room
    inside = [(o, p, a0, a1) for o, p, a0, a1 in lines
              if ((y0 + 60 <= p <= y1 - 60 and x0 - 50 <= min(a0, a1) and max(a0, a1) <= x1 + 50) if o == "h"
                  else (x0 + 60 <= p <= x1 - 60 and y0 - 50 <= min(a0, a1) and max(a0, a1) <= y1 + 50))]
    return find_flights(inside)


def _bbox(f) -> Rect:
    p0, p1 = min(f["positions"]), max(f["positions"])
    return (f["a0"], p0, f["a1"], p1) if f["orient"] == "h" else (p0, f["a0"], p1, f["a1"])


def pick_flights(flights: list[dict[str, Any]], centre: tuple[float, float]) -> list[dict[str, Any]]:
    """The flights of the stair at the label: a side-by-side pair if there is one, else the biggest near it."""
    cx, cy = centre

    def distance(f):
        x0, y0, x1, y1 = _bbox(f)
        return math.hypot(max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1))

    near = [f for f in flights if distance(f) <= FLIGHT_NEAR_LABEL_MM]
    best, best_score = None, -1
    for i, a in enumerate(near):
        for b in near[i + 1:]:
            if a["orient"] != b["orient"]:
                continue
            pa, pb = (min(a["positions"]), max(a["positions"])), (min(b["positions"]), max(b["positions"]))
            overlap = min(pa[1], pb[1]) - max(pa[0], pb[0])
            gap = max(a["a0"], b["a0"]) - min(a["a1"], b["a1"])
            if overlap >= 0.3 * min(pa[1] - pa[0], pb[1] - pb[0]) and -50 <= gap <= 1500:
                score = len(a["positions"]) + len(b["positions"])
                if score > best_score:
                    best, best_score = [a, b], score
    if best:
        return best
    return sorted(near, key=lambda f: (-len(f["positions"]), distance(f)))[:1]


def _wall_beyond(rects: list[Rect], orient: str, end: float, direction: int, a0: float, a1: float,
                 limit: float = LANDING_WALL_MM) -> float | None:
    """Nearest wall face past a flight end (along the run), among walls overlapping the flight's width."""
    best = None
    for r in rects:
        lo, hi = (r[1], r[3]) if orient == "h" else (r[0], r[2])           # along-run extent of the rect
        c0, c1 = (r[0], r[2]) if orient == "h" else (r[1], r[3])           # across extent
        if c1 <= a0 + 1 or c0 >= a1 - 1:
            continue
        face = lo if direction > 0 else hi
        d = (face - end) * direction
        if 0 <= d <= limit and (best is None or d < (best - end) * direction):
            best = face
    return best


def _open_sides(rects: list[Rect], orient: str, f: dict[str, Any]) -> list[float]:
    p0, p1 = min(f["positions"]), max(f["positions"])
    open_ = []
    for side, outward in ((f["a0"], -1), (f["a1"], 1)):
        closed = False
        for r in rects:
            lo, hi = (r[1], r[3]) if orient == "h" else (r[0], r[2])
            c0, c1 = (r[0], r[2]) if orient == "h" else (r[1], r[3])
            covers = min(hi, p1) - max(lo, p0) >= 0.5 * (p1 - p0)
            gap = (c0 - side) if outward > 0 else (side - c1)
            if covers and -50 <= gap <= SIDE_WALL_MM:
                closed = True
                break
        if not closed:
            open_.append(side)
    return open_


def plan_stair(flights: list[dict[str, Any]], arrows: list[tuple[str, float, float]],
               rects: list[Rect] | None = None) -> dict[str, Any] | None:
    """Climbing order, landing and open sides; None when there is no flight."""
    if not flights:
        return None
    rects = rects or []
    orient = flights[0]["orient"]
    flights = [f for f in flights if f["orient"] == orient][:2]
    ups = [(x, y) for text, x, y in arrows if text == "UP"]

    def along(x, y):
        return y if orient == "h" else x

    def centre(f):
        x0, y0, x1, y1 = _bbox(f)
        return (x0 + x1) / 2, (y0 + y1) / 2

    def nearest_up(f):
        if not ups:
            return None
        cx, cy = centre(f)
        return min(ups, key=lambda u: math.hypot(cx - u[0], cy - u[1]))

    for f in flights:
        f["open_sides"] = _open_sides(rects, orient, f)
    if len(flights) == 2:
        first = min(flights, key=lambda f: math.hypot(*(a - b for a, b in zip(centre(f), nearest_up(f) or (1e12, 1e12)))))
        second = flights[1] if first is flights[0] else flights[0]
        all_p = [p for f in flights for p in f["positions"]]
        lo_end, hi_end = min(all_p), max(all_p)
        across = (min(f["a0"] for f in flights), max(f["a1"] for f in flights))
        up = nearest_up(first)
        if up is not None:
            landing_low = abs(along(*up) - lo_end) > abs(along(*up) - hi_end)
            basis = "UP text nearest the first flight; landing at the far end"
        else:
            walls = [_wall_beyond(rects, orient, lo_end, -1, *across), _wall_beyond(rects, orient, hi_end, 1, *across)]
            gaps = [lo_end - walls[0] if walls[0] is not None else -1, walls[1] - hi_end if walls[1] is not None else -1]
            landing_low = gaps[0] >= gaps[1]
            basis = "no UP text: direction assumed from the deeper free end (not stated on the drawing)"
        end = lo_end if landing_low else hi_end
        wall = _wall_beyond(rects, orient, end, -1 if landing_low else 1, *across)
        width = max(f["a1"] - f["a0"] for f in flights)
        depth_assumed = wall is None
        boundary = wall if wall is not None else (end - width if landing_low else end + width)
        return {
            "orient": orient, "kind": "dog-leg",
            "flights": [{**first, "climb": sorted(first["positions"], reverse=landing_low)},
                        {**second, "climb": sorted(second["positions"], reverse=not landing_low)}],
            "landing": {"along": (boundary, end) if landing_low else (end, boundary), "across": across,
                        "towards": -1 if landing_low else 1, "boundary_assumed": depth_assumed},
            "direction_basis": basis,
        }
    flight = flights[0]
    up = nearest_up(flight)
    start_low = True if up is None else abs(along(*up) - min(flight["positions"])) <= abs(along(*up) - max(flight["positions"]))
    return {
        "orient": orient, "kind": "straight",
        "flights": [{**flight, "climb": sorted(flight["positions"], reverse=not start_low)}],
        "landing": None,
        "direction_basis": "starts at the end nearer the UP text" if up else
                           "no UP text: the start is assumed (direction not stated on the drawing)",
    }


def _wall_across(rects: list[Rect], orient: str, side: float, outward: int, p0: float, p1: float,
                 limit: float = LANDING_WALL_MM) -> float | None:
    """Nearest wall face beside a flight (across the run), among walls alongside at least half of it."""
    best = None
    for r in rects:
        lo, hi = (r[1], r[3]) if orient == "h" else (r[0], r[2])           # along-run extent
        c0, c1 = (r[0], r[2]) if orient == "h" else (r[1], r[3])           # across extent
        if min(hi, p1) - max(lo, p0) < 0.5 * (p1 - p0):
            continue
        face = c0 if outward > 0 else c1
        d = (face - side) * outward
        if 0 <= d <= limit and (best is None or d < (best - side) * outward):
            best = face
    return best


def _fit_dogleg(plan, storey_height_mm, drawn, rise) -> dict[str, Any] | None:
    flights, landing = plan["flights"], plan["landing"]
    going = median(f["going_mm"] for f in flights)
    width = max(f["a1"] - f["a0"] for f in flights)
    towards = landing["towards"]
    # Both flights run between the far end (where the first starts and the second arrives) and the landing.
    far = max(p for f in flights for p in f["positions"]) if towards < 0 else \
        min(p for f in flights for p in f["positions"])
    for max_rise in ASSUMED_MAX_RISES_MM:
        total = math.ceil(storey_height_mm / max_rise)
        n1 = math.ceil(total / 2)
        n2 = total - n1
        first = [far + towards * i * going for i in range(n1)]
        second = [far + towards * (n2 - 1 - i) * going for i in range(n2)]
        near = far + towards * (max(n1, n2) - 1) * going
        boundary = landing["along"][0] if towards < 0 else landing["along"][1]
        if landing["boundary_assumed"]:
            boundary = near + towards * width
        if (boundary - near) * towards < 0.5 * width:
            continue
        plan["flights"] = [{**flights[0], "climb": first}, {**flights[1], "climb": second}]
        plan["landing"] = {**landing, "along": (boundary, near) if towards < 0 else (near, boundary)}
        return {"ok": True, "rise_mm": storey_height_mm / total, "risers": total,
                "count_basis": (f"assumed: the drawing shows {drawn} risers (a partial flight, {rise:.0f} mm each); "
                                f"{total} risers from the storey height at a {max_rise:.0f} mm maximum rise")}
    return None


def fit_risers(plan: dict[str, Any], storey_height_mm: float, rects: list[Rect] | None = None) -> dict[str, Any]:
    """
    Rise from the drawn risers; or, when the plan draws only part of the stair, the fewest risers at a
    175, 190 or 200 mm maximum rise that fit the room (marked assumed):

    * dog-leg: both flights lengthened at the drawn going from the far end, leaving the landing at least
      half a flight wide;
    * one flight drawn: lengthened in a straight line if a wall ahead shows it fits; otherwise a second
      flight mirrored beside it against the stair room's opposite wall (the usual dog-leg), if the walls
      show there is room for it.
    """
    rects = rects or []
    drawn = sum(len(f["climb"]) for f in plan["flights"])
    rise = storey_height_mm / drawn
    if RISE_MM[0] <= rise <= RISE_MM[1]:
        return {"ok": True, "rise_mm": rise, "risers": drawn, "count_basis": f"{drawn} risers drawn"}
    if rise < RISE_MM[0]:
        return {"ok": False, "reason": f"{drawn} risers over {storey_height_mm:.0f} mm gives {rise:.0f} mm each, "
                                       f"under {RISE_MM[0]:.0f} mm"}
    orient = plan["orient"]
    if plan.get("landing"):
        fit = _fit_dogleg(plan, storey_height_mm, drawn, rise)
        return fit or {"ok": False, "reason": f"the drawing shows {drawn} risers ({rise:.0f} mm each) and a full "
                                              f"stair of {ASSUMED_MAX_RISES_MM[-1]:.0f} mm risers does not fit"}

    flight = plan["flights"][0]
    climb, going = flight["climb"], flight["going_mm"]
    direction = 1 if len(climb) < 2 or climb[1] > climb[0] else -1
    width = flight["a1"] - flight["a0"]
    ahead = _wall_beyond(rects, orient, climb[-1], direction, flight["a0"], flight["a1"], limit=LANDING_WALL_MM * 3)
    for max_rise in ASSUMED_MAX_RISES_MM:                       # a longer straight flight, if the room shows it fits
        total = math.ceil(storey_height_mm / max_rise)
        new = [climb[0] + direction * i * going for i in range(total)]
        if ahead is not None and (ahead - new[-1]) * direction >= 0:
            plan["flights"] = [{**flight, "climb": new}]
            return {"ok": True, "rise_mm": storey_height_mm / total, "risers": total,
                    "count_basis": (f"assumed: the drawing shows {drawn} risers (a partial flight, {rise:.0f} mm each); "
                                    f"{total} risers from the storey height at a {max_rise:.0f} mm maximum rise")}
    # A second flight mirrored beside the drawn one, against the stair room's opposite wall.
    p0, p1 = min(climb), max(climb)
    for side, outward in ((flight["a1"], 1), (flight["a0"], -1)):
        if side not in flight.get("open_sides", [side]):
            continue
        wall = _wall_across(rects, orient, side, outward, p0, p1)
        if wall is None or (wall - side) * outward < width:
            continue
        a0, a1 = (wall - width, wall) if outward > 0 else (wall, wall + width)
        twin = {**flight, "a0": a0, "a1": a1, "open_sides": [a0 if outward > 0 else a1]}
        towards = direction                                     # the landing is beyond the drawn flight's top
        end = max(climb) if towards > 0 else min(climb)
        landing_wall = _wall_beyond(rects, orient, end, towards, min(flight["a0"], a0), max(flight["a1"], a1))
        width_both = max(width, a1 - a0)
        boundary = landing_wall if landing_wall is not None else end + towards * width_both
        plan.update({
            "kind": "dog-leg",
            "flights": [{**flight, "open_sides": [side]}, {**twin, "positions": list(flight["positions"])}],
            "landing": {"along": (boundary, end) if towards < 0 else (end, boundary),
                        "across": (min(flight["a0"], a0), max(flight["a1"], a1)), "towards": towards,
                        "boundary_assumed": landing_wall is None},
            "direction_basis": plan["direction_basis"] + "; second flight assumed: mirrored against the opposite "
                                                         "wall (the drawing shows one flight)",
        })
        fit = _fit_dogleg(plan, storey_height_mm, drawn, rise)
        if fit:
            return fit
    return {"ok": False, "reason": f"the drawing shows {drawn} risers ({rise:.0f} mm each); a full stair does not "
                                   f"fit the walls found round it"}


# ---------------------------------------------------------------------------
# Geometry (page-local mm; z from the storey's base level)
# ---------------------------------------------------------------------------

def _xy(orient: str, along: float, across: float) -> tuple[float, float]:
    return (across, along) if orient == "h" else (along, across)


def _box(orient, p0, p1, a0, a1, z0, z1) -> list[list[float]]:
    (xa, ya), (xb, yb) = _xy(orient, min(p0, p1), a0), _xy(orient, max(p0, p1), a1)
    x0, x1, y0, y1 = min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb)
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


def _sloped(orient, p0, p1, a0, a1, z_at_p0, z_at_p1, thickness) -> list[list[float]]:
    """A plate along the flight whose top runs from z_at_p0 at p0 to z_at_p1 at p1 (box corner order)."""
    corners = [(p0, a0, z_at_p0), (p0, a1, z_at_p0), (p1, a1, z_at_p1), (p1, a0, z_at_p1)]
    pts = [(*_xy(orient, p, a), z) for p, a, z in corners]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
    if area < 0:
        pts = pts[::-1]
    return [[x, y, z - thickness] for x, y, z in pts] + [[x, y, z] for x, y, z in pts]


def _merge(boxes: list[list[list[float]]]) -> dict[str, Any]:
    vertices, faces = [], []
    for corners in boxes:
        offset = len(vertices)
        vertices += [[round(v, 3) for v in c] for c in corners]
        faces += [[offset + i for i in face] for face in BOX_FACES]
    return {"vertices": vertices, "faces": faces}


def build_stair(plan: dict[str, Any], storey_height_mm: float, waist_mm: float,
                rects: list[Rect] | None = None) -> dict[str, Any]:
    """Flight, landing and handrail meshes for a planned stair, or {"rejected": reason}."""
    fit = fit_risers(plan, storey_height_mm, rects)
    if not fit["ok"]:
        return {"rejected": fit["reason"]}
    orient, rise = plan["orient"], fit["rise_mm"]
    parts = []
    z = 0.0
    for n, flight in enumerate(plan["flights"]):
        climb = flight["climb"]
        a0, a1 = flight["a0"], flight["a1"]
        boxes = []
        for i in range(len(climb) - 1):                 # tread i between riser lines i and i+1
            top = z + (i + 1) * rise
            boxes.append(_box(orient, climb[i], climb[i + 1], a0, a1, top - rise, top))
        boxes.append(_sloped(orient, climb[0], climb[-1], a0, a1, z, z + (len(climb) - 1) * rise, waist_mm))
        steps_top = z + len(climb) * rise
        parts.append({"kind": "flight", "index": n, "mesh": _merge(boxes), "risers": len(climb),
                      "going_mm": flight["going_mm"], "width_mm": a1 - a0,
                      "run_mm": abs(climb[-1] - climb[0]), "from_z": z, "to_z": steps_top,
                      "climb": climb, "a": (a0, a1), "open_sides": flight.get("open_sides", [])})
        z = steps_top
        if n == 0 and plan.get("landing"):
            along, across = plan["landing"]["along"], plan["landing"]["across"]
            parts.append({"kind": "landing", "mesh": _merge([_box(orient, along[0], along[1], across[0], across[1],
                                                                   z - waist_mm, z)]),
                          "z": z, "along": along, "across": across,
                          "assumed": plan["landing"].get("boundary_assumed", False)})
    # Handrails (LOD 500 display sizes): a sloping rail 900 mm above the nosings on each open side, with balusters.
    rails = []
    for part in (p for p in parts if p["kind"] == "flight"):
        climb, z0, r = part["climb"], part["from_z"], rise
        if len(climb) < 2:
            continue
        for side in part["open_sides"]:
            inner = side == part["a"][1]
            s0, s1 = (side - RAIL_SIZE_DISPLAY_MM, side) if inner else (side, side + RAIL_SIZE_DISPLAY_MM)
            boxes = [_sloped(orient, climb[0], climb[-1], s0, s1, z0 + r + RAIL_HEIGHT_DISPLAY_MM,
                             z0 + len(climb) * r + RAIL_HEIGHT_DISPLAY_MM, RAIL_SIZE_DISPLAY_MM)]
            mid_rail = (s0 + s1) / 2
            b0, b1 = mid_rail - BALUSTER_DISPLAY_MM / 2, mid_rail + BALUSTER_DISPLAY_MM / 2
            for i in range(0, len(climb) - 1, 2):
                mid = (climb[i] + climb[i + 1]) / 2
                tread = z0 + (i + 1) * r
                t = (mid - climb[0]) / (climb[-1] - climb[0])
                rail_z = z0 + r + RAIL_HEIGHT_DISPLAY_MM + t * (len(climb) - 1) * r
                half = BALUSTER_DISPLAY_MM / 2
                boxes.append(_box(orient, mid - half, mid + half, b0, b1, tread, rail_z - RAIL_SIZE_DISPLAY_MM))
            rails.append({"kind": "railing", "mesh": _merge(boxes)})
    span = [p for f in plan["flights"] for p in f["climb"]]
    across = [v for f in plan["flights"] for v in (f["a0"], f["a1"])]
    along_lo, along_hi = min(span), max(span)
    if plan.get("landing"):
        along_lo = min(along_lo, *plan["landing"]["along"])
        along_hi = max(along_hi, *plan["landing"]["along"])
    (vx0, vy0), (vx1, vy1) = _xy(orient, along_lo, min(across)), _xy(orient, along_hi, max(across))
    return {"rise_mm": rise, "risers": fit["risers"], "count_basis": fit["count_basis"], "parts": parts + rails,
            "void_mm": (min(vx0, vx1), min(vy0, vy1), max(vx0, vx1), max(vy0, vy1))}


# ---------------------------------------------------------------------------
# Lifts
# ---------------------------------------------------------------------------

def lift_outline(cx: float, cy: float, rects: list[Rect], lines) -> tuple[Rect | None, str]:
    """The shaft round a LIFT label: its walls, else the nearest drawn lines (car outline)."""
    room = room_bounds(cx, cy, rects)
    if room and all(LIFT_SIZE_MM[0] <= s <= LIFT_SIZE_MM[1] for s in (room[2] - room[0], room[3] - room[1])):
        return room, "walls round the lift label"
    left = max((p for o, p, a0, a1 in lines if o == "v" and p < cx and min(a0, a1) <= cy <= max(a0, a1)
                and LIFT_SIZE_MM[0] <= abs(a1 - a0) <= LIFT_SIZE_MM[1] and cx - p <= LIFT_SIZE_MM[1]), default=None)
    right = min((p for o, p, a0, a1 in lines if o == "v" and p > cx and min(a0, a1) <= cy <= max(a0, a1)
                 and LIFT_SIZE_MM[0] <= abs(a1 - a0) <= LIFT_SIZE_MM[1] and p - cx <= LIFT_SIZE_MM[1]), default=None)
    top = max((p for o, p, a0, a1 in lines if o == "h" and p < cy and min(a0, a1) <= cx <= max(a0, a1)
               and LIFT_SIZE_MM[0] <= abs(a1 - a0) <= LIFT_SIZE_MM[1] and cy - p <= LIFT_SIZE_MM[1]), default=None)
    bottom = min((p for o, p, a0, a1 in lines if o == "h" and p > cy and min(a0, a1) <= cx <= max(a0, a1)
                  and LIFT_SIZE_MM[0] <= abs(a1 - a0) <= LIFT_SIZE_MM[1] and p - cy <= LIFT_SIZE_MM[1]), default=None)
    if None in (left, right, top, bottom):
        return None, "no walls or car outline found round the lift label"
    box = (left, top, right, bottom)
    if not all(LIFT_SIZE_MM[0] <= s <= LIFT_SIZE_MM[1] for s in (box[2] - box[0], box[3] - box[1])):
        return None, "the outline round the lift label is not lift-sized"
    return box, "the drawn car outline round the lift label"


def _overlap_share(a: Rect, b: Rect) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller > 0 else 0.0


def group_lift_stops(stops: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Lift outlines (building mm) on different storeys at the same place belong to one shaft."""
    shafts: list[list[dict[str, Any]]] = []
    for stop in sorted(stops, key=lambda s: s["base_mm"]):
        home = next((s for s in shafts if _overlap_share(s[0]["rect"], stop["rect"]) >= 0.5), None)
        if home is not None:
            home.append(stop)
        else:
            shafts.append([stop])
    return shafts


# ---------------------------------------------------------------------------
# Building model hook
# ---------------------------------------------------------------------------

def _to_building(mesh: dict[str, Any], offset: tuple[float, float], base: float) -> dict[str, Any]:
    return {"vertices": [[round(x - offset[0], 3), round(y - offset[1], 3), round(z + base, 3)]
                         for x, y, z in mesh["vertices"]], "faces": mesh["faces"]}


def _element(element_id, element_type, component, storey, mesh, *, dims, minimum_lod, provenance, detail=None,
             **extra) -> dict[str, Any]:
    return {
        "element_id": element_id, "element_type": element_type, "component": component,
        "detected_by": extra.pop("detected_by", "stair_lines"), "wall_type": None, "dimensions_mm": dims,
        "lod": {"category": element_type, "minimum_lod": minimum_lod},
        "height_status": extra.pop("height_status", "storey_height_over_risers"), "height_verification": None,
        "provenance": provenance, "stair": detail, "opening": None, "between": None, "host_component": None,
        "drawing_id": storey["drawing_id"], "drawing_title": storey.get("drawing_title"),
        "storey": {key: storey[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
        "mesh": mesh, "reviewed_by": None, "is_external": False, **extra,
    }


def _page_context(folder, storey, page, index):
    from pathlib import Path

    from app.services import hatch_wall_detection as hatch

    scale = page["scale"]
    sidecar = Path(folder) / "artifacts" / "primitive_sidecars" / f"{storey['drawing_id']}_page_{page['page_number']}.jsonl"
    if not sidecar.is_file():
        return None
    lines = [(o, p * scale, a * scale, b * scale) for o, p, a, b in hatch.axis_lines(sidecar)]
    rects = []
    for e in page["served"]:
        xs = [v[0] for v in e["mesh"]["vertices"]]; ys = [v[1] for v in e["mesh"]["vertices"]]
        rects.append((min(xs), min(ys), max(xs), max(ys)))
    entry = index.get(storey["drawing_id"]) or {}
    arrows = [(text, (box[0] + box[2]) / 2 * scale, (box[1] + box[3]) / 2 * scale)
              for text, box in entry.get("stair_arrows") or []]
    labels = [(name, ((box[0] + box[2]) / 2 * scale, (box[1] + box[3]) / 2 * scale))
              for name, box in entry.get("rooms") or []]
    return {"lines": lines, "rects": rects, "arrows": arrows, "labels": labels}


def building_stairs(folder, storeys: list[dict[str, Any]], pages: dict[str, dict[str, Any]],
                    waist_note: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Stair and lift elements; returns (elements, slab voids, notes).
    pages[placement_id] = {drawing_id, page_number, scale, offset, served}.
    """
    from app.services.sheet_registration import sheet_index

    index = sheet_index(folder)
    contexts = {}
    for storey in storeys:
        page = pages.get(storey["placement_id"])
        if not page:
            continue
        context = _page_context(folder, storey, page, index)
        if context is None:
            continue
        contexts[storey["placement_id"]] = {**context, "offset": page["offset"], "served": page["served"]}
    return stairs_from_contexts(storeys, contexts, waist_note)


def stairs_from_contexts(storeys: list[dict[str, Any]], contexts: dict[str, dict[str, Any]],
                         waist_note: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Stairs and lifts from each storey's plan context: {"lines": [(orient, pos, a0, a1)], "rects", "arrows":
    [(text, x, y)], "labels": [(name, (x, y))], "offset": (ox, oy), "served": elements}, in the plan's own mm
    frame (building frame + offset). PDF plans and CAD plans (offset 0) both come here.
    """
    ordered = sorted(storeys, key=lambda s: s["base_elevation_mm"])
    elements, voids, notes = [], [], []
    waist = float(waist_note["value_mm"]) if waist_note else WAIST_DISPLAY_MM
    lift_stops = []
    for position, storey in enumerate(ordered):
        context = contexts.get(storey["placement_id"])
        if context is None:
            continue
        page = context
        title = storey.get("drawing_title")
        above = next((s for s in ordered[position + 1:] if s["base_elevation_mm"] > storey["base_elevation_mm"]), None)
        ox, oy = page["offset"]
        base = float(storey["base_elevation_mm"])

        for name, (cx, cy) in context["labels"]:
            if any(w in name for w in LIFT_WORDS):
                outline, basis = lift_outline(cx, cy, context["rects"], context["lines"])
                if outline is None:
                    notes.append(f"{title}: {name}: {basis}; no lift built.")
                    continue
                capacity = CAPACITY.search(name)
                lift_stops.append({"storey": storey, "rect": (outline[0] - ox, outline[1] - oy, outline[2] - ox,
                                                              outline[3] - oy),
                                   "base_mm": base, "name": name, "basis": basis,
                                   "capacity_kg": float(capacity.group(1)) if capacity else None,
                                   "rects": [(r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy) for r in context["rects"]],
                                   "served": page["served"], "offset": (ox, oy)})

        stair_labels = [(name, c) for name, c in context["labels"] if any(w in name for w in STAIR_WORDS)]
        if not stair_labels:
            continue
        if above is None:
            notes.append(f"{title}: stair drawn on the top storey; nothing above it to reach, so it is not built again.")
            continue
        height = float(above["base_elevation_mm"]) - base
        built_here: list[Rect] = []
        for k, (name, centre) in enumerate(stair_labels):
            chosen = pick_flights(find_flights(candidate_lines(context["lines"], context["rects"], centre)), centre)
            plan = plan_stair(chosen, context["arrows"], context["rects"])
            if plan is None:
                notes.append(f"{title}: no riser lines found near the {name.lower()}; no stair built.")
                continue
            built = build_stair(plan, height, waist, context["rects"])
            if built.get("rejected"):
                notes.append(f"{title}: {name.lower()} not built: {built['rejected']}.")
                continue
            if any(_overlap_share(built["void_mm"], v) >= 0.5 for v in built_here):
                continue                                   # a second label on a stair already built
            built_here.append(built["void_mm"])
            prefix = f"stair:{storey['placement_id']}:{k}"
            provenance = {
                "room_label": name, "risers": built["risers"], "riser_count": built["count_basis"],
                "direction": plan["direction_basis"], "rise_mm": round(built["rise_mm"], 1),
                "rise_basis": f"storey height {height:.0f} mm ({storey.get('base_level_key')} to "
                              f"{above.get('base_level_key')}) / {built['risers']} risers",
                "waist_and_landing_thickness": (f"from the note \"{waist_note['text']}\"" if waist_note
                                                else f"{WAIST_DISPLAY_MM:.0f} mm, display only (not on the drawings)"),
            }
            for n, part in enumerate(built["parts"]):
                kind = part["kind"]
                mesh = _to_building(part["mesh"], page["offset"], base)
                if kind == "flight":
                    dims = {"length": round(part["run_mm"], 1), "thickness": round(part["width_mm"], 1),
                            "height": round(part["to_z"] - part["from_z"], 1)}
                    detail = {"risers": part["risers"], "rise_mm": round(built["rise_mm"], 1),
                              "going_mm": round(part["going_mm"], 1), "width_mm": round(part["width_mm"], 1)}
                    elements.append(_element(f"{prefix}:flight:{n}", "stair", "stair flight", storey, mesh, dims=dims,
                                             minimum_lod=100, provenance=provenance, detail=detail))
                elif kind == "landing":
                    dims = {"length": round(abs(part["along"][1] - part["along"][0]), 1),
                            "thickness": round(abs(part["across"][1] - part["across"][0]), 1), "height": None}
                    detail = {"landing_level_mm": round(part["z"] + base, 1)}
                    elements.append(_element(f"{prefix}:landing:{n}", "stair", "stair landing", storey, mesh, dims=dims,
                                             minimum_lod=100, detail=detail,
                                             provenance={**provenance, "landing_depth": "assumed: one flight width (no wall "
                                                         "found beyond the flights)" if part["assumed"]
                                                         else "to the wall beyond the flights"}))
                else:
                    elements.append(_element(f"{prefix}:railing:{n}", "railing", "stair handrail", storey, mesh,
                                             dims={"length": None, "thickness": None, "height": RAIL_HEIGHT_DISPLAY_MM},
                                             minimum_lod=500,
                                             provenance={**provenance, "display_only_sizes": ["handrail height and profile",
                                                                                               "balusters"]}))
            vx0, vy0, vx1, vy1 = built["void_mm"]
            voids.append({"placement_id": above["placement_id"], "rect_mm": [vx0 - ox, vy0 - oy, vx1 - ox, vy1 - oy],
                          "basis": f"opening over the stair from {title}"})
            notes.append(f"{title}: {name.lower()}: {plan['kind']} stair, {built['risers']} risers of "
                         f"{built['rise_mm']:.0f} mm from {storey.get('base_level_key')} to {above.get('base_level_key')}"
                         + ("" if built["count_basis"].endswith("drawn") else " (riser count assumed)") + ".")

    lift_elements, lift_voids, lift_notes = build_lifts(lift_stops, ordered)
    return elements + lift_elements, voids + lift_voids, notes + lift_notes


def build_lifts(stops: list[dict[str, Any]], ordered: list[dict[str, Any]]):
    elements, voids, notes = [], [], []
    for k, shaft in enumerate(group_lift_stops(stops)):
        lowest, highest = shaft[0], shaft[-1]
        top_storey = highest["storey"]
        top_mm = float(top_storey["base_elevation_mm"]) + float(top_storey.get("storey_height_mm") or 0)
        if top_mm <= lowest["base_mm"]:
            continue
        x0 = min(s["rect"][0] for s in shaft); y0 = min(s["rect"][1] for s in shaft)
        x1 = max(s["rect"][2] for s in shaft); y1 = max(s["rect"][3] for s in shaft)
        storey = lowest["storey"]
        capacity = next((s["capacity_kg"] for s in shaft if s["capacity_kg"]), None)
        levels = [s["storey"].get("base_level_key") for s in shaft]
        provenance = {"lift_label": lowest["name"], "outline": lowest["basis"], "stops": levels,
                      "capacity_kg": capacity}
        shaft_box = _merge([[[x0, y0, lowest["base_mm"]], [x1, y0, lowest["base_mm"]], [x1, y1, lowest["base_mm"]],
                             [x0, y1, lowest["base_mm"]], [x0, y0, top_mm], [x1, y0, top_mm], [x1, y1, top_mm],
                             [x0, y1, top_mm]]])
        elements.append(_element(f"lift:{k}:shaft", "lift shaft", "lift shaft", storey, shaft_box,
                                 dims={"length": round(x1 - x0, 1), "thickness": round(y1 - y0, 1),
                                       "height": round(top_mm - lowest["base_mm"], 1)},
                                 minimum_lod=100, provenance=provenance, detected_by="lift_label",
                                 height_status="lowest_stop_to_top_storey", lift={"capacity_kg": capacity, "stops": levels}))
        i = LIFT_CAR_INSET_DISPLAY_MM
        z0 = lowest["base_mm"]
        car = _merge([[[x0 + i, y0 + i, z0], [x1 - i, y0 + i, z0], [x1 - i, y1 - i, z0], [x0 + i, y1 - i, z0],
                       [x0 + i, y0 + i, z0 + LIFT_CAR_HEIGHT_DISPLAY_MM], [x1 - i, y0 + i, z0 + LIFT_CAR_HEIGHT_DISPLAY_MM],
                       [x1 - i, y1 - i, z0 + LIFT_CAR_HEIGHT_DISPLAY_MM], [x0 + i, y1 - i, z0 + LIFT_CAR_HEIGHT_DISPLAY_MM]]])
        elements.append(_element(f"lift:{k}:car", "lift car", "lift car", storey, car,
                                 dims={"length": None, "thickness": None, "height": None}, minimum_lod=100,
                                 provenance={**provenance, "display_only_sizes": ["car size (shaft less 100 mm)",
                                                                                  "car height 2400 mm"]},
                                 detected_by="lift_label", height_status="display_only",
                                 lift={"capacity_kg": capacity, "stops": levels}))
        for stop in shaft[1:]:
            voids.append({"placement_id": stop["storey"]["placement_id"], "rect_mm": [x0, y0, x1, y1],
                          "basis": f"lift shaft ({lowest['name']})"})
        # Gaps in the shaft's walls on each storey are the lift's landing doors: an opening straddling a shaft wall
        # (its centre within a wall's reach of the shaft and in line with the shaft's side).
        door_ids = []
        for stop in shaft:
            ox, oy = stop["offset"]
            for e in stop["served"]:
                if e.get("element_type") not in ("door", "opening"):
                    continue
                xs = [v[0] - ox for v in e["mesh"]["vertices"]]; ys = [v[1] - oy for v in e["mesh"]["vertices"]]
                cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                reach = LIFT_DOOR_TOUCH_MM + 300.0
                in_x, in_y = x0 <= cx <= x1, y0 <= cy <= y1
                near_x = x0 - reach <= cx <= x1 + reach
                near_y = y0 - reach <= cy <= y1 + reach
                if (in_x and near_y and not in_y) or (in_y and near_x and not in_x):
                    door_ids.append(e["element_id"])
        doors = len(door_ids)
        elements[-2]["lift"]["door_ids"] = door_ids
        notes.append(f"Lift ({lowest['name']}): shaft {x1 - x0:.0f} x {y1 - y0:.0f} mm from "
                     f"{levels[0]} to the top of {top_storey.get('drawing_title')}, {len(shaft)} stop(s), "
                     f"{doors} landing door(s) found in its walls.")
    return elements, voids, notes


def subtract_rect(rects: list[list[float]], hole: list[float]) -> list[list[float]]:
    """Rectangles minus one rectangular hole (each split into up to four pieces)."""
    hx0, hy0, hx1, hy1 = hole
    out = []
    for x0, y0, x1, y1 in rects:
        if hx0 >= x1 or hx1 <= x0 or hy0 >= y1 or hy1 <= y0:
            out.append([x0, y0, x1, y1])
            continue
        if y0 < hy0:
            out.append([x0, y0, x1, hy0])
        if hy1 < y1:
            out.append([x0, hy1, x1, y1])
        top, bottom = max(y0, hy0), min(y1, hy1)
        if x0 < hx0:
            out.append([x0, top, hx0, bottom])
        if hx1 < x1:
            out.append([hx1, top, x1, bottom])
    return [r for r in out if r[2] - r[0] > 1 and r[3] - r[1] > 1]
