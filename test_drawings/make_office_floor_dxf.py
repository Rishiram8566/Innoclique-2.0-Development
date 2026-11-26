"""
Generate a two-storey office building as DXF floor plans (what AutoCAD's Save As > DXF gives), to test office
fit-out, stairs and lifts: a 36 x 21.6 m floor on a 7.2 m column grid with an open-plan office (free-standing
columns inside), board and meeting rooms, a cafeteria / break-out area, reception, a cabin, a print room, toilets,
a server room and a team office along a corridor, and a core with a dog-leg staircase (riser lines, UP arrow) and
a lift shaft. Units: millimetres; layers as in make_2bhk_dxf.py.
Output: OFFICE_GROUND_FLOOR_PLAN.dxf and OFFICE_FIRST_FLOOR_PLAN.dxf next to this script.
"""
from pathlib import Path

import ezdxf

HERE = Path(__file__).parent
BAY = 7200
GX = [i * BAY for i in range(6)]          # 0 .. 36000
GY = [0, 7200, 14400, 21600]
EXT, INT = 230, 150
CORRIDOR = (14400, 16200)                 # y range of the corridor
STAIR = (18000, 21600)                    # x range of the stair core (south strip)
LIFT = (21600, 24000, 16200, 18600)       # lift shaft x0, x1, y0, y1


def P(x, y):
    return (x, -y)                        # plan y runs down; CAD y up


def plan(out: Path, labels, notes):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for name, colour in [("A-WALL", 7), ("A-WALL-PATT", 8), ("S-COLS", 7), ("A-DOOR", 30), ("A-GLAZ", 4),
                         ("A-ANNO", 2), ("A-NOTE", 2), ("A-GRID", 1), ("A-STRS", 3), ("A-LIFT", 5)]:
        doc.layers.add(name, color=colour)
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
        rotation = (0 if along_x else -90) + (180 if flip else 0)
        msp.add_blockref("DOOR_SINGLE", P(x, y), dxfattribs={"layer": "A-DOOR", "xscale": width / 1000,
                                                             "yscale": width / 1000, "rotation": rotation})

    def window_at(x, y, width, thickness, along_x=True):
        msp.add_blockref("WINDOW_FIXED", P(x, y), dxfattribs={"layer": "A-GLAZ", "xscale": width / 1000,
                                                              "yscale": thickness, "rotation": 0 if along_x else -90})

    def line(x0, y0, x1, y1, layer):
        msp.add_line(P(x0, y0), P(x1, y1), dxfattribs={"layer": layer})

    def text(x, y, s, h=250, layer="A-ANNO"):
        msp.add_text(s, height=h, dxfattribs={"layer": layer}).set_placement(P(x, y))

    # external walls: a 3 m window in every bay
    bay_windows = [(g + 2100, g + 5100) for g in GX[:-1]]
    side_windows = [(g + 2100, g + 5100) for g in GY[:-1]]
    wall(0, 0, 36000, 0, EXT, bay_windows)
    wall(0, 21600, 36000, 21600, EXT, [w for w in bay_windows if not (w[0] < STAIR[1] and w[1] > STAIR[0])])
    wall(0, 0, 0, 21600, EXT, [w for w in side_windows if w[0] < CORRIDOR[0] or w[0] > CORRIDOR[1]])
    wall(36000, 0, 36000, 21600, EXT, side_windows)
    for a, b in bay_windows:
        window_at(a, 0, b - a, EXT)
        if not (a < STAIR[1] and b > STAIR[0]):
            window_at(a, 21600, b - a, EXT)
    for a, b in side_windows:
        window_at(0, a, b - a, EXT, along_x=False); window_at(36000, a, b - a, EXT, along_x=False)

    # corridor walls, a door into every room, the stair and the lift
    north_doors = [(3000, 4000), (17000, 18000), (24000, 25000), (31000, 32000)]
    south_doors = [(4000, 5000), (12000, 12900), (15500, 16400), (18300, 19900), (22300, 23300),
                   (25500, 26400), (29300, 30200), (32500, 33400)]
    wall(0, CORRIDOR[0], 36000, CORRIDOR[0], INT, north_doors)
    wall(0, CORRIDOR[1], 36000, CORRIDOR[1], INT, south_doors)
    for a, b in north_doors:
        door_at(a, CORRIDOR[0], b - a, flip=True)
    for a, b in south_doors:
        door_at(a, CORRIDOR[1], b - a)

    # north strip: open-plan office (x 0-21.6 m), two meeting rooms, cafeteria / break-out
    wall(21600, 0, 21600, CORRIDOR[0], INT, [(9000, 10000)])
    wall(21600, 7200, 36000, 7200, INT, [(25500, 26400)])
    wall(28800, 0, 28800, 7200, INT)
    door_at(21600, 9000, 1000, along_x=False)
    door_at(25500, 7200, 900)
    # south strip partitions, the stair core and the lift shaft
    for x in (10800, 14400, STAIR[0], STAIR[1], LIFT[1], 28800, 31200):
        wall(x, CORRIDOR[1], x, 21600, INT)
    wall(LIFT[0], LIFT[3], LIFT[1], LIFT[3], INT)

    # dog-leg stair: two flights of 12 riser lines (250 mm going) up from the corridor, a landing at the far end
    for k in range(12):
        y = 16800 + k * 250
        line(STAIR[0] + 150, y, STAIR[0] + 1700, y, "A-STRS")             # flight 1, going up (south)
        line(STAIR[0] + 1900, y, STAIR[1] - 150, y, "A-STRS")             # flight 2, coming back (north)
    line(STAIR[0] + 1800, 16800, STAIR[0] + 1800, 19550, "A-STRS")        # the wall between the flights' handrails
    text(STAIR[0] + 700, 16550, "UP", 200)
    # lift car outline with its door, inside the shaft
    for (x0, y0, x1, y1) in [(LIFT[0] + 250, LIFT[2] + 250, LIFT[1] - 250, LIFT[2] + 250),
                             (LIFT[0] + 250, LIFT[3] - 250, LIFT[1] - 250, LIFT[3] - 250),
                             (LIFT[0] + 250, LIFT[2] + 250, LIFT[0] + 250, LIFT[3] - 250),
                             (LIFT[1] - 250, LIFT[2] + 250, LIFT[1] - 250, LIFT[3] - 250)]:
        line(x0, y0, x1, y1, "A-LIFT")
    line(LIFT[0] + 250, LIFT[2] + 250, LIFT[1] - 250, LIFT[3] - 250, "A-LIFT")
    line(LIFT[1] - 250, LIFT[2] + 250, LIFT[0] + 250, LIFT[3] - 250, "A-LIFT")

    # columns on the whole grid (the two inside the open-plan office stand free)
    for gx in GX:
        for gy in GY:
            pts = [P(gx - 200, gy - 200), P(gx + 200, gy - 200), P(gx + 200, gy + 200), P(gx - 200, gy + 200)]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "S-COLS"})
            hatch = msp.add_hatch(color=7, dxfattribs={"layer": "S-COLS"})
            hatch.paths.add_polyline_path(pts, is_closed=True)
    for i, x in enumerate(GX):
        msp.add_line(P(x, -2200), P(x, 23800), dxfattribs={"layer": "A-GRID"})
        msp.add_circle(P(x, -2900), 250, dxfattribs={"layer": "A-GRID"})
        msp.add_text(str(i + 1), height=250, dxfattribs={"layer": "A-GRID"}).set_placement(
            P(x, -2900), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    for label, y in zip("ABCD", GY):
        msp.add_line(P(-2200, y), P(38200, y), dxfattribs={"layer": "A-GRID"})
        msp.add_circle(P(-2900, y), 250, dxfattribs={"layer": "A-GRID"})
        msp.add_text(label, height=250, dxfattribs={"layer": "A-GRID"}).set_placement(
            P(-2900, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    for x, y, name in labels:
        text(x, y, name)
    for i, row in enumerate(["NOTES:", "ALL DIMENSIONS ARE IN MILLIMETRES.", "ALL LEVELS ARE IN METRES."] + notes):
        text(40000, 1000 + i * 450, row, 200, layer="A-NOTE")
    doc.saveas(out)
    print("saved", out, out.stat().st_size, "bytes")


CORE = [(19000, 18200, "STAIRCASE"), (22300, 17600, "LIFT"), (25700, 19000, "TOILET"), (29200, 19000, "SERVER ROOM"),
        (15000, 15500, "CORRIDOR")]
# The open-plan label stands in line with a free-standing column (y 7200), as labels often do.
plan(HERE / "OFFICE_GROUND_FLOOR_PLAN.dxf",
     [(9500, 7200, "OPEN PLAN OFFICE"), (23500, 3400, "BOARD ROOM"), (30600, 3400, "MEETING ROOM"),
      (27000, 10600, "CAFETERIA"), (4200, 19000, "RECEPTION"), (11300, 19000, "MANAGER CABIN"),
      (14900, 19000, "PRINT ROOM"), (32500, 19000, "OFFICE")] + CORE,
     ["GROUND FLOOR: SSL +0.150"])
plan(HERE / "OFFICE_FIRST_FLOOR_PLAN.dxf",
     [(9500, 7200, "OPEN PLAN OFFICE"), (23500, 3400, "MEETING ROOM"), (30600, 3400, "CONFERENCE ROOM"),
      (27000, 10600, "BREAK-OUT AREA"), (4200, 19000, "WORKSTATIONS"), (11300, 19000, "CABIN"),
      (14900, 19000, "COPY / PRINT"), (32500, 19000, "OFFICE")] + CORE,
     ["FIRST FLOOR: SSL +4.050", "ROOF SLAB: SSL +7.950", "PARAPET 1.1M HIGH ON ROOF."])
