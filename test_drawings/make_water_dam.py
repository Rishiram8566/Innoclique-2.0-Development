"""
Generate a test water-dam drawing (A3): plan of a 300 m concrete gravity dam with a gated ogee spillway,
the typical non-overflow and spillway cross-sections, and the notes and foundation-level table that a
dam general arrangement drawing carries. Output: WATER_DAM_GA.pdf next to this script.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "WATER_DAM_GA.pdf"
BLACK, GREY, BLUE = (0, 0, 0), (0.45, 0.45, 0.45), (0.2, 0.4, 0.7)
doc = pymupdf.open()
page = doc.new_page(width=1190.55, height=841.89)


def text(x, y, s, size=7, font="helv", color=BLACK):
    page.insert_text(pymupdf.Point(x, y), s, fontsize=size, fontname=font, color=color)


# Plan (1:2000): dam axis 300 m, spillway CH 115-187, reservoir upstream.
K = 72 / 25.4 / 2000
PX, PY = 80, 260
page.draw_rect(pymupdf.Rect(PX, PY, PX + 300000 * K, PY + 8000 * K * 3), color=BLACK, width=0.7)
for ch in range(0, 300001, 15000):
    page.draw_line(pymupdf.Point(PX + ch * K, PY), pymupdf.Point(PX + ch * K, PY + 24000 * K), color=GREY, width=0.2)
page.draw_rect(pymupdf.Rect(PX + 115000 * K, PY, PX + 187000 * K, PY + 60000 * K), color=BLACK, width=0.5)
text(PX + 120000 * K, PY + 40000 * K, "SPILLWAY", 6)
page.draw_rect(pymupdf.Rect(PX, PY - 70000 * K, PX + 300000 * K, PY - 2), color=BLUE, width=0.3)
text(PX + 100000 * K, PY - 30000 * K, "RESERVOIR (FRL +317.000)", 7, color=BLUE)
text(PX, PY - 80000 * K, "PLAN - SCALE 1:2000", 8, "hebo")
text(PX, PY + 70000 * K, "CH. 0.0", 6)
text(PX + 290000 * K, PY + 70000 * K, "CH. 300.0", 6)

# Typical sections (1:1000).
S = 72 / 25.4 / 1000
SX, SY = 120, 700


def sec(points, ox):
    pts = [pymupdf.Point(ox + y * S, SY - (z - 262000) * S) for y, z in points]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        page.draw_line(a, b, color=BLACK, width=0.6)


sec([(0, 262000), (0, 320000), (8000, 320000), (8000, 309333), (43500, 262000)], SX)
text(SX, SY + 18, "TYPICAL NON-OVERFLOW SECTION - SCALE 1:1000", 7, "hebo")
sec([(0, 262000), (0, 309000), (2500, 309000), (6000, 306500), (30000, 274000), (39000, 262000)], SX + 260)
text(SX + 260, SY + 18, "SPILLWAY SECTION - SCALE 1:1000", 7, "hebo")

notes = [
    "GENERAL NOTES:",
    "1. TYPE: CONCRETE GRAVITY DAM WITH GATED OGEE SPILLWAY.",
    "2. ALL LEVELS ARE IN METRES ABOVE MEAN SEA LEVEL.",
    "3. TOP OF DAM EL. +320.000 M",
    "4. DEEPEST FOUNDATION LEVEL EL. +262.000 M",
    "5. LENGTH OF DAM AT TOP: 300.0 M",
    "6. TOP WIDTH: 8.0 M",
    "7. UPSTREAM FACE: VERTICAL",
    "8. DOWNSTREAM SLOPE: 0.75H : 1V",
    "9. FRL +317.000 M, MWL +318.500 M, TWL +268.000 M",
    "10. SPILLWAY: OGEE, CREST EL. +309.000 M, 5 BAYS OF 12.0 M, PIERS 3.0 M THICK",
    "11. RADIAL GATES 12.0 X 8.5 M",
    "12. SPILLWAY FROM CH. 115.0 M TO CH. 187.0 M",
    "13. BLOCK LENGTH 15.0 M (CONTRACTION JOINTS)",
    "14. INSPECTION GALLERY 2.0 X 2.5 M AT EL. +270.000 M",
    "15. STILLING BASIN 55.0 M LONG, FLOOR EL. +260.000 M",
    "16. CREST ROAD 6.0 M WIDE WITH PARAPET 1.0 M HIGH",
]
for i, n in enumerate(notes):
    text(760, 90 + i * 12, n)
table = ["FOUNDATION LEVELS ALONG DAM AXIS:", "CH. 0.0 M - EL. +320.0", "CH. 30.0 M - EL. +300.0",
         "CH. 60.0 M - EL. +282.0", "CH. 100.0 M - EL. +266.0", "CH. 150.0 M - EL. +262.0",
         "CH. 200.0 M - EL. +265.0", "CH. 240.0 M - EL. +280.0", "CH. 270.0 M - EL. +298.0", "CH. 300.0 M - EL. +320.0"]
for i, n in enumerate(table):
    text(760, 330 + i * 12, n)
page.draw_rect(pymupdf.Rect(20, 20, 1170.55, 821.89), color=BLACK, width=0.8)
page.draw_rect(pymupdf.Rect(890, 745, 1160, 812), color=BLACK, width=0.5)
text(900, 765, "CONCRETE GRAVITY DAM", 11, "hebo")
text(900, 782, "GENERAL ARRANGEMENT", 9)
text(900, 800, "SCALES AS SHOWN (A3)        DRG. NO. D-101        REV. 0", 7)
doc.save(OUT)
print("saved", OUT, OUT.stat().st_size, "bytes")
