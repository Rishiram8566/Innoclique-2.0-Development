"""
Generate a test thermal power plant layout plan (A3, 1:2000): plant boundary, turbine and boiler houses,
ESP, chimney, two natural-draught cooling towers, switchyard, generator transformers, coal yard, tanks
and service buildings, each drawn and labelled, with the structure schedule a plot plan carries.
Output: THERMAL_POWER_PLANT_LAYOUT.pdf next to this script.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "THERMAL_POWER_PLANT_LAYOUT.pdf"
K = 72 / 25.4 / 2000            # plan metres -> pt is K * 1000
OX, OY = 60.0, 90.0
BLACK, GREY = (0, 0, 0), (0.45, 0.45, 0.45)
doc = pymupdf.open()
page = doc.new_page(width=1190.55, height=841.89)


def P(x, y):
    return pymupdf.Point(OX + x * 1000 * K, OY + y * 1000 * K)


def text(x, y, s, size=6.0, font="helv"):
    page.insert_text(pymupdf.Point(x, y), s, fontsize=size, fontname=font)


def rect(x0, y0, w, h, label, size=6.0):
    page.draw_rect(pymupdf.Rect(P(x0, y0), P(x0 + w, y0 + h)), color=BLACK, width=0.5)
    c = P(x0 + w / 2, y0 + h / 2)
    text(c.x - len(label) * size * 0.27, c.y + size * 0.35, label, size)


def circle(cx, cy, dia, label, size=6.0):
    c = P(cx, cy)
    page.draw_circle(c, dia / 2 * 1000 * K, color=BLACK, width=0.5)
    text(c.x - len(label) * size * 0.27, c.y + size * 0.35, label, size)


page.draw_rect(pymupdf.Rect(P(0, 0), P(600, 400)), color=GREY, width=0.8, dashes="[6 3] 0")
text(P(5, 0).x, P(5, 0).y - 4, "PLANT BOUNDARY", 7)
rect(200, 120, 90, 40, "TG BUILDING")
rect(200, 160, 90, 45, "BOILER HOUSE")
rect(210, 215, 70, 35, "ESP")
circle(245, 280, 18, "CHIMNEY", 4.5)
circle(420, 120, 90, "COOLING TOWER-1")
circle(520, 120, 90, "COOLING TOWER-2")
rect(40, 40, 120, 80, "SWITCHYARD")
for k, y in enumerate((122, 134, 146)):
    rect(165, y, 12, 8, f"GENERATOR TRANSFORMER-{k + 1}", 2.2)
rect(40, 300, 40, 20, "ADMIN BUILDING", 5)
rect(330, 250, 150, 80, "COAL YARD")
rect(380, 180, 50, 40, "WATER TREATMENT PLANT", 4.5)
circle(120, 220, 20, "FUEL OIL TANK-1", 3)
circle(150, 220, 20, "FUEL OIL TANK-2", 3)
rect(470, 170, 30, 20, "CW PUMP HOUSE", 4)
rect(100, 300, 40, 25, "WORKSHOP", 5)

schedule = [
    "STRUCTURE SCHEDULE:",
    "TG BUILDING - 90 X 40 M, HT 35 M",
    "BOILER HOUSE - 90 X 45 M, HT 65 M",
    "ESP - 70 X 35 M, HT 30 M",
    "CHIMNEY - DIA 18 M AT BASE, 10 M AT TOP, HT 150 M",
    "COOLING TOWER - 2 NOS, DIA 90 M, HT 110 M",
    "SWITCHYARD - 120 X 80 M",
    "GENERATOR TRANSFORMER - 3 NOS, 12 X 8 M, HT 9 M",
    "ADMIN BUILDING - 40 X 20 M, HT 12 M",
    "COAL YARD - 150 X 80 M, STOCKPILE HT 12 M",
    "WATER TREATMENT PLANT - 50 X 40 M, HT 8 M",
    "FUEL OIL TANK - 2 NOS, DIA 20 M, HT 15 M",
    "CW PUMP HOUSE - 30 X 20 M, HT 10 M",
    "WORKSHOP - 40 X 25 M, HT 10 M",
]
for i, row in enumerate(schedule):
    text(830, 110 + i * 12, row, 7)
text(830, 300, "NOTES:", 7)
text(830, 312, "1. ALL DIMENSIONS IN METRES. FGL +100.000 M", 7)
text(830, 324, "2. 2 X 250 MW COAL FIRED THERMAL POWER STATION.", 7)
page.draw_rect(pymupdf.Rect(20, 20, 1170.55, 821.89), color=BLACK, width=0.8)
page.draw_rect(pymupdf.Rect(890, 745, 1160, 812), color=BLACK, width=0.5)
text(900, 765, "THERMAL POWER STATION", 11, "hebo")
text(900, 782, "OVERALL PLOT PLAN", 9)
text(900, 800, "SCALE 1:2000 (A3)        DRG. NO. P-001        REV. 0", 7)
doc.save(OUT)
print("saved", OUT, OUT.stat().st_size, "bytes")
