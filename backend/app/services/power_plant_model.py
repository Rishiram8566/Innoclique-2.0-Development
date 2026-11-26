"""
Power plant from its layout plan: where each structure is, from its label and drawn
outline on the plan; its size and height from the structure schedule.

Read from the drawing:

* the plan scale ("SCALE 1:2000") and the ground level (FGL / NGL);
* schedule rows "NAME - [n NOS,] L X W M | DIA d M [, d2 M AT TOP], HT h M";
* each structure's label on the plan ("NAME", "NAME-1", "NAME 2"). Its footprint is
  the drawn outline round the label (rectangle or circle) when that outline matches
  the schedule size, else the schedule size centred on the label.

Kinds from the names: turbine / TG building, boiler house, ESP (precipitator),
chimney / stack, natural-draught cooling tower, transformer, switchyard, tank, coal
yard / stockpile, and any other building.

A structure with no height in the schedule is drawn only as its plan area. Sizes no
drawing gives are display values and say so: frame bay spacing, cooling tower throat
and shell, transformer radiators and bushings, switchyard gantry spacing and height.

LOD 100: massing. 200: structural frames, chimney and cooling tower shells, tanks,
stockpile. 300: cladding and roofs, cooling tower legs, transformer tanks,
switchyard gantries. 400: louvre bands, chimney platforms, radiators and bushings,
switchyard equipment. 500: colours, aviation bands, plant ground.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.services import structure_common as sc

NUM = sc.NUMBER
COLOURS = {
    "turbine": ("#d6dbe0", "#2f5d8a"), "boiler": ("#c4c9ce", "#5b6168"), "esp": ("#9aa1a8", "#6d747b"),
    "building": ("#ece9e1", "#6f9fc9"), "chimney": ("#b9b4aa", "#c0392b"), "cooling": ("#c8c4ba", "#8f8b82"),
    "transformer": ("#7d8a6a", "#7a4b2a"), "switchyard": ("#9b9586", "#a7b0b8"), "tank": ("#efefef", "#c9c9c9"),
    "coal": ("#2b2b2b", "#555555"),
}
KINDS = [
    ("chimney", r"\b(?:CHIMNEY|STACK)\b"), ("cooling", r"COOLING TOWER"),
    ("transformer", r"TRANSFORMER|\bGT\b|\bUAT\b|\bST\b"), ("switchyard", r"SWITCH ?YARD|SUBSTATION"),
    ("tank", r"\bTANKS?\b"), ("coal", r"COAL (?:YARD|STORAGE)|STOCKPILE"), ("boiler", r"\bBOILER"),
    ("turbine", r"\bTG\b|TURBINE"), ("esp", r"\bESP\b|PRECIPITATOR"),
]


def kind_of(name: str) -> str:
    return next((k for k, pattern in KINDS if re.search(pattern, name)), "building")


def schedule(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for line in lines:
        m = re.match(r"^([A-Z][A-Z0-9 .&/()]+?)\s+[-–]\s+(.*)$", line["upper"])
        if not m:
            continue
        name, rest = m.group(1).strip(), m.group(2)
        size = re.search(rf"({NUM})\s*[X×]\s*({NUM})\s*M", rest)
        dia = re.search(rf"DIA\.?\s*({NUM})\s*M", rest)
        if not (size or dia):
            continue
        top = re.search(rf"({NUM})\s*M AT TOP", rest)
        height = re.search(rf"\b(?:HT|HEIGHT)\.?\s*({NUM})\s*M", rest)
        count = re.search(r"(\d+)\s*NOS", rest)
        rows.append({"name": name, "kind": kind_of(name), "count": int(count.group(1)) if count else 1,
                     "size": (float(size.group(1)), float(size.group(2))) if size else None,
                     "dia": float(dia.group(1)) if dia else None, "top_dia": float(top.group(1)) if top else None,
                     "height": float(height.group(1)) if height else None, "text": line["text"],
                     "drawing": line.get("drawing_title")})
    return rows


def _norm(text: str) -> str:
    return re.sub(r"[\s.\-_]+", " ", text.upper()).strip()


def placements(row, lines, outlines, scale):
    """Footprints (pt bbox) for each of the row's structures: label positions, snapped to matching outlines."""
    labels = [ln for ln in lines if _is_label(row, ln)]
    out = []
    for ln in labels[: row["count"]]:
        cx, cy = (ln["bbox"][0] + ln["bbox"][2]) / 2, (ln["bbox"][1] + ln["bbox"][3]) / 2
        want = (row["dia"], row["dia"]) if row["dia"] else row["size"]
        best = None
        for r in outlines:
            if not (r[0] <= cx <= r[2] and r[1] <= cy <= r[3]):
                continue
            w, h = (r[2] - r[0]) * scale / 1000, (r[3] - r[1]) * scale / 1000
            fit = min(abs(w - want[0]) / want[0] + abs(h - want[1]) / want[1],
                      abs(w - want[1]) / want[1] + abs(h - want[0]) / want[0])
            if fit < 0.3 and (best is None or fit < best[0]):
                best = (fit, r)
        if best:
            out.append(("outline", best[1]))
        else:
            hw, hh = want[0] * 1000 / scale / 2, want[1] * 1000 / scale / 2
            out.append(("label", (cx - hw, cy - hh, cx + hw, cy + hh)))
    return out


def outlines_of(folder_pdf: str, page_index: int):
    import pymupdf

    with pymupdf.open(folder_pdf) as doc:
        page = doc[page_index]
        return [tuple(d["rect"]) for d in page.get_drawings() if d["rect"].width > 2 and d["rect"].height > 2]


def _is_label(row, line) -> bool:
    """A plan label for this schedule row: its name, optionally numbered (NAME-1, NAME 2), not the schedule line."""
    return bool(re.fullmatch(rf"{re.escape(_norm(row['name']))}(?: ?\d{{1,2}})?", _norm(line["text"]))) \
        and not re.search(r"\s[-–]\s", line["text"])


def build(project_id: str, facts: sc.Facts, files: list[str], drawing: dict[str, Any], lines) -> dict[str, Any]:
    scale_n = facts.find("scale", r"SCALE\s*1\s*:\s*(\d{2,5})", convert=int, label="plan scale 1:N")
    ground_m = facts.find("ground_level", rf"\b(?:FGL|NGL|FINISHED GROUND LEVEL|GROUND LEVEL)\D{{0,10}}?\+?({NUM})") or 0.0
    rows = schedule(lines)
    storey = sc.structure_storey(drawing.get("title"), drawing.get("id"), ground_m * 1000, 0.0, "FGL")
    if not rows or not scale_n:
        why = "no structure schedule (NAME - L X W M, HT h M)" if not rows else "no plan scale (SCALE 1:N)"
        return sc.model(project_id, "power plant", storey, [], facts, LOD_NOTES,
                        [f"No power plant built: the drawings give {why}."], files)
    for k, row in enumerate(rows):
        facts.used[f"schedule_{k}"] = {"name": row["name"].lower(), "value": _describe(row), "text": row["text"],
                                       "drawing": row["drawing"]}
    mm_per_pt = scale_n * 25.4 / 72
    G = ground_m * 1000
    by_page: dict[tuple, list] = {}
    for ln in lines:
        by_page.setdefault((ln["drawing_id"], ln["page"]), []).append(ln)
    footprints, unplaced = [], []
    for row in rows:
        page_key = next(((ln["drawing_id"], ln["page"]) for ln in lines if _is_label(row, ln)), None)
        if page_key is None:
            unplaced.append(row["name"].lower())
            continue
        for how, box in placements(row, by_page[page_key], _OUTLINES.get(page_key, []), mm_per_pt):
            footprints.append((row, how, box))
    if not footprints:
        return sc.model(project_id, "power plant", storey, [], facts, LOD_NOTES,
                        ["No power plant built: none of the schedule's structures is labelled on the plan."], files)
    ox = min(b[0] for _, _, b in footprints)
    oy = min(b[1] for _, _, b in footprints)

    def mm(box):
        return ((box[0] - ox) * mm_per_pt, (box[1] - oy) * mm_per_pt, (box[2] - ox) * mm_per_pt, (box[3] - oy) * mm_per_pt)

    prov = {"basis": "structure schedule and layout plan", "scale": f"1:{scale_n}"}
    elements: list[dict[str, Any]] = []
    counter: dict[str, int] = {}
    for row, how, box in footprints:
        n = counter[row["name"]] = counter.get(row["name"], 0) + 1
        label = row["name"].title() + (f" {n}" if row["count"] > 1 else "")
        ident = f"plant:{_norm(row['name']).replace(' ', '_').lower()}:{n}"
        pv = {**prov, "schedule": row["text"],
              "footprint": "drawn outline on the plan" if how == "outline" else "schedule size centred on the label"}
        elements += _structure(ident, label, row, mm(box), G, storey, pv)
    xs = [v[0] for e in elements for v in e["mesh"]["vertices"]]
    ys = [v[1] for e in elements for v in e["mesh"]["vertices"]]
    ground = sc.Mesh().box(min(xs) - 30000, min(ys) - 30000, G - 300, max(xs) + 30000, max(ys) + 30000, G - 5, "#8e9483")
    elements.append(sc.element("plant:ground", "terrain", "plant ground", ground, storey, lod=(500, 500),
                               provenance={**prov, "display_only_sizes": ["ground 30 m beyond the structures"]}))
    storey["storey_height_mm"] = max((r["height"] or 0) for r in rows) * 1000
    notes = [f"{len(footprints)} structure(s) placed from the layout plan (scale 1:{scale_n}); sizes and heights from "
             "the structure schedule; anything else is listed as display-only."]
    if unplaced:
        notes.append("In the schedule but not labelled on the plan (not built): " + ", ".join(unplaced) + ".")
    return sc.model(project_id, "power plant", storey, elements, facts, LOD_NOTES, notes, files)


_OUTLINES: dict[tuple, list] = {}


def register_outlines(folder, project, lines):
    """Read the drawn outlines of every page that carries text (called by structure_model before build)."""
    from pathlib import Path

    paths = {d["id"]: Path(folder) / "drawings" / d.get("stored_filename", "") for d in project.get("drawings") or []}
    for drawing_id, page in {(ln["drawing_id"], ln["page"]) for ln in lines}:
        if (drawing_id, page) not in _OUTLINES and paths.get(drawing_id) and paths[drawing_id].is_file():
            _OUTLINES[(drawing_id, page)] = outlines_of(str(paths[drawing_id]), page)


def _describe(row):
    size = f"{row['size'][0]:g} x {row['size'][1]:g} m" if row["size"] else f"dia {row['dia']:g} m"
    return f"{row['count']} x " * (row["count"] > 1) + size + (f", ht {row['height']:g} m" if row["height"] else "")


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

def _structure(ident, label, row, box, G, storey, prov) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    h = (row["height"] or 0) * 1000
    kind = row["kind"]
    main, accent = COLOURS.get(kind, COLOURS["building"])
    out = []

    def add(suffix, etype, component, mesh, lod, extra=None, **kw):
        if not mesh.empty():
            out.append(sc.element(f"{ident}:{suffix}", etype, component, mesh, storey, lod=lod,
                                  provenance={**prov, **(extra or {})}, **kw))

    dims = {"length": round(x1 - x0), "thickness": round(y1 - y0), "height": round(h) if h else None}
    if kind == "chimney":
        rb = (row["dia"] or (x1 - x0) / 1000) * 500
        rt = (row["top_dia"] or row["dia"] or (x1 - x0) / 1000) * 500
        add("massing", "stack", label, sc.Mesh().revolve(cx, cy, [(rb, G), (rt, G + h)], main, n=24), (100, 100), dimensions=dims)
        body = sc.Mesh()
        bands = 7
        for b in range(bands):
            z0, z1 = G + h * b / bands, G + h * (b + 1) / bands
            r0, r1 = rb + (rt - rb) * b / bands, rb + (rt - rb) * (b + 1) / bands
            colour = (accent if (bands - b) % 2 else "#f2f2f2") if b >= bands - 2 else main
            body.revolve(cx, cy, [(r0, z0), (r1, z1)], colour, n=28, cap_bottom=b == 0, cap_top=b == bands - 1)
        add("shell", "stack", label, body, (200, 500), dimensions=dims)
        rings = sc.Mesh()
        for frac in (0.5, 0.8, 0.97):
            z = G + h * frac
            r = rb + (rt - rb) * frac
            rings.revolve(cx, cy, [(r + 1500, z - 150), (r + 1500, z + 100)], "#5f666d", n=28)
        add("platforms", "stack", f"{label} platforms", rings, (400, 500), {"display_only_sizes": ["platforms 1.5 m wide"]})
        return out
    if kind == "cooling":
        rb = (row["dia"] or (x1 - x0) / 1000) * 500
        add("massing", "cooling tower", label, sc.Mesh().revolve(cx, cy, [(rb, G), (rb * 0.7, G + h)], main, n=32), (100, 100),
            dimensions=dims)
        shell_bottom = G + 0.08 * h
        throat_z, r_throat = shell_bottom + 0.78 * (h - 0.08 * h), rb * 0.6
        c = (throat_z - shell_bottom) / math.sqrt((rb / r_throat) ** 2 - 1)
        prof = []
        for i in range(13):
            z = shell_bottom + (G + h - shell_bottom) * i / 12
            prof.append((r_throat * math.sqrt(1 + ((z - throat_z) / c) ** 2), z))
        add("shell", "cooling tower", label, sc.Mesh().revolve(cx, cy, prof, main, n=40, cap_bottom=False, cap_top=False),
            (200, 500), {"display_only_sizes": ["throat at 78% of the shell, 60% of the base radius"]}, dimensions=dims)
        legs = sc.Mesh()
        for k in range(36):
            a, b = 2 * math.pi * k / 36, 2 * math.pi * (k + 1) / 36
            top = (cx + prof[0][0] * math.cos(a), cy + prof[0][0] * math.sin(a), shell_bottom)
            for bb in (a - math.pi / 36, b - math.pi / 36):
                legs.tube((cx + (rb + 500) * math.cos(bb), cy + (rb + 500) * math.sin(bb), G), top, 450, colour=accent)
        add("legs", "cooling tower", f"{label} raker columns", legs, (300, 500), {"display_only_sizes": ["36 pairs of raker columns"]})
        basin = sc.Mesh().revolve(cx, cy, [(rb + 2500, G), (rb + 2500, G + 1500)], accent, n=40)
        add("basin", "cooling tower", f"{label} cold water basin", basin, (300, 500), {"display_only_sizes": ["basin wall 1.5 m"]})
        return out
    if kind == "tank":
        r = (row["dia"] or (x1 - x0) / 1000) * 500
        add("massing", "tank", label, sc.Mesh().revolve(cx, cy, [(r, G), (r, G + h)], main, n=28), (100, 200), dimensions=dims)
        tank = sc.Mesh().revolve(cx, cy, [(r, G), (r, G + h), (r * 0.6, G + h + r * 0.12), (0.01, G + h + r * 0.18)], main, n=32)
        add("tank", "tank", label, tank, (300, 500), {"display_only_sizes": ["cone roof"]}, dimensions=dims)
        return out
    if kind == "coal":
        add("area", "terrain", f"{label} area", sc.Mesh().box(x0, y0, G, x1, y1, G + 300, "#6b6b6b"), (100, 500))
        if h:
            long_x = (x1 - x0) >= (y1 - y0)
            pile = sc.Mesh()
            if long_x:
                w = (y1 - y0) * 0.8
                pile.prism_x([(cy - w / 2, G + 300), (cy, G + h), (cy + w / 2, G + 300)], x0 + 5000, x1 - 5000, main)
            else:
                w = (x1 - x0) * 0.8
                pile.box(cx - w / 2, y0 + 5000, G + 300, cx + w / 2, y1 - 5000, G + h * 0.6, main)
            add("stockpile", "terrain", f"{label} stockpile", pile, (200, 500), {"display_only_sizes": ["pile 80% of the yard width"]})
        return out
    if kind == "switchyard":
        add("area", "terrain", f"{label} area", sc.Mesh().box(x0, y0, G, x1, y1, G + 300, main), (100, 500))
        gantry, gear = sc.Mesh(), sc.Mesh()
        long_x = (x1 - x0) >= (y1 - y0)
        span = (y1 - y0) if long_x else (x1 - x0)
        length = (x1 - x0) if long_x else (y1 - y0)
        for i in range(1, int(length // 15000)):
            a = (x0 if long_x else y0) + i * 15000
            for j in range(1, int(span // 20000) + 1):
                b0 = (y0 if long_x else x0) + (j - 1) * 20000 + 2000
                b1 = b0 + 16000
                p0 = (a, b0) if long_x else (b0, a)
                p1 = (a, b1) if long_x else (b1, a)
                for p in (p0, p1):
                    gantry.tube((p[0], p[1], G), (p[0], p[1], G + 12000), 250, colour=accent, n=4)
                gantry.tube((p0[0], p0[1], G + 12000), (p1[0], p1[1], G + 12000), 200, colour=accent, n=4)
                for k in range(3):
                    q = (a + 7000, b0 + 3000 + k * 5000) if long_x else (b0 + 3000 + k * 5000, a + 7000)
                    gear.box(q[0] - 800, q[1] - 800, G + 300, q[0] + 800, q[1] + 800, G + 2500, "#7c858d")
                    gear.tube((q[0], q[1], G + 2500), (q[0], q[1], G + 5500), 200, colour="#c9a06a", n=8)
        add("gantries", "gantry", f"{label} gantries", gantry, (300, 500), {"display_only_sizes": ["gantries every 15 m, 12 m high"]})
        add("equipment", "gantry", f"{label} equipment", gear, (400, 500), {"display_only_sizes": ["breakers and insulators"]})
        return out
    if kind == "transformer":
        th = h or 6000
        add("massing", "transformer", label, sc.Mesh().box(x0, y0, G, x1, y1, G + th, main), (100, 200), dimensions=dims)
        t = sc.Mesh().box(x0 + 1500, y0 + 1000, G, x1 - 1500, y1 - 1000, G + th * 0.7, main)
        t.tube((x0 + 2000, cy, G + th * 0.85), (x1 - 2000, cy, G + th * 0.85), min(900, (y1 - y0) / 6), colour=main, n=12)
        add("tank", "transformer", label, t, (300, 500), dimensions=dims)
        fins, bush = sc.Mesh(), sc.Mesh()
        for k in range(int((x1 - x0 - 3000) // 700)):
            fx = x0 + 1800 + k * 700
            for side in (y0 + 200, y1 - 1000):
                fins.box(fx, side, G + 500, fx + 120, side + 800, G + th * 0.65, "#6d7a5c")
        for k in range(3):
            bx = x0 + (x1 - x0) * (k + 1) / 4
            bush.tube((bx, cy, G + th * 0.7), (bx, cy, G + th * 1.15), 220, 120, colour=accent, n=10)
        add("radiators", "transformer", f"{label} radiators", fins, (400, 500), {"display_only_sizes": ["radiator fins"]})
        add("bushings", "transformer", f"{label} bushings", bush, (400, 500), {"display_only_sizes": ["bushings"]})
        return out
    # Buildings: turbine hall, boiler house, ESP, admin, pump house, workshops ...
    if not h:
        add("area", "terrain", label, sc.Mesh().box(x0, y0, G, x1, y1, G + 300, main), (100, 500))
        return out
    add("massing", "building", label, sc.Mesh().box(x0, y0, G, x1, y1, G + h, main), (100, 100), dimensions=dims)
    frame = sc.Mesh()
    nx = max(2, round((x1 - x0) / 10000))
    ny = max(2, round((y1 - y0) / 10000))
    for i in range(nx + 1):
        for j in range(ny + 1):
            px, py = x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny
            frame.box(px - 400, py - 400, G, px + 400, py + 400, G + h, "#8e98a3")
    frame.box(x0, y0, G + h - 800, x1, y1, G + h, "#8e98a3")
    add("frame", "steel", f"{label} frame", frame, (200, 200), {"display_only_sizes": ["about 10 m column bays"]})
    shell = sc.Mesh().box(x0, y0, G, x1, y1, G + h, main)
    add("cladding", "building", label, shell, (300, 500), dimensions=dims)
    bands = sc.Mesh()
    for frac in (0.35, 0.7):
        z = G + h * frac
        bands.box(x0 - 60, y0 - 60, z, x1 + 60, y1 + 60, z + min(2500, h * 0.08), accent)
    bands.box(x0 - 100, y0 - 100, G + h, x1 + 100, y1 + 100, G + h + 900, "#5b6168")
    add("bands", "building", f"{label} louvres and parapet", bands, (400, 500), {"display_only_sizes": ["louvre bands", "roof parapet 0.9 m"]})
    return out


LOD_NOTES = {
    "100": "LOD 100 — plant massing: every scheduled structure as a block or cylinder at its layout position.",
    "200": "LOD 200 — building frames, chimney and cooling tower shells, tanks and the coal stockpile.",
    "300": "LOD 300 — cladding and roofs, cooling tower raker columns and basins, transformer tanks, switchyard gantries.",
    "400": "LOD 400 — louvre bands and parapets, chimney platforms, transformer radiators and bushings, switchyard equipment.",
    "500": "LOD 500 — finishes and colours, chimney aviation bands, plant ground.",
}
