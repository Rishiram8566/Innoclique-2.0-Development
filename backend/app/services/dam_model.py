"""
Concrete gravity dam with a gated ogee spillway, from its drawing notes.

Read from the drawing (each value is kept with its source line):
top of dam (crest) level; deepest foundation level and the foundation levels along the
axis (CH. x - EL. y table); dam length; top width; upstream and downstream face slopes;
FRL, MWL and TWL; spillway crest level, bays, bay width, pier thickness and location
(CH. a TO CH. b); gate type and size; block (monolith) length; stilling basin length
and floor level; crest road width and parapet height.

Crest level, foundation level, length, top width and downstream slope are required.
Without the foundation table the foundation is level along the whole axis, and the
model says so.

Section: vertical (or battered) upstream face; the top width; then the downstream
slope line through the crest's upstream edge, down to the foundation. The spillway
crest follows the standard WES ogee for the design head (MWL or FRL minus crest),
joining the downstream slope tangentially.

Sizes no drawing gives are display values and say so: pier and bridge length, gate
radius, basin slab and end sill, training wall height, reservoir and tailwater
extent, and the valley ground beyond the abutments.

LOD 100: dam massing. 200: monoliths and spillway bays, piers. 300: ogee profile,
radial gates, stilling basin, training walls. 400: hoist bridge and houses, gate
arms, parapets. 500: road surface, reservoir and tailwater, valley ground, finishes.
"""

from __future__ import annotations

import math
from typing import Any

from app.services import structure_common as sc

NUM = sc.NUMBER
CONC, CONC_DARK, STEEL, ROAD, WATER, GROUND, HOUSE = "#b8b3a7", "#9d988c", "#4f6d8f", "#3b3d40", "#3a7ca5", "#7c8a5c", "#e8e6df"


def read(facts: sc.Facts) -> dict[str, Any]:
    f = facts.find
    p: dict[str, Any] = {}
    p["crest"] = f("crest_level", rf"(?:TOP OF DAM|DAM TOP|T\.?O\.?D\.?|CREST LEVEL)\D{{0,15}}?\+?({NUM})\s*M?\b", label="top of dam (crest) level")
    p["foundation"] = f("foundation_level", rf"(?:DEEPEST )?FOUNDATION (?:LEVEL|EL)\D{{0,10}}?\+?({NUM})", label="deepest foundation level")
    p["length"] = f("length", rf"(?:LENGTH OF DAM(?: AT TOP)?|CREST LENGTH|DAM LENGTH)\D{{0,12}}?({NUM})\s*M", label="dam length at top")
    p["top_width"] = f("top_width", rf"(?:TOP|CREST) WIDTH\D{{0,10}}?({NUM})\s*M")
    us = facts.has(r"(?:U/S|UPSTREAM) (?:FACE|SLOPE)")
    if us and "VERTICAL" in us["upper"]:
        p["us"] = 0.0
        facts.note("upstream_face", "vertical", us)
    else:
        p["us"] = f("upstream_slope", rf"(?:U/S|UPSTREAM) (?:FACE|SLOPE)\D{{0,10}}?({NUM})\s*H\s*:\s*1\s*V") or 0.0
    p["ds"] = f("downstream_slope", rf"(?:D/S|DOWNSTREAM) (?:FACE|SLOPE)\D{{0,10}}?({NUM})\s*H\s*:\s*1\s*V", label="downstream slope (H per 1 V)")
    p["frl"] = f("frl", rf"\b(?:FRL|F\.R\.L\.?|FULL RESERVOIR LEVEL)\D{{0,8}}?\+?({NUM})", label="full reservoir level")
    p["mwl"] = f("mwl", rf"\b(?:MWL|M\.W\.L\.?|MAXIMUM WATER LEVEL)\D{{0,8}}?\+?({NUM})", label="maximum water level")
    p["twl"] = f("twl", rf"\b(?:TWL|T\.W\.L\.?|TAIL ?WATER LEVEL)\D{{0,8}}?\+?({NUM})", label="tail water level")
    p["spill_crest"] = f("spillway_crest", rf"SPILLWAY[^\n]*?CREST\D{{0,8}}?\+?({NUM})", label="spillway crest level")
    bays = facts.find_all(rf"(\d+)\s*(?:NOS\.?\s*)?BAYS?\s*(?:OF|@|X)\s*({NUM})\s*M")
    if bays:
        m, line = bays[0]
        p["bays"], p["bay_width"] = int(m.group(1)), float(m.group(2))
        facts.note("spillway_bays", f"{p['bays']} x {p['bay_width']:g} m", line)
    else:
        p["bays"] = p["bay_width"] = None
    p["pier"] = f("pier_thickness", rf"PIERS?\D{{0,8}}?({NUM})\s*M\s*(?:THICK|WIDE)")
    gates = facts.find_all(rf"(RADIAL|VERTICAL|LIFT|TAINTER)\s*GATES?\D{{0,8}}?({NUM})\s*[X×]\s*({NUM})\s*M")
    if gates:
        m, line = gates[0]
        p["gate"] = (m.group(1).lower(), float(m.group(2)), float(m.group(3)))
        facts.note("gates", f"{p['gate'][0]} {p['gate'][1]:g} x {p['gate'][2]:g} m", line)
    else:
        p["gate"] = None
    where = facts.find_all(rf"SPILLWAY[^\n]*?CH\.?\s*({NUM})\s*M?\s*TO\s*CH\.?\s*({NUM})")
    if where:
        m, line = where[0]
        p["spill_from"], p["spill_to"] = float(m.group(1)), float(m.group(2))
        facts.note("spillway_location", f"CH. {p['spill_from']:g} to {p['spill_to']:g} m", line)
    else:
        p["spill_from"] = p["spill_to"] = None
    p["block"] = f("block_length", rf"(?:BLOCK|MONOLITH)S?\s*(?:LENGTH)?\D{{0,8}}?({NUM})\s*M") or None
    basin = facts.find_all(rf"STILLING BASIN\D{{0,8}}?({NUM})\s*M\s*LONG[^\n]*?\+?({NUM})")
    if basin:
        m, line = basin[0]
        p["basin"] = (float(m.group(1)), float(m.group(2)))
        facts.note("stilling_basin", f"{p['basin'][0]:g} m long, floor {p['basin'][1]:g}", line)
    else:
        p["basin"] = None
    p["road"] = f("crest_road", rf"(?:CREST )?ROAD(?:WAY)?\D{{0,8}}?({NUM})\s*M\s*WIDE")
    p["parapet"] = f("parapet", rf"PARAPET\D{{0,8}}?({NUM})\s*M\s*HIGH")
    profile = []
    for m, line in facts.find_all(rf"CH\.?\s*({NUM})\s*M?\s*[-–:]\s*EL\.?\s*\+?({NUM})"):
        profile.append((float(m.group(1)), float(m.group(2))))
    if profile:
        profile.sort()
        facts.used["foundation_profile"] = {"name": "foundation levels along the axis", "value": f"{len(profile)} points",
                                            "text": "; ".join(f"CH {c:g}: {e:g}" for c, e in profile)}
    p["profile"] = profile
    return p


REQUIRED = {"crest": "top of dam level", "foundation": "foundation level", "length": "dam length",
            "top_width": "top width", "ds": "downstream slope"}


def foundation_at(p, ch_m: float) -> float:
    prof = p["profile"]
    if not prof:
        return p["foundation"]
    if ch_m <= prof[0][0]:
        return prof[0][1]
    for (c0, e0), (c1, e1) in zip(prof, prof[1:]):
        if c0 <= ch_m <= c1:
            return e0 + (e1 - e0) * (ch_m - c0) / max(c1 - c0, 1e-6)
    return prof[-1][1]


def nof_section(crest, found, top, ds, us):
    """Non-overflow section in (y, z) mm: y = 0 at the upstream face's top edge, downstream positive."""
    h = crest - found
    if h <= 0:
        return None
    toe = ds * h
    up = -us * h
    if top / ds >= h or toe <= top:                       # short block near an abutment: a plain block
        return [(up, found), (0, crest), (top, crest), (top, found)]
    return [(up, found), (0, crest), (top, crest), (top, crest - top / ds), (toe, found)]


def ogee_section(spill, found, hd, ds):
    """WES ogee crest for design head hd, tangent to the downstream slope, then down to the foundation."""
    pts = [(0, found), (0, spill - 0.13 * hd), (0.12 * hd, spill - 0.03 * hd), (0.28 * hd, spill)]
    x_t = (2 * hd ** 0.85 / (1.85 * ds)) ** (1 / 0.85)
    steps = 10
    for i in range(1, steps + 1):
        x = x_t * i / steps
        pts.append((0.28 * hd + x, spill - x ** 1.85 / (2 * hd ** 0.85)))
    y_t, z_t = pts[-1]
    if z_t <= found:                                   # a low spillway: the ogee reaches the foundation first
        return pts[:-1] + [(y_t, found)], (y_t, found)
    pts.append((y_t + ds * (z_t - found), found))
    return pts, (y_t, z_t)


def build(project_id: str, facts: sc.Facts, files: list[str], drawing: dict[str, Any]) -> dict[str, Any]:
    p = read(facts)
    lacking = [name for key, name in REQUIRED.items() if p.get(key) is None]
    crest_mm = (p.get("crest") or 0) * 1000
    storey = sc.structure_storey(drawing.get("title"), drawing.get("id"), (p.get("foundation") or 0) * 1000,
                                 crest_mm - (p.get("foundation") or 0) * 1000, "foundation")
    if lacking:
        return sc.model(project_id, "water dam", storey, [], facts, LOD_NOTES,
                        ["No dam built: the drawings do not give " + ", ".join(lacking) + "."], files)
    notes = []
    if not p["profile"]:
        notes.append("No foundation levels along the axis are printed: the foundation is taken as level throughout.")
    L = p["length"] * 1000
    C, T, ds, us = crest_mm, p["top_width"] * 1000, p["ds"], p["us"]
    block = (p["block"] or 15.0) * 1000
    if not p["block"]:
        facts.assume("block_length", 15.0, "display joint spacing: no block length printed")
    spill = None
    if p["spill_crest"] and p["bays"] and p["bay_width"]:
        pier = (p["pier"] or 3.0) * 1000
        width = p["bays"] * p["bay_width"] * 1000 + (p["bays"] - 1) * pier
        s0 = p["spill_from"] * 1000 if p["spill_from"] is not None else (L - width) / 2
        spill = {"from": s0, "to": s0 + width, "crest": p["spill_crest"] * 1000, "pier": pier,
                 "hd": max(1000.0, ((p["mwl"] or p["frl"] or p["spill_crest"] + 3) - p["spill_crest"]) * 1000)}
    prov = {"basis": "dam parameters printed on the drawing", "parameters": "see the model's parameter list"}
    elements: list[dict[str, Any]] = []
    E = lambda *a, **k: elements.append(sc.element(*a, storey=storey, provenance=k.pop("provenance", prov), **k))  # noqa: E731

    # Block boundaries: every block length, plus the spillway's ends.
    cuts = sorted({0.0, L, *(x for x in _range(block, L, block))} | ({spill["from"], spill["to"]} if spill else set()))
    massing = sc.Mesh()
    lowest = min(foundation_at(p, x / 1000) for x in cuts) * 1000
    for n, (x0, x1) in enumerate(zip(cuts, cuts[1:])):
        mid = (x0 + x1) / 2
        found = foundation_at(p, mid / 1000) * 1000
        in_spill = spill and spill["from"] - 1 <= x0 and x1 <= spill["to"] + 1
        if in_spill:
            continue
        section = nof_section(C, found, T, ds, us)
        if not section:
            continue
        massing.prism_x(section, x0, x1, CONC)
        E(f"dam:block:{n}", "dam block", "non-overflow block", sc.Mesh().prism_x(section, x0, x1, CONC), lod=(200, 500),
          dimensions={"length": round(x1 - x0), "thickness": round(max(y for y, _ in section) - min(y for y, _ in section)),
                      "height": round(C - found)})

    if spill:
        found_s = min(foundation_at(p, x / 1000) for x in (spill["from"], (spill["from"] + spill["to"]) / 2, spill["to"])) * 1000
        S, hd, pier = spill["crest"], spill["hd"], spill["pier"]
        bay_w = p["bay_width"] * 1000
        ogee, tangent = ogee_section(S, found_s, hd, ds)
        flat = [(0, found_s), (0, S), (ogee[-1][0], S), (ogee[-1][0], found_s)]
        flat = nof_section(S, found_s, T, ds, us) or flat
        x = spill["from"]
        piers = []
        for b in range(p["bays"]):
            massing.prism_x(flat, x, x + bay_w, CONC)
            E(f"dam:spillway:{b}:block", "dam block", "spillway bay", sc.Mesh().prism_x(flat, x, x + bay_w, CONC), lod=(200, 200))
            E(f"dam:spillway:{b}", "dam block", "ogee spillway bay", sc.Mesh().prism_x(ogee, x, x + bay_w, CONC), lod=(300, 500),
              dimensions={"length": bay_w, "thickness": round(ogee[-1][0]), "height": round(S - found_s)})
            x += bay_w
            if b < p["bays"] - 1:
                piers.append((x, x + pier))
                x += pier
        # Piers rise from the spillway to the crest; display length upstream and over the crest.
        for k, (a, b) in enumerate(piers):
            pier_mesh = sc.Mesh().prism_x([(-3000, S - 6000), (-3000, C), (tangent[0], C), (tangent[0], S - 6000)], a, b, CONC_DARK)
            E(f"dam:pier:{k}", "dam block", "spillway pier", pier_mesh, lod=(200, 500),
              provenance={**prov, "display_only_sizes": ["pier nose 3 m upstream", "pier length to the ogee tangent"]})
        # Radial gates (LOD 300) and their arms, hoist bridge and hoist houses (LOD 400).
        gate_w = bay_w
        gate_h = (p["gate"][2] if p["gate"] else (C - S) / 1000 - 1) * 1000
        radius = 1.25 * gate_h
        bridge = sc.Mesh()
        houses = sc.Mesh()
        for b in range(p["bays"]):
            x0 = spill["from"] + b * (bay_w + pier)
            gate, arms = _radial_gate(x0, x0 + gate_w, 0.28 * hd, S, gate_h, radius)
            E(f"dam:gate:{b}", "gate", f"{(p['gate'] or ('radial',))[0]} gate", gate, lod=(300, 500),
              dimensions={"length": gate_w, "thickness": None, "height": gate_h},
              provenance={**prov, "display_only_sizes": ["gate radius 1.25 x gate height"]})
            E(f"dam:gate:{b}:arms", "gate", "gate arms and trunnions", arms, lod=(400, 500))
            houses.box(x0 + gate_w * 0.3, -2500, C, x0 + gate_w * 0.7, 2500, C + 3500, HOUSE)
        bridge.box(spill["from"] - 500, -3500, C - 1500, spill["to"] + 500, 3500, C, CONC_DARK)
        E("dam:hoist_bridge", "dam block", "hoist bridge", bridge, lod=(400, 500),
          provenance={**prov, "display_only_sizes": ["bridge 7 m wide, 1.5 m deep"]})
        E("dam:hoist_houses", "dam block", "gate hoist houses", houses, lod=(400, 500),
          provenance={**prov, "display_only_sizes": ["hoist house 3.5 m high"]})
        # Stilling basin and training walls (LOD 300).
        if p["basin"]:
            length, floor = p["basin"][0] * 1000, p["basin"][1] * 1000
            y0 = ogee[-1][0]
            basin = sc.Mesh().box(spill["from"], y0, floor - 2000, spill["to"], y0 + length, floor, CONC)
            basin.box(spill["from"], y0 + length - 3000, floor, spill["to"], y0 + length, floor + 2500, CONC_DARK)
            E("dam:stilling_basin", "dam block", "stilling basin", basin, lod=(300, 500),
              dimensions={"length": length, "thickness": spill["to"] - spill["from"], "height": None},
              provenance={**prov, "display_only_sizes": ["slab 2 m thick", "end sill 2.5 m high"]})
            top = ((p["twl"] or floor / 1000 + 6) * 1000) + 3000
            walls = sc.Mesh()
            for xw in (spill["from"] - 1500, spill["to"]):
                walls.prism_x([(tangent[0], tangent[1]), (tangent[0], max(top, tangent[1])), (y0 + length, top), (y0 + length, floor - 2000)],
                              xw, xw + 1500, CONC_DARK)
            E("dam:training_walls", "dam block", "training walls", walls, lod=(300, 500),
              provenance={**prov, "display_only_sizes": ["training walls 1.5 m thick, 3 m above TWL"]})
    E("dam:massing", "dam block", "dam massing", massing, lod=(100, 100),
      dimensions={"length": L, "thickness": None, "height": C - lowest})

    # Crest road, kerbs and parapets (LOD 400-500).
    road = sc.Mesh()
    parapet_h = (p["parapet"] or 1.0) * 1000
    if not p["parapet"]:
        facts.assume("parapet", 1.0, "display height: no parapet height printed")
    for y0, y1 in ((0, 300), (T - 300, T)):
        road.box(0, y0, C, L, y1, C + parapet_h, CONC_DARK)
    E("dam:parapets", "dam block", "crest parapets", road, lod=(400, 500))
    asphalt = sc.Mesh().box(0, 300, C, L, T - 300, C + 60, ROAD)
    E("dam:road", "dam block", "crest road", asphalt, lod=(500, 500))

    # Water and ground (LOD 500): reservoir up to FRL, tailwater to TWL, ground beyond the abutments.
    lowest_m = min(foundation_at(p, c / 1000) for c in cuts) * 1000
    if p["frl"]:
        x_a, x_b = _crossings(p, p["frl"], L)
        water = sc.Mesh().box(x_a, -220000, lowest_m, x_b, -100 if us == 0 else -us * (C - lowest_m), p["frl"] * 1000, WATER, 0.6)
        E("dam:reservoir", "water", "reservoir at FRL", water, lod=(500, 500),
          provenance={**prov, "display_only_sizes": ["reservoir drawn 220 m upstream"]})
    if p["twl"]:
        x_a, x_b = _crossings(p, p["twl"], L)
        start = (spill and (ogee[-1][0] + (p["basin"][0] * 1000 if p["basin"] else 0))) or ds * (C - lowest_m)
        tail = sc.Mesh().box(x_a, start, lowest_m, x_b, start + 140000, p["twl"] * 1000, WATER, 0.6)
        E("dam:tailwater", "water", "tailwater at TWL", tail, lod=(500, 500),
          provenance={**prov, "display_only_sizes": ["tailwater drawn 140 m downstream"]})
    # The valley: the drawing's foundation levels along the axis, carried up and downstream, with level ground
    # beyond the abutments.
    valley = [(-80000, C + 6000), (0, C + 6000)]
    chainages = [c for c, _ in p["profile"]] or [0.0, p["length"]]
    for ch in chainages:
        valley.append((ch * 1000, foundation_at(p, ch) * 1000))
    valley += [(L, C + 6000), (L + 80000, C + 6000)]
    ground = sc.Mesh().terrain_y(valley, lowest_m - 5000, -230000, 180000, GROUND)
    E("dam:ground", "terrain", "valley ground", ground, lod=(500, 500),
      provenance={**prov, "display_only_sizes": ["valley follows the axis foundation profile up and downstream",
                                                 "level ground at crest + 6 m beyond the abutments"]})

    notes.append(f"Concrete gravity dam {p['length']:g} m long, crest +{p['crest']:g}, deepest foundation "
                 f"+{min(foundation_at(p, c / 1000) for c in cuts):g}; downstream slope {ds:g}H:1V"
                 + (f"; spillway {p['bays']} x {p['bay_width']:g} m, crest +{p['spill_crest']:g}" if spill else "")
                 + ". Every size is from the drawing's notes unless listed as display-only or assumed.")
    return sc.model(project_id, "water dam", storey, elements, facts, LOD_NOTES, notes, files)


def _range(step, end, start):
    x = start
    while x < end - 1:
        yield x
        x += step


def _crossings(p, level_m, L):
    """Where the foundation profile rises above a water level (the water's ends along the axis)."""
    if not p["profile"]:
        return 0.0, L
    xs = [x for x in range(0, int(L) + 1, 1000) if foundation_at(p, x / 1000) < level_m]
    return (float(xs[0]), float(xs[-1])) if xs else (0.0, L)


def _radial_gate(x0, x1, y_crest, sill, height, radius):
    """A curved skin plate from the crest up, with arms back to a trunnion downstream."""
    gate, arms = sc.Mesh(), sc.Mesh()
    trunnion = (y_crest + radius * 0.95, sill + height * 0.55)
    a0 = math.atan2(sill - trunnion[1], y_crest - trunnion[0])
    a1 = a0 - math.radians(28 + 30 * min(1.0, height / radius))
    arc = [(trunnion[0] + radius * math.cos(a0 + (a1 - a0) * t / 8), trunnion[1] + radius * math.sin(a0 + (a1 - a0) * t / 8))
           for t in range(9)]
    skin = arc + [(trunnion[0] + (radius - 250) * math.cos(a0 + (a1 - a0) * t / 8),
                   trunnion[1] + (radius - 250) * math.sin(a0 + (a1 - a0) * t / 8)) for t in range(8, -1, -1)]
    gate.prism_x(skin, x0 + 100, x1 - 100, STEEL)
    for xa in (x0 + 400, x1 - 400):
        for t in (1, 7):
            arms.tube((xa, arc[t][0], arc[t][1]), (xa, trunnion[0], trunnion[1]), 180, colour=STEEL)
        arms.tube((xa - 300, trunnion[0], trunnion[1]), (xa + 300, trunnion[0], trunnion[1]), 350, colour="#2f3b4a", n=10)
    return gate, arms


LOD_NOTES = {
    "100": "LOD 100 — dam massing: the full section along the axis, foundation levels from the drawing.",
    "200": "LOD 200 — monoliths (contraction joints), spillway bays and piers.",
    "300": "LOD 300 — ogee spillway profile, radial gates, stilling basin and training walls.",
    "400": "LOD 400 — gate arms and trunnions, hoist bridge and hoist houses, crest parapets.",
    "500": "LOD 500 — reservoir at FRL, tailwater at TWL, crest road, valley ground and finishes.",
}
