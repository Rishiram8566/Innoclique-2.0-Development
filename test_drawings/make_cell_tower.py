"""
Generate a test cell-tower drawing (A3, 1:200): a 40 m four-legged self-supporting lattice tower
elevation and base plan, with the tower notes and antenna schedule a telecom drawing normally carries.
Output: CELL_TOWER_40M_ELEVATION.pdf next to this script.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "CELL_TOWER_40M_ELEVATION.pdf"
K = 72 / 25.4 / 200            # 1:200, mm -> pt
BLACK, GREY = (0, 0, 0), (0.45, 0.45, 0.45)
H, BASE, TOP, STRAIGHT, PANEL = 40000, 6000, 1500, 30000, 2500

doc = pymupdf.open()
page = doc.new_page(width=1190.55, height=841.89)
OX, OY = 300.0, 760.0          # ground at the tower centre line


def P(x, z):
    return pymupdf.Point(OX + x * K, OY - z * K)


def width(z):
    return TOP if z >= STRAIGHT else BASE + (TOP - BASE) * z / STRAIGHT


def text(x, y, s, size=7, font="helv"):
    page.insert_text(pymupdf.Point(x, y), s, fontsize=size, fontname=font)


# Elevation: legs, horizontals and X-bracing of one face.
levels = list(range(0, H + 1, PANEL))
for z0, z1 in zip(levels, levels[1:]):
    for side in (-1, 1):
        page.draw_line(P(side * width(z0) / 2, z0), P(side * width(z1) / 2, z1), color=BLACK, width=0.6)
    page.draw_line(P(-width(z1) / 2, z1), P(width(z1) / 2, z1), color=BLACK, width=0.3)
    page.draw_line(P(-width(z0) / 2, z0), P(width(z1) / 2, z1), color=GREY, width=0.2)
    page.draw_line(P(width(z0) / 2, z0), P(-width(z1) / 2, z1), color=GREY, width=0.2)
page.draw_line(P(-6000, 0), P(6000, 0), color=BLACK, width=0.8)
text(OX + 6000 * K + 6, OY + 3, "NGL +0.000")
for z, label in ((38000, "ANTENNA PLATFORM +38.0 M"), (20000, "REST PLATFORM +20.0 M"), (H, "TOP OF TOWER +40.0 M")):
    page.draw_line(P(-width(z) / 2 - 900, z), P(width(z) / 2 + 900, z), color=BLACK, width=0.5)
    text(OX + 3000 * K + 4, OY - z * K + 2, label, 6)

# Base plan (square, legs at the corners).
bx, by = 620, 600
s = BASE * K * 0.9
page.draw_rect(pymupdf.Rect(bx, by, bx + s, by + s), color=BLACK, width=0.6)
for cx, cy in ((bx, by), (bx + s, by), (bx, by + s), (bx + s, by + s)):
    page.draw_rect(pymupdf.Rect(cx - 7, cy - 7, cx + 7, cy + 7), color=BLACK, width=0.4)
text(bx, by - 8, "TOWER BASE PLAN", 7)

notes = [
    "TOWER NOTES:",
    "1. TOWER TYPE: 4-LEGGED SELF SUPPORTING LATTICE TOWER (ANGLE SECTIONS).",
    "2. TOWER HEIGHT: 40.0 M ABOVE NGL.",
    "3. BASE WIDTH (LEG SPACING): 6.0 M",
    "4. TOP WIDTH: 1.5 M, STRAIGHT PORTION FROM +30.0 M",
    "5. X-BRACING, PANEL HEIGHT 2.5 M",
    "6. MAIN LEGS: L 150 X 150 X 12, BRACINGS: L 75 X 75 X 6",
    "7. ANTENNA PLATFORM AT +38.0 M, REST PLATFORM AT +20.0 M",
    "8. LADDER WITH SAFETY CAGE ON ONE FACE, CABLE LADDER ON OPPOSITE FACE.",
    "9. LIGHTNING ARRESTOR 1.5 M HIGH AT TOP.",
    "10. AVIATION OBSTRUCTION LIGHT AT TOP.",
    "11. PAINTING: ALTERNATE RED AND WHITE BANDS (AVIATION MARKING).",
    "12. FOUNDATION: ISOLATED PAD 3.0 X 3.0 M, DEPTH 3.0 M BELOW NGL.",
]
for i, n in enumerate(notes):
    text(780, 120 + i * 12, n, 7)
schedule = [
    "ANTENNA SCHEDULE:",
    "GSM PANEL ANTENNA 2.6 X 0.3 M - 3 NOS AT +38.0 M",
    "LTE PANEL ANTENNA 1.4 X 0.3 M - 3 NOS AT +36.0 M",
    "MW DISH 0.6 M DIA - 2 NOS AT +34.0 M",
    "RRU 0.5 X 0.3 M - 6 NOS AT +37.0 M",
]
for i, n in enumerate(schedule):
    text(780, 300 + i * 12, n, 7)
page.draw_rect(pymupdf.Rect(20, 20, 1170.55, 821.89), color=BLACK, width=0.8)
page.draw_rect(pymupdf.Rect(890, 745, 1160, 812), color=BLACK, width=0.5)
text(900, 765, "40 M SELF SUPPORTING TOWER", 11, "hebo")
text(900, 782, "ELEVATION AND BASE PLAN", 9)
text(900, 800, "SCALE 1:200 (A3)        DRG. NO. T-101        REV. 0", 7)
doc.save(OUT)
print("saved", OUT, OUT.stat().st_size, "bytes")
