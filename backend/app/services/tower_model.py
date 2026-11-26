"""
Cell (telecom) tower from its drawing notes: self-supporting lattice tower (3 or 4 legs)
or monopole.

Read from the drawing (each value is kept with its source line):
height; legs (3-LEGGED / 4-LEGGED, or MONOPOLE); base and top width (or monopole
base/top diameter); the height where the straight portion starts; bracing type and
panel height; leg / bracing angle sizes; platform levels; ladder; lightning arrestor;
aviation light; red-and-white painting; foundation pad size and depth; the antenna
schedule (panel antennas, microwave dishes, RRUs: size, number and level).

Height, legs and base and top width (or diameters) are required. Without them no tower
is built, and the model says what is missing. Sizes no drawing gives are display
values and say so: pad and pedestal thickness, platform overhang, rung spacing.

LOD 100: envelope and foundations. 200: legs, horizontals, platforms. 300: bracing,
ladder. 400: gussets, safety cage, cable ladder, mounts, antennas, arrestor, light.
500: paint bands and equipment colours.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.services import structure_common as sc

GALV = "#a7b0b8"
RED, WHITE = "#c0392b", "#f2f2f2"
GRATING, CONCRETE = "#6f767e", "#b9b4aa"
NUM = sc.NUMBER


def read(facts: sc.Facts) -> dict[str, Any]:
    p: dict[str, Any] = {}
    p["height"] = facts.find("height", rf"(?:TOWER HEIGHT|HEIGHT OF TOWER|TOTAL HEIGHT)\D{{0,12}}({NUM})\s*M\b")
    if facts.has(r"\bMONOPOLE\b"):
        p["legs"] = 0
        facts.note("legs", "monopole", facts.has(r"\bMONOPOLE\b"))
        p["base_dia"] = facts.find("base_diameter", rf"BASE (?:DIA(?:METER)?|Ø)\D{{0,10}}({NUM})\s*M")
        p["top_dia"] = facts.find("top_diameter", rf"TOP (?:DIA(?:METER)?|Ø)\D{{0,10}}({NUM})\s*M")
    else:
        legs = facts.find("legs", r"\b([34])\s*-?\s*LEG(?:GED)?\b", convert=int)
        if legs is None and facts.has(r"\bSQUARE\b.*\bTOWER\b|\bTOWER\b.*\bSQUARE\b"):
            legs = 4
        if legs is None and facts.has(r"\bTRIANGULAR\b"):
            legs = 3
        p["legs"] = legs
        p["base_width"] = facts.find("base_width", rf"BASE WIDTH\D{{0,25}}?({NUM})\s*M\b")
        p["top_width"] = facts.find("top_width", rf"TOP WIDTH\D{{0,12}}({NUM})\s*M\b")
    p["straight_from"] = facts.find("straight_portion_from",
                                    rf"(?:STRAIGHT|PARALLEL) (?:PORTION|SECTION)\D{{0,20}}(?:FROM|ABOVE)\s*\+?({NUM})\s*M")
    p["panel"] = facts.find("panel_height", rf"PANEL HEIGHT\D{{0,10}}({NUM})\s*M")
    k_line = facts.has(r"\bK\s*-?\s*BRACING|BRACING\W{1,4}K\b")
    x_line = facts.has(r"\bX\s*-?\s*BRACING|BRACING\W{1,4}X\b")
    p["bracing"] = "K" if k_line else "X"
    if k_line or x_line:
        facts.note("bracing", p["bracing"], k_line or x_line)
    else:
        facts.assume("bracing", "X", "display pattern: no bracing type printed")
    leg = facts.find("leg_section", rf"LEGS?\s*:?\s*(?:ANGLE|L)\s*(\d+)\s*[X×]", convert=float)
    brace = facts.find("bracing_section", rf"BRACINGS?\s*:?\s*(?:ANGLE|L)\s*(\d+)\s*[X×]", convert=float)
    p["leg_mm"], p["brace_mm"] = leg or 150.0, brace or 75.0
    if leg is None:
        facts.assume("leg_section", 150.0, "display size: no leg section printed")
    p["platforms"] = []
    for _, line in facts.find_all(rf"PLATFORM\D{{0,8}}(?:AT|@)\s*\+?({NUM})\s*M"):
        for m in re.finditer(rf"PLATFORM\D{{0,8}}(?:AT|@)\s*\+?({NUM})\s*M", line["upper"]):
            level = float(m.group(1))
            if level not in p["platforms"]:
                p["platforms"].append(level)
                facts.note(f"platform_{len(p['platforms'])}", level, line, "platform level")
    p["ladder"] = facts.has(r"\bLADDER\b") is not None
    p["cable_ladder"] = facts.has(r"CABLE LADDER") is not None
    p["arrestor"] = facts.find("lightning_arrestor", rf"LIGHTNING (?:ARRESTER|ARRESTOR|ROD|SPIKE)\D{{0,10}}({NUM})\s*M")
    p["aviation_light"] = facts.has(r"AVIATION (?:OBSTRUCTION )?LIGHT") is not None
    p["painted"] = facts.has(r"RED (?:AND|&) WHITE|ALTERNATE (?:RED|ORANGE)") is not None
    pads = facts.find_all(rf"(?:FOUNDATION|PAD|FOOTING)[^\n]*?({NUM})\s*[X×]\s*({NUM})\s*M[^\n]*?DEPTH\D{{0,5}}({NUM})\s*M")
    if pads:
        match, line = pads[0]
        p["pad"] = (float(match.group(1)), float(match.group(2)), float(match.group(3)))
        facts.note("foundation", f"{p['pad'][0]:g} x {p['pad'][1]:g} m pad, {p['pad'][2]:g} m deep", line)
    else:
        p["pad"] = None
    p["antennas"] = []
    for match, line in facts.find_all(rf"ANTENNA[^\n]*?({NUM})\s*[X×]\s*({NUM})\s*M[^\n]*?(\d+)\s*NOS?[^\n]*?(?:AT|@)\s*\+?({NUM})\s*M"):
        p["antennas"].append({"kind": "panel", "h": float(match.group(1)), "w": float(match.group(2)),
                              "count": int(match.group(3)), "z": float(match.group(4)), "text": line["text"]})
    for match, line in facts.find_all(rf"(?:MW|MICROWAVE)\s*(?:DISH|ANTENNA)\D{{0,10}}({NUM})\s*M\s*(?:DIA|Ø)[^\n]*?(\d+)\s*NOS?[^\n]*?(?:AT|@)\s*\+?({NUM})\s*M"):
        p["antennas"].append({"kind": "dish", "d": float(match.group(1)), "count": int(match.group(2)),
                              "z": float(match.group(3)), "text": line["text"]})
    for match, line in facts.find_all(rf"\bRRU\b[^\n]*?({NUM})\s*[X×]\s*({NUM})\s*M[^\n]*?(\d+)\s*NOS?[^\n]*?(?:AT|@)\s*\+?({NUM})\s*M"):
        p["antennas"].append({"kind": "rru", "h": float(match.group(1)), "w": float(match.group(2)),
                              "count": int(match.group(3)), "z": float(match.group(4)), "text": line["text"]})
    for k, a in enumerate(p["antennas"]):
        facts.used[f"antenna_{k + 1}"] = {"name": f"{a['kind']} antenna", "value": a["count"], "text": a["text"]}
    if p["panel"] is None:
        p["panel"] = 3.0
        facts.assume("panel_height", 3.0, "display spacing: no panel height printed")
    return p


def missing(p: dict[str, Any]) -> list[str]:
    need = ["height"] + (["base_dia", "top_dia"] if p.get("legs") == 0 else ["legs", "base_width", "top_width"])
    return [k.replace("_", " ") for k in need if p.get(k) in (None, 0) and not (k == "legs" and p.get(k) == 0)]


# ---------------------------------------------------------------------------
# Geometry (mm; tower centre at the origin, NGL at z = 0)
# ---------------------------------------------------------------------------

class Tower:
    def __init__(self, p):
        self.p = p
        self.h = p["height"] * 1000
        self.n = p["legs"]
        self.base = p["base_width"] * 1000
        self.top = p["top_width"] * 1000
        self.z_straight = (p["straight_from"] or p["height"]) * 1000

    def width(self, z):
        if z >= self.z_straight:
            return self.top
        return self.base + (self.top - self.base) * z / self.z_straight

    def leg(self, k, z):
        """Plan position of leg k at height z."""
        r = self.width(z) / (2 * math.sin(math.pi / self.n))
        a = (math.pi / 4 if self.n == 4 else math.pi / 2) + 2 * math.pi * k / self.n
        return (r * math.cos(a), r * math.sin(a), z)

    def levels(self):
        step = self.p["panel"] * 1000
        out = [0.0]
        while out[-1] + step < self.h - 1:
            out.append(out[-1] + step)
        if self.z_straight not in out and 0 < self.z_straight < self.h:
            out.append(self.z_straight)
        out.append(self.h)
        return sorted(set(round(z, 1) for z in out))

    def face_point(self, k, z, t):
        """A point on face k (between legs k and k+1) at height z, t = 0..1 along the face."""
        a, b = self.leg(k, z), self.leg((k + 1) % self.n, z)
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def paint(z, h, painted):
    if not painted:
        return GALV
    band = min(6, int(z / (h / 7)))                 # seven equal bands, red at both ends
    return RED if band % 2 == 0 else WHITE


def build(project_id: str, facts: sc.Facts, files: list[str], drawing: dict[str, Any]) -> dict[str, Any]:
    p = read(facts)
    lacking = missing(p)
    storey = sc.structure_storey(drawing.get("title"), drawing.get("id"), 0.0, (p.get("height") or 0) * 1000, "NGL")
    notes = []
    if lacking:
        notes.append("No tower built: the drawings do not give " + ", ".join(lacking) + ".")
        return sc.model(project_id, "cell tower", storey, [], facts, LOD_NOTES, notes, files)
    if p["legs"] == 0:
        return _monopole(project_id, p, facts, files, storey)
    t = Tower(p)
    prov = {"basis": "tower parameters printed on the drawing", "parameters": "see the model's parameter list"}
    elements = []
    E = lambda *a, **k: elements.append(sc.element(*a, storey=storey, provenance=k.pop("provenance", prov), **k))  # noqa: E731

    # LOD 100: the tower's envelope.
    env = sc.Mesh()
    rings = [0.0, t.z_straight, t.h] if t.z_straight < t.h else [0.0, t.h]
    pts, faces = [], []
    for z in rings:
        pts += [t.leg(k, z) for k in range(t.n)]
    for r in range(len(rings) - 1):
        for k in range(t.n):
            a, b = r * t.n + k, r * t.n + (k + 1) % t.n
            faces += [[a, b, b + t.n], [a, b + t.n, a + t.n]]
    faces += [[0, k + 1, k] for k in range(1, t.n - 1)]
    last = (len(rings) - 1) * t.n
    faces += [[last, last + k, last + k + 1] for k in range(1, t.n - 1)]
    env.add(pts, faces, GALV, 0.35)
    E("tower:envelope", "tower envelope", "tower envelope", env, lod=(100, 100), envelope=True,
      dimensions={"length": t.base, "thickness": t.top, "height": t.h})

    # Foundations (every LOD): a pad per leg at the printed depth, with a pedestal to just above ground.
    if p["pad"]:
        fl, fw, depth = (v * 1000 for v in p["pad"])
        found = sc.Mesh()
        for k in range(t.n):
            x, y, _ = t.leg(k, 0)
            found.box(x - fl / 2, y - fw / 2, -depth, x + fl / 2, y + fw / 2, -depth + 600, CONCRETE)
            found.box(x - 350, y - 350, -depth + 600, x + 350, y + 350, 300, CONCRETE)
        E("tower:foundations", "foundation", "pad foundation", found, lod=(100, 500),
          dimensions={"length": fl, "thickness": fw, "height": depth},
          provenance={**prov, "display_only_sizes": ["pad thickness 600 mm", "pedestal 700 x 700 mm to +0.3 m"]})

    levels = t.levels()
    r_leg, r_brace = p["leg_mm"] / 2, p["brace_mm"] / 2
    legs = sc.Mesh()
    for k in range(t.n):
        for z0, z1 in zip(levels, levels[1:]):
            legs.tube(t.leg(k, z0), t.leg(k, z1), r_leg, colour=paint((z0 + z1) / 2, t.h, p["painted"]))
    E("tower:legs", "tower leg", "main legs", legs, lod=(200, 500), dimensions={"length": None, "thickness": p["leg_mm"], "height": t.h})

    horizontals = sc.Mesh()
    for z in levels[1:]:
        for k in range(t.n):
            horizontals.tube(t.leg(k, z), t.leg((k + 1) % t.n, z), r_brace * 0.9, colour=paint(z, t.h, p["painted"]))
    E("tower:horizontals", "tower member", "horizontal members", horizontals, lod=(200, 500))

    bracing = sc.Mesh()
    for z0, z1 in zip(levels, levels[1:]):
        colour = paint((z0 + z1) / 2, t.h, p["painted"])
        for k in range(t.n):
            if p["bracing"] == "K":
                mid = t.face_point(k, z1, 0.5)
                bracing.tube(t.leg(k, z0), mid, r_brace, colour=colour)
                bracing.tube(t.leg((k + 1) % t.n, z0), mid, r_brace, colour=colour)
            else:
                bracing.tube(t.leg(k, z0), t.leg((k + 1) % t.n, z1), r_brace, colour=colour)
                bracing.tube(t.leg((k + 1) % t.n, z0), t.leg(k, z1), r_brace, colour=colour)
    E("tower:bracing", "tower member", f"{p['bracing']}-bracing", bracing, lod=(300, 500))

    # Platforms: a grating deck round the tower at each printed level (from LOD 200), handrails from LOD 400.
    for j, level in enumerate(p["platforms"]):
        z = level * 1000
        deck = sc.Mesh()
        ring = [t.leg(k, z) for k in range(t.n)]
        grow = 800.0
        outer = [(x * (1 + grow / max(1.0, math.hypot(x, y))), y * (1 + grow / max(1.0, math.hypot(x, y)))) for x, y, _ in ring]
        deck.polygon_prism(outer, z - 60, z, GRATING)
        E(f"tower:platform:{j}", "platform", "platform", deck, lod=(200, 500),
          provenance={**prov, "display_only_sizes": ["platform overhang 800 mm", "deck 60 mm"]})
        rail = sc.Mesh()
        for k in range(t.n):
            a, b = outer[k], outer[(k + 1) % t.n]
            for hz in (500, 1100):
                rail.tube((a[0], a[1], z + hz), (b[0], b[1], z + hz), 25, colour="#e0b100")
            rail.tube((a[0], a[1], z), (a[0], a[1], z + 1100), 25, colour="#e0b100")
        E(f"tower:platform:{j}:rail", "platform", "platform handrail", rail, lod=(400, 500))

    # Ladder up the middle of face 0 (from LOD 300), rungs and safety cage (LOD 400); cable ladder on face 2.
    if p["ladder"]:
        ladder, cage = sc.Mesh(), sc.Mesh()
        steps = [z for z in range(0, int(t.h), 300)]
        for side in (-0.04, 0.04):
            for z0, z1 in zip(levels, levels[1:]):
                ladder.tube(_inset(t, 0, z0, 0.5 + side), _inset(t, 0, z1, 0.5 + side), 20, colour="#d4d7da")
        E("tower:ladder", "ladder", "climbing ladder", ladder, lod=(300, 500))
        for z in steps:
            cage.tube(_inset(t, 0, z, 0.46), _inset(t, 0, z, 0.54), 12, colour="#d4d7da", n=4)
        for z in range(2400, int(t.h), 1000):
            a, b = _inset(t, 0, z, 0.42), _inset(t, 0, z, 0.58)
            out = _outward(t, 0, z, 700)
            cage.tube(a, (a[0] + out[0], a[1] + out[1], z), 15, colour="#d4d7da", n=4)
            cage.tube((a[0] + out[0], a[1] + out[1], z), (b[0] + out[0], b[1] + out[1], z), 15, colour="#d4d7da", n=4)
            cage.tube((b[0] + out[0], b[1] + out[1], z), b, 15, colour="#d4d7da", n=4)
        E("tower:ladder:rungs", "ladder", "rungs and safety cage", cage, lod=(400, 500),
          provenance={**prov, "display_only_sizes": ["rungs every 300 mm", "cage hoops every 1 m from 2.4 m"]})
    if p["cable_ladder"]:
        cable = sc.Mesh()
        face = 2 % t.n
        for side in (-0.05, 0.05):
            for z0, z1 in zip(levels, levels[1:]):
                cable.tube(_inset(t, face, z0, 0.5 + side), _inset(t, face, z1, 0.5 + side), 18, colour="#8d949b", n=4)
        E("tower:cable_ladder", "ladder", "cable ladder", cable, lod=(400, 500))

    # Gussets at every leg node (LOD 400).
    gussets = sc.Mesh()
    for z in levels[1:-1]:
        for k in range(t.n):
            x, y, _ = t.leg(k, z)
            gussets.box(x - 160, y - 160, z - 160, x + 160, y + 160, z + 160, paint(z, t.h, p["painted"]))
    E("tower:gussets", "tower member", "gusset plates", gussets, lod=(400, 500),
      provenance={**prov, "display_only_sizes": ["gusset 320 mm"]})

    # Top: lightning arrestor and aviation light.
    if p["arrestor"]:
        top = sc.Mesh().tube((0, 0, t.h - 500), (0, 0, t.h + p["arrestor"] * 1000), 30, 8, colour="#c9a227")
        for k in range(t.n):
            top.tube(t.leg(k, t.h), (0, 0, t.h - 500), 25, colour=GALV, n=4)
        E("tower:arrestor", "tower member", "lightning arrestor", top, lod=(400, 500))
    if p["aviation_light"]:
        light = sc.Mesh().revolve(0, 0, [(110, t.h + 150), (130, t.h + 250), (90, t.h + 400)], "#e02020", n=12)
        E("tower:aviation_light", "antenna", "aviation light", light, lod=(400, 500))

    # Antennas from the schedule (LOD 400): spread evenly round the tower, facing out, on mounting pipes.
    for j, a in enumerate(p["antennas"]):
        mesh = sc.Mesh()
        z = a["z"] * 1000
        for i in range(a["count"]):
            yaw = 2 * math.pi * i / a["count"] + (math.pi / 6 if a["kind"] == "rru" else 0) + (math.pi / 4 if a["kind"] == "dish" else 0)
            r = t.width(z) / 2 / math.cos(math.pi / t.n) + (350 if a["kind"] == "rru" else 650)
            cx, cy = r * math.cos(yaw), r * math.sin(yaw)
            mesh.tube((cx * 0.55, cy * 0.55, z), (cx, cy, z), 40, colour=GALV, n=6)          # mount arm
            if a["kind"] == "dish":
                d = a["d"] * 1000
                dx, dy = math.cos(yaw), math.sin(yaw)
                mesh.tube((cx, cy, z), (cx + dx * 250, cy + dy * 250, z), d / 2, d / 2 * 0.95, colour="#f0f0f0", n=20)
                mesh.tube((cx - dx * 150, cy - dy * 150, z), (cx, cy, z), d / 4, d / 2 * 0.9, colour="#d9d9d9", n=20)
            else:
                h, w = a["h"] * 1000, a["w"] * 1000
                dep = 120 if a["kind"] == "panel" else 180
                _obox(mesh, cx, cy, z - h / 2, z + h / 2, w, dep, yaw, "#f4f4f2" if a["kind"] == "panel" else "#3a3f45")
        label = {"panel": "panel antennas", "dish": "microwave dishes", "rru": "radio units (RRU)"}[a["kind"]]
        E(f"tower:antenna:{j}", "antenna", label, mesh, lod=(400, 500),
          provenance={"basis": "antenna schedule on the drawing", "text": a["text"],
                      "display_only_sizes": ["mount arms", "azimuths spread evenly (not on the drawing)"]})

    notes.append(f"{t.n}-legged lattice tower {p['height']:g} m high, base {p['base_width']:g} m to top "
                 f"{p['top_width']:g} m; every size from the drawing's notes unless listed as display-only or assumed.")
    return sc.model(project_id, "cell tower", storey, elements, facts, LOD_NOTES, notes, files)


def _inset(t: Tower, face, z, along):
    """A point on a face pulled 120 mm inside the tower (ladders)."""
    x, y, _ = t.face_point(face, z, along)
    r = math.hypot(x, y) or 1.0
    return (x * (1 - 120 / r), y * (1 - 120 / r), z)


def _outward(t: Tower, face, z, dist):
    x, y, _ = t.face_point(face, z, 0.5)
    r = math.hypot(x, y) or 1.0
    return (x / r * dist, y / r * dist)


def _obox(mesh: sc.Mesh, cx, cy, z0, z1, w, d, yaw, colour):
    """A box turned to face `yaw` in plan: w across, d along the facing direction."""
    ux, uy = -math.sin(yaw), math.cos(yaw)        # across
    vx, vy = math.cos(yaw), math.sin(yaw)          # facing
    corners = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
    ring = [(cx + a * ux + b * vx, cy + a * uy + b * vy) for a, b in corners]
    pts = [(x, y, z0) for x, y in ring] + [(x, y, z1) for x, y in ring]
    mesh.add(pts, sc.BOX_FACES, colour)


def _monopole(project_id, p, facts, files, storey):
    h = p["height"] * 1000
    base, top = p["base_dia"] * 500, p["top_dia"] * 500
    elements = []
    prov = {"basis": "monopole parameters printed on the drawing"}
    shaft = sc.Mesh().revolve(0, 0, [(base, 0), (top, h)], GALV, n=16)
    elements.append(sc.element("tower:shaft", "tower leg", "monopole shaft", shaft, storey, lod=(100, 500), provenance=prov,
                               dimensions={"length": base * 2, "thickness": top * 2, "height": h}))
    notes = [f"Monopole {p['height']:g} m high, diameter {p['base_dia']:g} m at the base to {p['top_dia']:g} m at the top."]
    return sc.model(project_id, "cell tower", storey, elements, facts, LOD_NOTES, notes, files)


LOD_NOTES = {
    "100": "LOD 100 — tower envelope (height, base and top width) and the foundations.",
    "200": "LOD 200 — main legs, horizontal members at each panel, platforms and foundations.",
    "300": "LOD 300 — bracing on every face and the climbing ladder.",
    "400": "LOD 400 — gussets, ladder rungs and safety cage, cable ladder, antennas and dishes on their mounts, "
           "lightning arrestor and aviation light.",
    "500": "LOD 500 — finishes: aviation red-and-white bands (as noted on the drawing) and equipment colours.",
}
