"""
Generate the 2BHK ground floor as a DXF (what AutoCAD's Save As > DXF gives), drawn the usual way:
units in millimetres, layers A-WALL (closed outline per wall piece), S-COLS (filled column outlines),
A-DOOR / A-GLAZ (door and window blocks inserted in the wall gaps), A-ANNO (room names, sizes, tags),
A-NOTE (levels, schedule), A-GRID. Output: 2BHK_GROUND_FLOOR_PLAN.dxf next to this script.
"""
from pathlib import Path

import ezdxf

OUT = Path(__file__).parent / "2BHK_GROUND_FLOOR_PLAN.dxf"
doc = ezdxf.new("R2018", setup=True)
doc.header["$INSUNITS"] = 4          # millimetres
msp = doc.modelspace()
for name, colour in [("A-WALL", 7), ("A-WALL-PATT", 8), ("S-COLS", 7), ("A-DOOR", 30), ("A-GLAZ", 4),
                     ("A-ANNO", 2), ("A-NOTE", 2), ("A-GRID", 1), ("A-FURN", 8)]:
    doc.layers.add(name, color=colour)

# Plan coordinates in mm with y "down" as on the PDF; CAD y is up, so draw at -y.
GX = {"A": 0, "B": 3900, "C": 6300, "D": 11400}
GY = {"1": 0, "2": 3900, "3": 6900, "4": 10200}
EXT, INT = 230, 115


def P(x, y):
    return (x, -y)


# --- blocks: a single-leaf door (1000 wide, hinge at origin, swing into +y) and a window (1000 long, unit thick)
door = doc.blocks.new("DOOR_SINGLE")
door.add_line((0, 0), (0, 1000))
door.add_arc((0, 0), 1000, 0, 90)
window = doc.blocks.new("WINDOW_FIXED")
for y in (-0.3, 0.0, 0.3):
    window.add_line((0, y), (1000, y))
window.add_line((0, -0.5), (0, 0.5)); window.add_line((1000, -0.5), (1000, 0.5))


def wall(x0, y0, x1, y1, t, openings=()):
    horizontal = y0 == y1
    a0, a1 = (min(x0, x1), max(x0, x1)) if horizontal else (min(y0, y1), max(y0, y1))
    start, h = a0, t / 2
    for s, e in sorted(openings) + [(a1, a1)]:
        if s - start > 1:
            if horizontal:
                pts = [P(start, y0 - h), P(s, y0 - h), P(s, y0 + h), P(start, y0 + h)]
            else:
                pts = [P(x0 - h, start), P(x0 + h, start), P(x0 + h, s), P(x0 - h, s)]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-WALL"})
            hatch = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-PATT"})
            hatch.set_pattern_fill("ANSI31", scale=10)
            hatch.paths.add_polyline_path(pts, is_closed=True)
        start = e


def door_at(x, y, width, along_x=True, flip=False):
    rotation = (0 if along_x else -90) + (180 if flip else 0)    # the leaf closes across the gap
    msp.add_blockref("DOOR_SINGLE", P(x, y), dxfattribs={"layer": "A-DOOR", "xscale": width / 1000,
                                                         "yscale": width / 1000, "rotation": rotation})


def window_at(x, y, width, thickness, along_x=True):
    msp.add_blockref("WINDOW_FIXED", P(x, y), dxfattribs={"layer": "A-GLAZ", "xscale": width / 1000,
                                                          "yscale": thickness, "rotation": 0 if along_x else -90})


X, Y = GX, GY
wall(X["A"], Y["1"], X["D"], Y["1"], EXT, [(1200, 2700), (4800, 5400), (7800, 9600)])
wall(X["A"], Y["4"], X["D"], Y["4"], EXT, [(1500, 2500), (5000, 5600)])
wall(X["A"], Y["1"], X["A"], Y["4"], EXT, [(1000, 2500), (6000, 8000)])
wall(X["D"], Y["1"], X["D"], Y["4"], EXT, [(1000, 2500), (7800, 9300)])
wall(X["B"], Y["1"], X["B"], Y["2"], EXT)
wall(X["C"], Y["1"], X["C"], Y["2"], EXT)
wall(X["A"], Y["2"], X["D"], Y["2"], EXT, [(2800, 3700), (4300, 5200), (9600, 10500)])
wall(X["C"], Y["2"], X["C"], Y["4"], EXT, [(4300, 5050), (7600, 8500)])
wall(X["C"], Y["3"], X["D"], Y["3"], EXT, [(9600, 10500)])
wall(X["B"], 2100, X["C"], 2100, INT, [(4500, 5250)])
wall(8800, Y["2"], 8800, Y["3"], INT)

for x0, x1, y in [(1200, 2700, 0), (4800, 5400, 0), (7800, 9600, 0), (5000, 5600, 10200)]:
    window_at(x0, y, x1 - x0, EXT)
for y0, y1, x in [(1000, 2500, 0), (6000, 8000, 0), (1000, 2500, 11400), (7800, 9300, 11400)]:
    window_at(x, y0, y1 - y0, EXT, along_x=False)
door_at(1500, 10200, 1000)
door_at(2800, 3900, 900); door_at(4300, 3900, 900); door_at(9600, 3900, 900)
door_at(4500, 2100, 750); door_at(9600, 6900, 900)
door_at(6300, 4300, 750, along_x=False)

for gx in X.values():
    for gy in Y.values():
        pts = [P(gx - 115, gy - 150), P(gx + 115, gy - 150), P(gx + 115, gy + 150), P(gx - 115, gy + 150)]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "S-COLS"})
        hatch = msp.add_hatch(color=7, dxfattribs={"layer": "S-COLS"})
        hatch.paths.add_polyline_path(pts, is_closed=True)

for label, x in X.items():
    msp.add_line(P(x, -2200), P(x, 12400), dxfattribs={"layer": "A-GRID"})
    for y in (-2900, 13100):
        msp.add_circle(P(x, y), 250, dxfattribs={"layer": "A-GRID"})
        msp.add_text(label, height=250, dxfattribs={"layer": "A-GRID"}).set_placement(P(x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
for label, y in Y.items():
    msp.add_line(P(-2200, y), P(13600, y), dxfattribs={"layer": "A-GRID"})
    for x in (-2900, 14300):
        msp.add_circle(P(x, y), 250, dxfattribs={"layer": "A-GRID"})
        msp.add_text(label, height=250, dxfattribs={"layer": "A-GRID"}).set_placement(P(x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def text(x, y, s, h=200, layer="A-ANNO"):
    msp.add_text(s, height=h, dxfattribs={"layer": layer}).set_placement(P(x, y))


for x, y, name, size in [(700, 3000, "BEDROOM 1", "3900 X 3900"), (4050, 1300, "TOILET", "2400 X 2100"),
                         (4050, 3000, "DRESS", ""), (7400, 3100, "MASTER BEDROOM", "5100 X 3900"),
                         (900, 7400, "LIVING / DINING", "6300 X 6300"), (6700, 5900, "TOILET", "2500 X 3000"),
                         (9200, 5300, "STORE", ""), (7800, 8200, "KITCHEN", "5100 X 3300")]:
    text(x, y, name, 220)
    if size:
        text(x, y + 450, size, 160)
for x, y, t in [(1700, -500, "W1"), (8400, -500, "W2"), (4900, -500, "V"), (-900, 1600, "W1"), (-900, 6800, "W3"),
                (11700, 1600, "W1"), (11700, 8400, "W1"), (1800, 10900, "D"), (3000, 3500, "D1"), (9800, 3500, "D1"),
                (4500, 3500, "D1"), (4700, 1800, "D2"), (6500, 4800, "D2"), (9800, 6500, "D2"), (5100, 10900, "V")]:
    text(x, y, t, 160)
for i, row in enumerate(["NOTES:", "ALL DIMENSIONS ARE IN MILLIMETRES.", "ALL LEVELS ARE IN METRES.",
                         "PLINTH SLAB: SSL +0.300", "ROOF SLAB: SSL +3.300", "FINISHED FLOOR: FFL +0.450",
                         "DOOR & WINDOW SCHEDULE", "D 1000 X 2100 MAIN DOOR", "D1 900 X 2100", "D2 750 X 2100",
                         "W1 1500 X 1200 SILL 900", "W2 1800 X 1200 SILL 900", "W3 2000 X 1200 SILL 900",
                         "V 600 X 450 SILL 1800", "PARAPET 1.0M HIGH ON ROOF."]):
    text(16000, 1000 + i * 400, row, 180, layer="A-NOTE")

msp.add_lwpolyline([P(500, 600), P(2400, 600), P(2400, 2600), P(500, 2600)], close=True, dxfattribs={"layer": "A-FURN"})
doc.saveas(OUT)
print("saved", OUT, OUT.stat().st_size, "bytes")
