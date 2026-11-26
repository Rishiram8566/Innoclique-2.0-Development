"""
Generate a two-storey (G+1) house drawing set: GROUND and FIRST floor plans (A3, 1:100), drawn to the
same conventions as make_2bhk.py: grid A-D / 1-3 with bubbles, 230 mm brick walls (ANSI31 hatch, legend),
115 mm partitions, 230 x 300 RCC columns, door swings, windows, tags, schedule, room labels, levels in metres,
and roof / envelope notes (gable roof pitch, ridge direction, overhang, roof sheet, gutters, slab thickness,
ground level). Outputs G1_HOUSE_GROUND_FLOOR_PLAN.pdf and G1_HOUSE_FIRST_FLOOR_PLAN.pdf next to this script.
"""
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
PT_PER_MM = 72 / 25.4 / 100          # 1:100 -> plan mm to PDF points
OX, OY = 300.0, 220.0                # grid A/1 on the sheet
BLACK, GREY, HATCH = (0, 0, 0), (0.45, 0.45, 0.45), (0.5, 0.5, 0.5)
X = {"A": 0, "B": 4200, "C": 7800, "D": 12000}
Y = {"1": 0, "2": 4500, "3": 9000}
EXT, INT = 230, 115
COL = (230, 300)

SCHEDULE = [
    "DOOR & WINDOW SCHEDULE",
    "D   1200 X 2100  MAIN DOOR",
    "D1  900 X 2100",
    "D2  750 X 2100",
    "W1  1800 X 1500  SILL 750",
    "W2  1500 X 1500  SILL 750",
    "W3  1600 X 1200  SILL 1500",
    "W4  2400 X 1500  SILL 750",
    "V   600 X 450  SILL 1800",
]


class Sheet:
    def __init__(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=1190.55, height=841.89)   # A3 landscape

    def P(self, x, y):
        return pymupdf.Point(OX + x * PT_PER_MM, OY + y * PT_PER_MM)

    def line(self, a, b, color=BLACK, width=0.35, dashes=None):
        self.page.draw_line(a, b, color=color, width=width, dashes=dashes)

    def text(self, x, y, s, size=6.0):
        self.page.insert_text(self.P(x, y), s, fontsize=size, fontname="helv", color=BLACK)

    def note(self, x_pt, y_pt, s, size=6.0, font="helv"):
        self.page.insert_text(pymupdf.Point(x_pt, y_pt), s, fontsize=size, fontname=font)

    def hatch_rect(self, x0, y0, x1, y1, step_pt=3.2):
        c = -(y1 - y0)
        while c < (x1 - x0):
            pts = []
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
                self.page.draw_line(pymupdf.Point(*pts[0]), pymupdf.Point(*pts[-1]), color=HATCH, width=0.15)
            c += step_pt

    def wall(self, x0, y0, x1, y1, thickness, openings=()):
        horizontal = y0 == y1
        a0, a1 = (min(x0, x1), max(x0, x1)) if horizontal else (min(y0, y1), max(y0, y1))
        pieces, start = [], a0
        for s, e in sorted(openings):
            pieces.append((start, s))
            start = e
        pieces.append((start, a1))
        h = thickness / 2
        for s, e in pieces:
            if e - s <= 1:
                continue
            r = (self.P(s, y0 - h), self.P(e, y0 + h)) if horizontal else (self.P(x0 - h, s), self.P(x0 + h, e))
            (ax, ay), (bx, by) = (r[0].x, r[0].y), (r[1].x, r[1].y)
            Pt = pymupdf.Point
            if horizontal:
                self.line(Pt(ax, ay), Pt(bx, ay)); self.line(Pt(ax, by), Pt(bx, by))
                self.line(Pt(ax, ay), Pt(ax, by), width=0.25); self.line(Pt(bx, ay), Pt(bx, by), width=0.25)
            else:
                self.line(Pt(ax, ay), Pt(ax, by)); self.line(Pt(bx, ay), Pt(bx, by))
                self.line(Pt(ax, ay), Pt(bx, ay), width=0.25); self.line(Pt(ax, by), Pt(bx, by), width=0.25)
            self.hatch_rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def door(self, hx, hy, width, direction, swing):
        leaf_end = (hx + swing[0] * width, hy + swing[1] * width)
        self.line(self.P(hx, hy), self.P(*leaf_end), width=0.3)
        closed = (hx + direction[0] * width, hy + direction[1] * width)
        k = 0.5523
        c1 = (closed[0] + swing[0] * width * k, closed[1] + swing[1] * width * k)
        c2 = (leaf_end[0] + direction[0] * width * k, leaf_end[1] + direction[1] * width * k)
        self.page.draw_bezier(self.P(*closed), self.P(*c1), self.P(*c2), self.P(*leaf_end), color=GREY, width=0.2)

    def window(self, x0, y0, x1, y1, thickness=EXT):
        horizontal = y0 == y1
        for f in (-0.5, 0.0, 0.5):
            off = f * thickness * 0.6
            if horizontal:
                self.line(self.P(x0, y0 + off), self.P(x1, y1 + off), width=0.2)
            else:
                self.line(self.P(x0 + off, y0), self.P(x1 + off, y1), width=0.2)

    def grid_and_columns(self):
        for label, x in X.items():
            self.line(self.P(x, -2200), self.P(x, 11200), color=GREY, width=0.15, dashes="[6 2 1 2] 0")
            for y in (-2900, 11900):
                c = self.P(x, y)
                self.page.draw_circle(c, 7, color=BLACK, width=0.3)
                self.page.insert_text(pymupdf.Point(c.x - 2.6, c.y + 2.6), label, fontsize=8, fontname="helv")
        for label, y in Y.items():
            self.line(self.P(-2200, y), self.P(14200, y), color=GREY, width=0.15, dashes="[6 2 1 2] 0")
            for x in (-2900, 14900):
                c = self.P(x, y)
                self.page.draw_circle(c, 7, color=BLACK, width=0.3)
                self.page.insert_text(pymupdf.Point(c.x - 2.4, c.y + 2.6), label, fontsize=8, fontname="helv")
        for gx in X.values():
            for gy in Y.values():
                r = pymupdf.Rect(self.P(gx - COL[0] / 2, gy - COL[1] / 2), self.P(gx + COL[0] / 2, gy + COL[1] / 2))
                self.page.draw_rect(r, color=BLACK, fill=BLACK, width=0.3)

    def stair(self, arrow):
        """
        Dog-leg stair in bay B-C / 1-2: two 1500 mm flights of 10 risers (250 mm going) either side of a
        centre line, with a 1500 mm half landing at the north wall. Riser lines are thin lines (not walls).
        The ground floor marks the first flight UP; the first floor marks the same stair DN.
        """
        for k in range(10):
            y = 1615 + k * 250
            self.line(self.P(4400, y), self.P(5900, y), color=GREY, width=0.15)
            self.line(self.P(6100, y), self.P(7600, y), color=GREY, width=0.15)
        self.line(self.P(6000, 1615), self.P(6000, 3865), color=GREY, width=0.3)
        if arrow == "UP":
            self.text(4950, 4250, "UP", size=6)
        else:
            self.text(6650, 4250, "DN", size=6)

    def dims(self):
        def dim_h(x0, x1, y, label):
            self.line(self.P(x0, y), self.P(x1, y), width=0.2)
            for x in (x0, x1):
                self.line(self.P(x - 150, y + 150), self.P(x + 150, y - 150), width=0.3)
                self.line(self.P(x, y - 400), self.P(x, y + 400), width=0.15)
            self.text((x0 + x1) / 2 - 250, y - 150, label, size=5.5)

        def dim_v(y0, y1, x, label):
            self.line(self.P(x, y0), self.P(x, y1), width=0.2)
            for y in (y0, y1):
                self.line(self.P(x - 150, y + 150), self.P(x + 150, y - 150), width=0.3)
                self.line(self.P(x - 400, y), self.P(x + 400, y), width=0.15)
            self.text(x - 900, (y0 + y1) / 2, label, size=5.5)

        dim_h(0, 4200, -1300, "4200"); dim_h(4200, 7800, -1300, "3600"); dim_h(7800, 12000, -1300, "4200")
        dim_h(0, 12000, -1900, "12000")
        dim_v(0, 4500, -1300, "4500"); dim_v(4500, 9000, -1300, "4500"); dim_v(0, 9000, -1900, "9000")

    def legend_schedule_title(self, title, drawing_no, notes):
        lx, ly = 900.0, 520.0
        self.note(lx, ly - 14, "LEGEND", size=8)
        for i, label in enumerate(["230 THK. BRICK MASONRY WALL", "115 THK. BRICK PARTITION WALL"]):
            y = ly + i * 22
            sw = pymupdf.Rect(lx, y, lx + 30, y + 12)
            self.page.draw_rect(sw, color=BLACK, width=0.3)
            self.hatch_rect(sw.x0, sw.y0, sw.x1, sw.y1)
            self.note(lx + 40, y + 9, label, size=6.5)
        y = ly + 44
        self.page.draw_rect(pymupdf.Rect(lx, y, lx + 30, y + 12), color=BLACK, fill=BLACK, width=0.3)
        self.note(lx + 40, y + 9, "RCC COLUMN 230 X 300", size=6.5)
        for i, row in enumerate(SCHEDULE):
            self.note(1065, 520 + i * 10, row)
        for i, n in enumerate(notes):
            self.note(900, 625 + i * 10, n)
        self.page.draw_rect(pymupdf.Rect(20, 20, 1170.55, 821.89), color=BLACK, width=0.8)
        self.page.draw_rect(pymupdf.Rect(890, 745, 1160, 812), color=BLACK, width=0.5)
        self.note(900, 765, "PROPOSED G+1 RESIDENCE", size=11, font="hebo")
        self.note(900, 782, title, size=9)
        self.note(900, 800, f"SCALE 1:100 (A3)        DRG. NO. {drawing_no}        REV. 0", size=7)

    def save(self, path):
        self.doc.save(path)
        d = pymupdf.open(path)
        d[0].get_pixmap(dpi=90).save(path.with_suffix(".png"))
        print("saved", path, path.stat().st_size, "bytes", "paths", len(d[0].get_drawings()))


def outer_walls(s, front, back, left, right):
    s.wall(X["A"], Y["1"], X["D"], Y["1"], EXT, openings=back)
    s.wall(X["A"], Y["3"], X["D"], Y["3"], EXT, openings=front)
    s.wall(X["A"], Y["1"], X["A"], Y["3"], EXT, openings=left)
    s.wall(X["D"], Y["1"], X["D"], Y["3"], EXT, openings=right)


def ground_floor():
    s = Sheet()
    s.grid_and_columns()
    outer_walls(s,
                front=[(900, 2700), (5400, 6600), (9600, 11100)],    # W1, main door D, W2
                back=[(1200, 3000), (5200, 6800), (9600, 11100)],    # W1, stair W3, kitchen W2
                left=[(1500, 3000), (6000, 7800)],                   # W2, W1
                right=[(1500, 3000), (5200, 5800), (7400, 8150)])    # W2, toilet V, utility door D2
    s.wall(X["A"], Y["2"], X["D"], Y["2"], EXT, openings=[(2800, 3700), (4800, 7200), (8400, 9300)])
    s.wall(X["B"], Y["1"], X["B"], Y["2"], EXT)
    s.wall(X["C"], Y["1"], X["C"], Y["2"], EXT)
    s.wall(X["C"], Y["2"], X["C"], Y["3"], EXT, openings=[(5200, 5950), (7400, 8150)])
    s.wall(X["C"], 6600, X["D"], 6600, INT)
    for args in [((900, 9000, 2700, 9000)), ((9600, 9000, 11100, 9000)), ((1200, 0, 3000, 0)), ((5200, 0, 6800, 0)),
                 ((9600, 0, 11100, 0)), ((0, 1500, 0, 3000)), ((0, 6000, 0, 7800)), ((12000, 1500, 12000, 3000)),
                 ((12000, 5200, 12000, 5800))]:
        s.window(*args)
    s.door(5400, 9000, 1200, (1, 0), (0, -1))          # main door
    s.door(2800, 4500, 900, (1, 0), (0, -1))           # bedroom 1
    s.door(8400, 4500, 900, (1, 0), (0, -1))           # kitchen
    s.door(7800, 5200, 750, (0, 1), (1, 0))            # toilet
    s.door(7800, 7400, 750, (0, 1), (1, 0))            # utility
    s.door(12000, 7400, 750, (0, 1), (-1, 0))          # utility to outside
    s.stair("UP")
    s.dims()
    for x, y, name, size in [(700, 2000, "BEDROOM 1", "4200 X 4500"), (4500, 1300, "STAIRCASE", ""),
                             (8600, 2000, "KITCHEN", "4200 X 4500"), (1500, 6400, "LIVING / DINING", "7800 X 4500"),
                             (8600, 5400, "TOILET", "4200 X 2100"), (8600, 7600, "UTILITY", "4200 X 2400")]:
        s.text(x, y, name, size=6.5)
        if size:
            s.text(x, y + 450, size, size=5)
    s.text(1500, 7800, "FFL +0.450", size=5)
    s.text(5200, 10000, "ENTRANCE", size=6); s.text(5200, 10450, "PLINTH LVL SSL +0.300", size=5)
    for x, y, t in [(1600, 9500, "W1"), (5900, 9500, "D"), (10200, 9500, "W2"), (1900, -500, "W1"), (5800, -500, "W3"),
                    (10200, -500, "W2"), (-900, 2200, "W2"), (-900, 6800, "W1"), (12300, 2200, "W2"),
                    (12300, 5500, "V"), (12300, 7800, "D2"), (3000, 4100, "D1"), (8600, 4100, "D1"),
                    (7300, 5500, "D2"), (7300, 7700, "D2")]:
        s.text(x, y, t, size=5)
    s.legend_schedule_title("GROUND FLOOR PLAN", "A-101", [
        "NOTES:",
        "1. ALL DIMENSIONS ARE IN MILLIMETRES UNLESS NOTED OTHERWISE.",
        "2. ALL LEVELS ARE IN METRES.",
        "3. GROUND LEVEL: NGL +0.000",
        "4. PLINTH SLAB: SSL +0.300",
        "5. FIRST FLOOR SLAB: SSL +3.300",
        "6. ALL RCC SLABS 150 THK.",
        "7. DO NOT SCALE FROM THIS DRAWING.",
    ])
    s.save(HERE / "G1_HOUSE_GROUND_FLOOR_PLAN.pdf")


def first_floor():
    s = Sheet()
    s.grid_and_columns()
    outer_walls(s,
                front=[(900, 2700), (4800, 7200), (9600, 11100)],    # W1, W4, W2
                back=[(1200, 3000), (5200, 6800), (9600, 11100)],    # W1, stair W3, W2
                left=[(1500, 3000), (6000, 7800)],                   # W2, W1
                right=[(1500, 3000), (5200, 5800), (7200, 8700)])    # W2, toilet V, dress W2
    s.wall(X["A"], Y["2"], X["D"], Y["2"], EXT, openings=[(2800, 3700), (4800, 7200), (8400, 9300)])
    s.wall(X["B"], Y["1"], X["B"], Y["2"], EXT)
    s.wall(X["C"], Y["1"], X["C"], Y["2"], EXT)
    s.wall(X["B"], Y["2"], X["B"], Y["3"], EXT, openings=[(5000, 5900)])
    s.wall(X["C"], Y["2"], X["C"], Y["3"], EXT, openings=[(5200, 5950), (7400, 8300)])
    s.wall(X["C"], 6600, X["D"], 6600, INT)
    for args in [((900, 9000, 2700, 9000)), ((4800, 9000, 7200, 9000)), ((9600, 9000, 11100, 9000)), ((1200, 0, 3000, 0)),
                 ((5200, 0, 6800, 0)), ((9600, 0, 11100, 0)), ((0, 1500, 0, 3000)), ((0, 6000, 0, 7800)),
                 ((12000, 1500, 12000, 3000)), ((12000, 5200, 12000, 5800)), ((12000, 7200, 12000, 8700))]:
        s.window(*args)
    s.door(2800, 4500, 900, (1, 0), (0, -1))           # bedroom 2
    s.door(8400, 4500, 900, (1, 0), (0, -1))           # bedroom 3
    s.door(4200, 5000, 900, (0, 1), (1, 0))            # master <-> lounge
    s.door(7800, 5200, 750, (0, 1), (1, 0))            # toilet
    s.door(7800, 7400, 900, (0, 1), (1, 0))            # dress
    s.stair("DN")
    s.dims()
    for x, y, name, size in [(700, 2000, "BEDROOM 2", "4200 X 4500"), (4500, 1300, "STAIRCASE", ""),
                             (8600, 2000, "BEDROOM 3", "4200 X 4500"), (600, 6400, "MASTER BEDROOM", "4200 X 4500"),
                             (4600, 6400, "FAMILY LOUNGE", "3600 X 4500"), (8600, 5400, "TOILET", "4200 X 2100"),
                             (8600, 7600, "DRESS", "4200 X 2400")]:
        s.text(x, y, name, size=6.5)
        if size:
            s.text(x, y + 450, size, size=5)
    s.text(600, 7800, "FIRST FLOOR LVL SSL +3.300", size=5)
    for x, y, t in [(1600, 9500, "W1"), (5900, 9500, "W4"), (10200, 9500, "W2"), (1900, -500, "W1"), (5800, -500, "W3"),
                    (10200, -500, "W2"), (-900, 2200, "W2"), (-900, 6800, "W1"), (12300, 2200, "W2"),
                    (12300, 5500, "V"), (12300, 7900, "W2"), (3000, 4100, "D1"), (8600, 4100, "D1"),
                    (3800, 5400, "D1"), (7300, 5500, "D2"), (7300, 7800, "D1")]:
        s.text(x, y, t, size=5)
    s.legend_schedule_title("FIRST FLOOR PLAN", "A-102", [
        "NOTES:",
        "1. ALL DIMENSIONS ARE IN MILLIMETRES UNLESS NOTED OTHERWISE.",
        "2. ALL LEVELS ARE IN METRES.",
        "3. FIRST FLOOR SLAB: SSL +3.300",
        "4. ROOF EAVES / CEILING SLAB: SSL +6.300",
        "5. ROOF: GABLE, 30 DEG PITCH, RIDGE PARALLEL TO GRID 1.",
        "6. EAVES AND VERGE OVERHANG 450 MM.",
        "7. ROOF SHEET: RED STANDING SEAM METAL.",
        "8. PVC GUTTERS AND DOWNPIPES AT EAVES.",
        "9. ALL RCC SLABS 150 THK.",
    ])
    s.save(HERE / "G1_HOUSE_FIRST_FLOOR_PLAN.pdf")


if __name__ == "__main__":
    ground_floor()
    first_floor()
