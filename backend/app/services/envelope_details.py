"""
LOD 500 envelope details read from the drawings' notes: a gable roof, gutters and
downpipes, the plinth below the ground floor, and slab thickness.

Every size comes from a printed note:

* Roof. "ROOF: GABLE, 30 DEG PITCH, RIDGE PARALLEL TO GRID 1", "EAVES AND VERGE
  OVERHANG 450 MM", "ROOF SHEET: RED STANDING SEAM METAL". The eaves are the top
  storey's top level and the outline is its outer wall faces. The ridge direction
  comes from the named grid line's direction on the sheet. Gable walls fill the
  ends up to the roof.
* Gutters and downpipes, when a note names them ("PVC GUTTERS AND DOWNPIPES AT
  EAVES"): along each eave and down the corners to the ground.
* Plinth, from a ground-level note ("GROUND LEVEL: NGL +0.000") up to the lowest
  storey's base level, around the ground floor's outline.
* Slab thickness, from a note like "ALL RCC SLABS 150 THK."

Sizes that no drawing gives are only for display and are marked so: the roof
sheet's thickness, the gutter and downpipe profiles, the fascia, the ridge cap and
the plinth's projection. A roof form other than gable, a missing pitch or ridge
direction, or an outline that is not a rectangle gives no pitched roof, and the
model says why.
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
PRISM_FACES = [[0, 1, 2], [3, 5, 4], [0, 3, 4], [0, 4, 1], [1, 4, 5], [1, 5, 2], [2, 5, 3], [2, 3, 0]]

ENVELOPE_WORDS = re.compile(r"\b(?:ROOF|RIDGE|EAVES|VERGE|OVERHANG|GUTTERS?|DOWN ?PIPES?|RWP|NGL|FGL|GROUND LEVEL|SLABS?)\b",
                            re.IGNORECASE)
ROOF_FORM = re.compile(r"\b(GABLE|HIP(?:PED)?|FLAT|MONO ?PITCH|LEAN-TO|MANSARD|GAMBREL)\b", re.IGNORECASE)
PITCH_DEG = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*(?:°|DEG(?:REES?)?\b)", re.IGNORECASE)
PITCH_RATIO = re.compile(r"(?:PITCH|SLOPE)\s*(?:OF)?\s*[:=]?\s*(\d{1,2}(?:\.\d+)?)\s*(?::|IN)\s*(\d{1,2}(?:\.\d+)?)", re.IGNORECASE)
RIDGE_GRID = re.compile(r"RIDGE\s+(?:PARALLEL\s+TO|ALONG)\s+GRID(?:\s*LINE)?\s+([A-Z]{1,2}|\d{1,2})\b", re.IGNORECASE)
OVERHANG = re.compile(r"OVERHANG\s*(?:OF)?\s*[:=]?\s*(\d{2,4})\s*(?:MM)?\b", re.IGNORECASE)
SHEETS = (("STANDING SEAM", "standing seam metal"), ("TILE", "tiles"), ("SLATE", "slate"), ("SHINGLE", "shingles"),
          ("METAL", "metal sheet"))
COLOURS = {"RED": "#8f2f2b", "TERRACOTTA": "#a4532f", "BROWN": "#6b4a36", "GREEN": "#3f5f4a", "BLUE": "#3b5470",
           "GREY": "#5b6068", "GRAY": "#5b6068", "CHARCOAL": "#3a3d42", "BLACK": "#2b2d31", "WHITE": "#e9e7e1"}
GUTTER_WORDS = re.compile(r"\bGUTTERS?\b", re.IGNORECASE)
DOWNPIPE_WORDS = re.compile(r"\b(?:DOWN ?PIPES?|RWP|RAIN ?WATER PIPES?)\b", re.IGNORECASE)
SLAB_THICKNESS = re.compile(r"SLABS?\s+(?:ARE\s+)?(\d{2,3})\s*(?:MM)?\s*TH(?:IC)?K|(\d{2,3})\s*(?:MM)?\s*TH(?:IC)?K\.?\s*(?:RCC\s+)?SLABS?",
                            re.IGNORECASE)
GROUND_LEVEL = re.compile(r"\b(?:NGL|FGL|GROUND LEVEL)\b[^0-9+\-±]{0,20}([+\-±]?)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

MIN_PITCH_DEG, MAX_PITCH_DEG = 5.0, 60.0
RECTANGLE_SHARE = 0.85            # the top storey's outline must fill this share of its bounding rectangle

# Display-only sizes (no drawing gives them); every element using one says so.
ROOF_SHEET_DISPLAY_MM = 100.0
FASCIA_DISPLAY_MM = (25.0, 180.0)            # depth, height
GUTTER_DISPLAY_MM = (120.0, 110.0)           # width, height
DOWNPIPE_DISPLAY_MM = 80.0
RIDGE_CAP_DISPLAY_MM = (220.0, 60.0)
PLINTH_PROJECTION_DISPLAY_MM = 40.0


def is_envelope_text(text: str) -> bool:
    return bool(ENVELOPE_WORDS.search(text or ""))


def envelope_facts(texts: list[str]) -> dict[str, Any]:
    """Roof, gutter, downpipe, slab thickness and ground level facts printed in these texts."""
    facts: dict[str, Any] = {"roof": None, "gutters": None, "downpipes": None,
                             "slab_thickness_mm": None, "ground_level_mm": None}
    roof: dict[str, Any] = {"texts": []}
    for raw in texts:
        text = " ".join(str(raw or "").split())
        upper = text.upper()
        if re.search(r"\b(?:ROOF|RIDGE|EAVES|VERGE|OVERHANG)\b", upper):
            used = False
            form = ROOF_FORM.search(upper)
            if form and "form" not in roof:
                roof["form"] = form.group(1).lower().replace(" ", "").replace("hipped", "hip"); used = True
            if "pitch_deg" not in roof:
                degrees = PITCH_DEG.search(upper)
                ratio = PITCH_RATIO.search(upper)
                if degrees:
                    roof["pitch_deg"] = float(degrees.group(1)); used = True
                elif ratio and float(ratio.group(2)) > 0:
                    roof["pitch_deg"] = round(math.degrees(math.atan(float(ratio.group(1)) / float(ratio.group(2)))), 2)
                    used = True
            ridge = RIDGE_GRID.search(upper)
            if ridge and "ridge_grid" not in roof:
                roof["ridge_grid"] = ridge.group(1); used = True
            overhang = OVERHANG.search(upper)
            if overhang and "overhang_mm" not in roof:
                roof["overhang_mm"] = float(overhang.group(1)); used = True
            if re.search(r"\bROOF\b", upper) and ("SHEET" in upper or "COVERING" in upper or "FINISH" in upper):
                for word, name in SHEETS:
                    if word in upper and "sheet" not in roof:
                        roof["sheet"] = name; used = True
                        break
                for word, colour in COLOURS.items():
                    if re.search(rf"\b{word}\b", upper) and "colour" not in roof:
                        roof["colour"] = word.lower(); roof["color_hex"] = colour; used = True
                        break
            if used:
                roof["texts"].append(text)
        if GUTTER_WORDS.search(upper) and not facts["gutters"]:
            facts["gutters"] = text
        if DOWNPIPE_WORDS.search(upper) and not facts["downpipes"]:
            facts["downpipes"] = text
        slab = SLAB_THICKNESS.search(upper)
        if slab and not facts["slab_thickness_mm"]:
            facts["slab_thickness_mm"] = {"value_mm": float(slab.group(1) or slab.group(2)), "text": text}
        ground = GROUND_LEVEL.search(upper)
        if ground and not facts["ground_level_mm"]:
            value = float(ground.group(2)) * (-1 if ground.group(1) == "-" else 1)
            facts["ground_level_mm"] = {"value_mm": value * 1000.0 if "." in ground.group(2) else value, "text": text}
    if roof["texts"]:
        facts["roof"] = roof
    return facts


def grid_axes(labels: dict[str, list[tuple[float, float]]]) -> dict[str, str]:
    """Direction of each grid line from its bubbles: 'x' when the bubbles spread across the sheet, else 'y'."""
    axes = {}
    for label, points in (labels or {}).items():
        if len(points) < 2:
            continue
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        dx, dy = max(xs) - min(xs), max(ys) - min(ys)
        if max(dx, dy) > 3 * max(min(dx, dy), 1e-6):
            axes[label] = "x" if dx > dy else "y"
    return axes


# ---------------------------------------------------------------------------
# Geometry helpers (building frame: X right, Y plan-down, Z up; mm)
# ---------------------------------------------------------------------------

def _r(v: float) -> float:
    return round(float(v), 3)


def _quad_slab(corners: list[tuple[float, float, float]], thickness: float) -> list[list[float]]:
    """A sloped plate: the four top corners (in box order) and the same corners thickness lower."""
    bottom = [[_r(x), _r(y), _r(z - thickness)] for x, y, z in corners]
    top = [[_r(x), _r(y), _r(z)] for x, y, z in corners]
    return bottom + top


def _box(x0, y0, x1, y1, z0, z1) -> list[list[float]]:
    x0, y0, x1, y1, z0, z1 = (_r(v) for v in (x0, y0, x1, y1, z0, z1))
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


def _element(element_id: str, element_type: str, component: str, top: dict[str, Any], vertices, faces, *,
             dimensions: dict[str, Any], provenance: dict[str, Any], note: str | None, display_only: list[str],
             **extra) -> dict[str, Any]:
    return {
        "element_id": element_id, "element_type": element_type, "component": component,
        "detected_by": "envelope_note", "wall_type": extra.pop("wall_type", None),
        "dimensions_mm": dimensions,
        "lod": {"category": element_type, "minimum_lod": 500},
        "height_status": "drawing_note", "height_verification": None,
        "provenance": {**provenance, "display_only_sizes": display_only},
        "source_note": note, "opening": None, "between": None, "host_component": extra.pop("host_component", None),
        "drawing_id": top.get("drawing_id"), "drawing_title": top.get("drawing_title"),
        "storey": {key: top[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
        "mesh": {"vertices": vertices, "faces": faces},
        "reviewed_by": None, "is_external": True, **extra,
    }


def _outline(elements: list[dict[str, Any]], storey: dict[str, Any]):
    from app.services import building_model as bm

    outer = [e for e in elements if e["storey"]["placement_id"] == storey["placement_id"] and e.get("is_external")
             and bm._is_box(e)]
    if not outer:
        return None, [], None
    rects = [bm._rect(e) for e in outer]
    walls = [e for e in outer if e.get("element_type") == "wall"]
    thickness = median(min(r[2] - r[0], r[3] - r[1]) for r in (bm._rect(w) for w in walls)) if walls else None
    finishes = [w.get("component") for w in walls if w.get("component")]
    finish = max(set(finishes), key=finishes.count) if finishes else None
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)), \
        outer, (thickness, finish)


# ---------------------------------------------------------------------------
# Gable roof
# ---------------------------------------------------------------------------

def gable_roof(storeys: list[dict[str, Any]], elements: list[dict[str, Any]], slabs: list[dict[str, Any]],
               facts: dict[str, Any], axes: dict[str, str]) -> tuple[list[dict[str, Any]], str | None]:
    """Roof planes, gable walls, fascia, ridge cap, gutters and downpipes, or ([], reason)."""
    roof = facts.get("roof") or {}
    if not roof:
        return [], None
    form, pitch, grid = roof.get("form"), roof.get("pitch_deg"), roof.get("ridge_grid")
    note = "; ".join(roof["texts"])
    if form != "gable":
        return [], f"The roof note gives a {form or 'unstated'} roof; only gable roofs are built, so the roof stays flat."
    if pitch is None or not MIN_PITCH_DEG <= pitch <= MAX_PITCH_DEG:
        return [], "The roof note gives no usable pitch, so the roof stays flat."
    axis = axes.get(grid) if grid else None
    if axis is None:
        return [], "The roof note does not name a grid line the ridge runs along (or that grid line was not found)."
    top = max(storeys, key=lambda s: s["base_elevation_mm"])
    if not top.get("storey_height_mm"):
        return [], "The top storey has no height, so the eaves level is unknown."
    rect, _, wall_info = _outline(elements, top)
    if rect is None or wall_info[0] is None:
        return [], "The top storey's outer walls were not found."
    x0, y0, x1, y1 = rect
    roof_slab = next((s for s in slabs if s.get("role") == "roof"), None)
    area = (roof_slab or {}).get("footprint", {}) or {}
    if area.get("area_m2") and area["area_m2"] * 1e6 < RECTANGLE_SHARE * (x1 - x0) * (y1 - y0):
        return [], "The top storey's outline is not a rectangle; a gable roof over it is not built."

    t_wall, finish = wall_info
    eaves = float(top["base_elevation_mm"]) + float(top["storey_height_mm"])
    slope = math.tan(math.radians(pitch))
    o = float(roof.get("overhang_mm") or 0.0)
    d = ROOF_SHEET_DISPLAY_MM
    along_x = axis == "x"
    # Work in (a, b): a along the ridge, b across it; map back to (x, y).
    a0, a1, b0, b1 = (x0, x1, y0, y1) if along_x else (y0, y1, x0, x1)

    def xy(a, b):
        return (a, b) if along_x else (b, a)

    bm = (b0 + b1) / 2
    rise = (bm - b0) * slope
    ridge_z = eaves + rise
    eave_z = eaves - o * slope
    sheet = roof.get("sheet") or "roof sheet"
    colour = roof.get("color_hex")
    base_prov = {"roof_note": note, "eaves_level_key": top.get("top_level_key"), "pitch_deg": pitch,
                 "ridge_parallel_to_grid": grid, "overhang_mm": o}
    common = {"roof": {"form": "gable", "pitch_deg": pitch, "ridge_axis": axis, "overhang_mm": o, "sheet": sheet,
                       "colour": roof.get("colour")}}
    pieces = []
    for side, (b_eave, b_ridge) in (("a", (b0 - o, bm)), ("b", (b1 + o, bm))):
        z_eave = eave_z
        corners_ab = [(a0 - o, b_eave, z_eave), (a1 + o, b_eave, z_eave), (a1 + o, b_ridge, ridge_z),
                      (a0 - o, b_ridge, ridge_z)]
        corners = [(*xy(a, b), z) for a, b, z in corners_ab]
        area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(corners, corners[1:] + corners[:1]))
        if area < 0:                                    # keep the box corner order the faces expect
            corners = corners[::-1]
        slope_len = math.hypot(abs(b_ridge - b_eave), ridge_z - z_eave)
        pieces.append(_element(
            f"roof:{top['placement_id']}:{side}", "roof", sheet, top, _quad_slab(corners, d), BOX_FACES,
            dimensions={"length": _r(a1 - a0 + 2 * o), "thickness": None, "height": _r(slope_len)},
            provenance={**base_prov, "plan_from": "outer faces of the top storey's walls"},
            note=note, display_only=["roof sheet thickness"], color=colour, **common))

    gable_apex = ridge_z - d
    for end, (a_lo, a_hi) in (("start", (a0, a0 + t_wall)), ("end", (a1 - t_wall, a1))):
        pts = []
        for a in (a_lo, a_hi):
            pts += [(*xy(a, b0), eaves), (*xy(a, b1), eaves), (*xy(a, bm), gable_apex)]
        pieces.append(_element(
            f"gable:{top['placement_id']}:{end}", "wall", finish or "wall", top,
            [[_r(x), _r(y), _r(z)] for x, y, z in pts], PRISM_FACES,
            dimensions={"length": _r(b1 - b0), "thickness": _r(t_wall), "height": _r(gable_apex - eaves)},
            provenance={**base_prov, "plan_from": "the gable end walls of the top storey, up to the roof"},
            note=note, display_only=[], wall_type="gable", host_component=finish))

    fd, fh = FASCIA_DISPLAY_MM
    rw, rh = RIDGE_CAP_DISPLAY_MM
    for side, b in (("a", b0 - o), ("b", b1 + o)):
        lo, hi = (b - fd, b) if side == "a" else (b, b + fd)
        bx = _box(*xy(a0 - o, lo), *xy(a1 + o, hi), eave_z - fh, eave_z)
        pieces.append(_element(
            f"fascia:{top['placement_id']}:{side}", "fascia", "fascia board", top, _norm_box(bx), BOX_FACES,
            dimensions={"length": _r(a1 - a0 + 2 * o), "thickness": None, "height": None},
            provenance={**base_prov, "plan_from": "along the eaves"}, note=note, display_only=["fascia profile"]))
    ridge_box = _box(*xy(a0 - o, bm - rw / 2), *xy(a1 + o, bm + rw / 2), ridge_z - rh / 2, ridge_z + rh / 2)
    pieces.append(_element(
        f"ridge:{top['placement_id']}", "ridge", "ridge cap", top, _norm_box(ridge_box), BOX_FACES,
        dimensions={"length": _r(a1 - a0 + 2 * o), "thickness": None, "height": None},
        provenance={**base_prov, "plan_from": "along the ridge"}, note=note, display_only=["ridge cap profile"]))

    gutter_note, downpipe_note = facts.get("gutters"), facts.get("downpipes")
    ground = (facts.get("ground_level_mm") or {}).get("value_mm")
    if ground is None:
        ground = min(float(s["base_elevation_mm"]) for s in storeys)
    gw, gh = GUTTER_DISPLAY_MM
    if gutter_note:
        for side, b in (("a", b0 - o), ("b", b1 + o)):
            lo, hi = (b - fd - gw, b - fd) if side == "a" else (b + fd, b + fd + gw)
            pieces.append(_element(
                f"gutter:{top['placement_id']}:{side}", "gutter", "gutter", top,
                _norm_box(_box(*xy(a0 - o, lo), *xy(a1 + o, hi), eave_z - gh - 20, eave_z - 20)), BOX_FACES,
                dimensions={"length": _r(a1 - a0 + 2 * o), "thickness": None, "height": None},
                provenance={**base_prov, "gutter_note": gutter_note, "plan_from": "along the eaves"},
                note=gutter_note, display_only=["gutter profile"]))
    if downpipe_note:
        p = DOWNPIPE_DISPLAY_MM
        n = 0
        for side, b_face in (("a", b0), ("b", b1)):
            for a in (a0 + 150, a1 - 150 - p):
                lo, hi = (b_face - p, b_face) if side == "a" else (b_face, b_face + p)
                top_z = eave_z - gh - 20
                pieces.append(_element(
                    f"downpipe:{top['placement_id']}:{n}", "downpipe", "downpipe", top,
                    _norm_box(_box(*xy(a, lo), *xy(a + p, hi), ground, top_z)), BOX_FACES,
                    dimensions={"length": None, "thickness": None, "height": _r(top_z - ground)},
                    provenance={**base_prov, "downpipe_note": downpipe_note,
                                "plan_from": "at the ends of each eave, down the wall to the ground"},
                    note=downpipe_note, display_only=["downpipe profile"]))
                # Swan neck: from the wall out to the gutter.
                g_lo, g_hi = (b_face - o - fd - gw / 2, b_face) if side == "a" else (b_face, b_face + o + fd + gw / 2)
                pieces.append(_element(
                    f"downpipe:{top['placement_id']}:{n}:neck", "downpipe", "downpipe", top,
                    _norm_box(_box(*xy(a, g_lo), *xy(a + p, g_hi), top_z - p, top_z)), BOX_FACES,
                    dimensions={"length": None, "thickness": None, "height": None},
                    provenance={**base_prov, "downpipe_note": downpipe_note, "plan_from": "from the gutter to the wall"},
                    note=downpipe_note, display_only=["downpipe profile"]))
                n += 1
    for slab in slabs:
        if slab.get("role") == "roof":
            slab["role"] = "ceiling"
            slab["basis"] = (slab.get("basis") or "") + "; under a gable roof, so shown as the top floor's ceiling"
    return pieces, (f"Gable roof, {pitch:g} degree pitch, ridge parallel to grid {grid}"
                    f"{f', {o:.0f} mm overhang' if o else ''}, from the note: {note}")


def _norm_box(vertices: list[list[float]]) -> list[list[float]]:
    """Re-order a box's corners so x0 <= x1 and y0 <= y1 (the faces assume that order)."""
    xs = sorted({v[0] for v in vertices}); ys = sorted({v[1] for v in vertices}); zs = sorted({v[2] for v in vertices})
    return _box(xs[0], ys[0], xs[-1], ys[-1], zs[0], zs[-1])


# ---------------------------------------------------------------------------
# Plinth and slab thickness
# ---------------------------------------------------------------------------

def plinth(storeys: list[dict[str, Any]], elements: list[dict[str, Any]], slabs: list[dict[str, Any]],
           facts: dict[str, Any]) -> list[dict[str, Any]]:
    ground = facts.get("ground_level_mm")
    if not ground or not storeys:
        return []
    low = min(storeys, key=lambda s: s["base_elevation_mm"])
    base = float(low["base_elevation_mm"])
    if base - ground["value_mm"] < 50:
        return []
    slab = next((s for s in slabs if s["element_id"] == f"slab:{low['placement_id']}:base" and s.get("footprint")), None)
    _, _, wall_info = _outline(elements, low)
    if slab is None or not wall_info or wall_info[0] is None:
        return []
    rects = slab["footprint"]["rects_mm"]

    def inside(x, y):
        return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects)

    t, p = wall_info[0], PLINTH_PROJECTION_DISPLAY_MM
    pieces = []
    for n, (ex0, ey0, ex1, ey1) in enumerate(slab["footprint"]["edges_mm"]):
        if abs(ey0 - ey1) < 1e-6:                       # horizontal edge
            mx, y = (ex0 + ex1) / 2, ey0
            out = 1 if inside(mx, y - 5) else -1        # outside is +y when the inside is above
            lo, hi = sorted((y - out * t, y + out * p))
            box = _box(min(ex0, ex1) - p, lo, max(ex0, ex1) + p, hi, ground["value_mm"], base)
        else:
            my, x = (ey0 + ey1) / 2, ex0
            out = 1 if inside(x - 5, my) else -1
            lo, hi = sorted((x - out * t, x + out * p))
            box = _box(lo, min(ey0, ey1) - p, hi, max(ey0, ey1) + p, ground["value_mm"], base)
        pieces.append(_element(
            f"plinth:{low['placement_id']}:{n}", "plinth", "plinth", low, box, BOX_FACES,
            dimensions={"length": _r(math.hypot(ex1 - ex0, ey1 - ey0)), "thickness": _r(t),
                        "height": _r(base - ground["value_mm"])},
            provenance={"ground_level_note": ground["text"], "base_level_key": low.get("base_level_key"),
                        "plan_from": "the ground floor's outline"},
            note=ground["text"], display_only=["plinth projection beyond the wall face"]))
    return pieces


def apply_slab_thickness(slabs: list[dict[str, Any]], facts: dict[str, Any]) -> None:
    note = facts.get("slab_thickness_mm")
    if not note:
        return
    for slab in slabs:
        slab["thickness_mm"] = note["value_mm"]
        slab["thickness_basis"] = f"from the note \"{note['text']}\""
