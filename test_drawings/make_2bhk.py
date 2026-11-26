"""
Generate a test 2BHK ground-floor plan PDF (A3, 1:100) drawn to common CAD conventions:
grid with bubbles, 230 mm brick walls on grid lines, 115 mm partitions (same ANSI31 hatch),
230x300 RCC columns, door swings, windows, ventilators, furniture, dimensions, room labels,
level notes in metres. Output: 2BHK_GROUND_FLOOR_PLAN.pdf next to this script.
"""
import math
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "2BHK_GROUND_FLOOR_PLAN.pdf"
PT_PER_MM = 72 / 25.4 / 100          # 1:100 -> plan mm to PDF points
OX, OY = 260.0, 190.0                # grid A/1 on the sheet
BLACK, GREY, HATCH = (0, 0, 0), (0.45, 0.45, 0.45), (0.5, 0.5, 0.5)

GX = {"A": 0, "B": 3900, "C": 6300, "D": 11400}
GY = {"1": 0, "2": 3900, "3": 6900, "4": 10200}
EXT, INT = 230, 115
COL = (230, 300)                     # column along x, along y (mm)

doc = pymupdf.open()
page = doc.new_page(width=1190.55, height=841.89)   # A3 landscape


def P(x, y):
    return pymupdf.Point(OX + x * PT_PER_MM, OY + y * PT_PER_MM)


def line(a, b, color=BLACK, width=0.35, dashes=None):
    page.draw_line(a, b, color=color, width=width, dashes=dashes)


def text(x, y, s, size=6.0, color=BLACK, mm=True):
    point = P(x, y) if mm else pymupdf.Point(x, y)
    page.insert_text(point, s, fontsize=size, fontname="helv", color=color)


def hatch_rect(x0, y0, x1, y1, step_pt=3.2):
    """ANSI31 (45 degree) hatch clipped to a rectangle given in points."""
    c = -(y1 - y0)
    while c < (x1 - x0):
        pts = []
        # line: (x - x0) - (y - y0) = c  -> y = y0 + (x - x0) - c ... in page coords (y down) use x - y = const
        for x in (x0, x1):
            y = y1 - ((x - x0) - c)
            if y0 <= y <= y1:
                pts.append((x, y))
        for y in (y0, y1):
            x = x0 + c + (y1 - y)
            if x0 <= x <= x1:
                pts.append((x, y))
        pts = sorted(set((round(px, 3), round(py, 3)) for px, py in pts))
        if len(pts) >= 2:
            page.draw_line(pymupdf.Point(*pts[0]), pymupdf.Point(*pts[-1]), color=HATCH, width=0.15)
        c += step_pt


def wall(x0, y0, x1, y1, thickness, openings=()):
    """Straight wall on a centreline (x0,y0)-(x1,y1) in mm; openings are (start, end) along it."""
    horizontal = y0 == y1
    a0, a1 = (min(x0, x1), max(x0, x1)) if horizontal else (min(y0, y1), max(y0, y1))
    cuts = sorted(openings)
    pieces, start = [], a0
    for s, e in cuts:
        pieces.append((start, s))
        start = e
    pieces.append((start, a1))
    h = thickness / 2
    for s, e in pieces:
        if e - s <= 1:
            continue
        if horizontal:
            r = (P(s, y0 - h), P(e, y0 + h))
        else:
            r = (P(x0 - h, s), P(x0 + h, e))
        (ax, ay), (bx, by) = (r[0].x, r[0].y), (r[1].x, r[1].y)
        if horizontal:
            line(pymupdf.Point(ax, ay), pymupdf.Point(bx, ay))
            line(pymupdf.Point(ax, by), pymupdf.Point(bx, by))
            line(pymupdf.Point(ax, ay), pymupdf.Point(ax, by), width=0.25)
            line(pymupdf.Point(bx, ay), pymupdf.Point(bx, by), width=0.25)
        else:
            line(pymupdf.Point(ax, ay), pymupdf.Point(ax, by))
            line(pymupdf.Point(bx, ay), pymupdf.Point(bx, by))
            line(pymupdf.Point(ax, ay), pymupdf.Point(bx, ay), width=0.25)
            line(pymupdf.Point(ax, by), pymupdf.Point(bx, by), width=0.25)
        hatch_rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def door(hinge_x, hinge_y, width, direction, swing):
    """Door leaf + quarter arc. direction: leaf direction unit (dx,dy); swing: arc end direction."""
    hx, hy = hinge_x, hinge_y
    leaf_end = (hx + swing[0] * width, hy + swing[1] * width)
    line(P(hx, hy), P(*leaf_end), width=0.3)
    closed = (hx + direction[0] * width, hy + direction[1] * width)
    # quarter arc from closed position to open position (bezier approximation)
    k = 0.5523
    c1 = (closed[0] + swing[0] * width * k, closed[1] + swing[1] * width * k)
    c2 = (leaf_end[0] + direction[0] * width * k, leaf_end[1] + direction[1] * width * k)
    page.draw_bezier(P(*closed), P(*c1), P(*c2), P(*leaf_end), color=GREY, width=0.2)


def window(x0, y0, x1, y1, thickness):
    """Window in an opening: three thin lines along the wall."""
    horizontal = y0 == y1
    for f in (-0.5, 0.0, 0.5):
        off = f * thickness * 0.6
        if horizontal:
            line(P(x0, y0 + off), P(x1, y1 + off), width=0.2)
        else:
            line(P(x0 + off, y0), P(x1 + off, y1), width=0.2)


# ---------------------------------------------------------------- grid + bubbles
for label, x in GX.items():
    line(P(x, -2200), P(x, 12400), color=GREY, width=0.15, dashes="[6 2 1 2] 0")
    for y in (-2900, 13100):
        c = P(x, y)
        page.draw_circle(c, 7, color=BLACK, width=0.3)
        page.insert_text(pymupdf.Point(c.x - 2.6, c.y + 2.6), label, fontsize=8, fontname="helv")
for label, y in GY.items():
    line(P(-2200, y), P(13600, y), color=GREY, width=0.15, dashes="[6 2 1 2] 0")
    for x in (-2900, 14300):
        c = P(x, y)
        page.draw_circle(c, 7, color=BLACK, width=0.3)
        page.insert_text(pymupdf.Point(c.x - 2.4, c.y + 2.6), label, fontsize=8, fontname="helv")

# ---------------------------------------------------------------- walls
X, Y = GX, GY
wall(X["A"], Y["1"], X["D"], Y["1"], EXT, openings=[(1200, 2700), (4800, 5400), (7800, 9600)])   # front: windows, ventilator
wall(X["A"], Y["4"], X["D"], Y["4"], EXT, openings=[(1500, 2500), (5000, 5600)])                 # back: main door, window
wall(X["A"], Y["1"], X["A"], Y["4"], EXT, openings=[(1000, 2500), (6000, 8000)])                 # left windows
wall(X["D"], Y["1"], X["D"], Y["4"], EXT, openings=[(1000, 2500), (7800, 9300)])                 # right windows
wall(X["B"], Y["1"], X["B"], Y["2"], EXT)                                                         # bedroom 1 | toilet
wall(X["C"], Y["1"], X["C"], Y["2"], EXT)                                                         # toilet | master
wall(X["A"], Y["2"], X["D"], Y["2"], EXT, openings=[(2800, 3700), (4300, 5200), (9600, 10500)])  # bedroom doors
wall(X["C"], Y["2"], X["C"], Y["4"], EXT, openings=[(4300, 5050), (7600, 8500)])                  # toilet 2 door, kitchen opening
wall(X["C"], Y["3"], X["D"], Y["3"], EXT, openings=[(9600, 10500)])                               # store door
wall(X["B"], 2100, X["C"], 2100, INT, openings=[(4500, 5250)])                                    # toilet 1 / dress
wall(8800, Y["2"], 8800, Y["3"], INT)                                                             # toilet 2 / store

# ---------------------------------------------------------------- openings
window(1200, 0, 2700, 0, EXT); window(7800, 0, 9600, 0, EXT); window(4800, 0, 5400, 0, EXT)
window(5000, 10200, 5600, 10200, EXT)
window(0, 1000, 0, 2500, EXT); window(0, 6000, 0, 8000, EXT)
window(11400, 1000, 11400, 2500, EXT); window(11400, 7800, 11400, 9300, EXT)
door(1500, 10200, 1000, (1, 0), (0, -1))          # main door
door(2800, 3900, 900, (1, 0), (0, -1))            # bedroom 1
door(4300, 3900, 900, (1, 0), (0, -1))            # dress
door(4500, 2100, 750, (1, 0), (0, -1))            # toilet 1
door(9600, 3900, 900, (1, 0), (0, -1))            # master
door(6300, 4300, 750, (0, 1), (1, 0))             # toilet 2
door(9600, 6900, 750, (1, 0), (0, -1))            # store

# ---------------------------------------------------------------- columns (filled)
for gx in X.values():
    for gy in Y.values():
        r = pymupdf.Rect(P(gx - COL[0] / 2, gy - COL[1] / 2), P(gx + COL[0] / 2, gy + COL[1] / 2))
        page.draw_rect(r, color=BLACK, fill=BLACK, width=0.3)

# ---------------------------------------------------------------- furniture & fixtures
def rect(x0, y0, x1, y1, width=0.2, color=GREY):
    page.draw_rect(pymupdf.Rect(P(x0, y0), P(x1, y1)), color=color, width=width)

rect(600, 600, 2400, 2600); rect(450, 700, 900, 1150); rect(2100, 700, 2550, 1150)      # bed 1 + side tables
rect(7800, 600, 9600, 2600); rect(7300, 700, 7750, 1150); rect(9650, 700, 10100, 1150)   # master bed
rect(10600, 300, 11150, 2000)                                                            # wardrobe
rect(500, 4500, 2600, 5300); rect(500, 5300, 1300, 6500)                                 # sofa L
page.draw_circle(P(4200, 8200), 900 * PT_PER_MM, color=GREY, width=0.2)                  # dining table
rect(6450, 9400, 11250, 10050); rect(10650, 7050, 11250, 9400)                            # kitchen counter L
page.draw_oval(pymupdf.Rect(P(5300, 600), P(5800, 1300)), color=GREY, width=0.2)       # WC 1
page.draw_oval(pymupdf.Rect(P(7300, 4400), P(7800, 5100)), color=GREY, width=0.2)       # WC 2
rect(4100, 300, 4700, 800); rect(6500, 6100, 7100, 6600)                                 # basins

# ---------------------------------------------------------------- dimensions
def dim_h(x0, x1, y, label):
    line(P(x0, y), P(x1, y), width=0.2)
    for x in (x0, x1):
        line(P(x - 150, y + 150), P(x + 150, y - 150), width=0.3)
        line(P(x, y - 400), P(x, y + 400), width=0.15)
    text((x0 + x1) / 2 - 250, y - 150, label, size=5.5)

def dim_v(y0, y1, x, label):
    line(P(x, y0), P(x, y1), width=0.2)
    for y in (y0, y1):
        line(P(x - 150, y + 150), P(x + 150, y - 150), width=0.3)
        line(P(x - 400, y), P(x + 400, y), width=0.15)
    text(x - 900, (y0 + y1) / 2, label, size=5.5)

dim_h(0, 3900, -1300, "3900"); dim_h(3900, 6300, -1300, "2400"); dim_h(6300, 11400, -1300, "5100")
dim_h(0, 11400, -1900, "11400")
dim_v(0, 3900, -1300, "3900"); dim_v(3900, 6900, -1300, "3000"); dim_v(6900, 10200, -1300, "3300")
dim_v(0, 10200, -1900, "10200")

# ---------------------------------------------------------------- labels and levels
for x, y, name, size in [
    (700, 3000, "BEDROOM 1", "3900 X 3900"), (4050, 1300, "TOILET", "2400 X 2100"),
    (4050, 3000, "DRESS", ""), (7400, 3100, "MASTER BEDROOM", "5100 X 3900"),
    (900, 7400, "LIVING / DINING", "6300 X 6300"), (6700, 5900, "TOILET", "2500 X 3000"),
    (9200, 5300, "STORE", ""), (7800, 8200, "KITCHEN", "5100 X 3300"),
]:
    text(x, y, name, size=6.5)
    if size:
        text(x, y + 450, size, size=5)
text(900, 8350, "FFL +0.450", size=5)
text(1200, 11200, "ENTRANCE", size=6); text(1200, 11650, "PLINTH LVL SSL +0.300", size=5)

# windows / doors tags
for x, y, t in [(1700, -500, "W1"), (8400, -500, "W2"), (4900, -500, "V"), (-900, 1600, "W1"), (-900, 6800, "W3"),
                (11700, 1600, "W1"), (11700, 8400, "W1"), (1800, 10900, "D"), (3000, 3500, "D1"), (9800, 3500, "D1"),
                (4500, 3500, "D1"), (4700, 1800, "D2"), (6500, 4800, "D2"), (9800, 6500, "D2"), (5100, 10900, "V")]:
    text(x, y, t, size=5)

# door & window schedule (common on residential plans)
schedule = [
    "DOOR & WINDOW SCHEDULE",
    "D   1000 X 2100  MAIN DOOR",
    "D1  900 X 2100",
    "D2  750 X 2100",
    "W1  1500 X 1200  SILL 900",
    "W2  1800 X 1200  SILL 900",
    "W3  2000 X 1200  SILL 900",
    "V   600 X 450  SILL 1800",
]
for i, row in enumerate(schedule):
    page.insert_text(pymupdf.Point(1010, 560 + i * 10), row, fontsize=6, fontname="helv")

# ---------------------------------------------------------------- legend, notes, title block
lx, ly = 860.0, 560.0
page.insert_text(pymupdf.Point(lx, ly - 14), "LEGEND", fontsize=8, fontname="helv")
for i, label in enumerate(["230 THK. BRICK MASONRY WALL", "115 THK. BRICK PARTITION WALL"]):
    y = ly + i * 22
    sw = pymupdf.Rect(lx, y, lx + 30, y + 12)
    page.draw_rect(sw, color=BLACK, width=0.3)
    hatch_rect(sw.x0, sw.y0, sw.x1, sw.y1)
    page.insert_text(pymupdf.Point(lx + 40, y + 9), label, fontsize=6.5, fontname="helv")
y = ly + 44
page.draw_rect(pymupdf.Rect(lx, y, lx + 30, y + 12), color=BLACK, fill=BLACK, width=0.3)
page.insert_text(pymupdf.Point(lx + 40, y + 9), "RCC COLUMN 230 X 300", fontsize=6.5, fontname="helv")

notes = [
    "NOTES:",
    "1. ALL DIMENSIONS ARE IN MILLIMETRES UNLESS NOTED OTHERWISE.",
    "2. ALL LEVELS ARE IN METRES.",
    "3. PLINTH SLAB: SSL +0.300",
    "4. ROOF SLAB: SSL +3.300",
    "5. FINISHED FLOOR: FFL +0.450, TOILETS FFL +0.420",
    "6. PARAPET 1.0M HIGH ON ROOF.",
    "7. DO NOT SCALE FROM THIS DRAWING.",
]
for i, n in enumerate(notes):
    page.insert_text(pymupdf.Point(860, 660 + i * 10), n, fontsize=6, fontname="helv")

page.draw_rect(pymupdf.Rect(20, 20, 1170.55, 821.89), color=BLACK, width=0.8)
page.draw_rect(pymupdf.Rect(850, 745, 1160, 812), color=BLACK, width=0.5)
page.insert_text(pymupdf.Point(860, 765), "PROPOSED 2BHK RESIDENCE", fontsize=11, fontname="hebo")
page.insert_text(pymupdf.Point(860, 782), "GROUND FLOOR PLAN", fontsize=9, fontname="helv")
page.insert_text(pymupdf.Point(860, 800), "SCALE 1:100 (A3)        DRG. NO. A-101        REV. 0", fontsize=7, fontname="helv")

doc.save(OUT)
print("saved", OUT, OUT.stat().st_size, "bytes")
d = pymupdf.open(OUT)
print("paths", len(d[0].get_drawings()))
d[0].get_pixmap(dpi=90).save(OUT.with_suffix(".png"))
