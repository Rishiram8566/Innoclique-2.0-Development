import pytest

from app.services import drawing_text_facts as facts
from app.services import hatch_wall_detection as hw
from app.services import height_verification_ledger as ledger_service
from app.services import opening_detection as od

SCALE = 50.0          # mm per PDF point: a 20 pt gap is a 1000 mm opening
BLACK = [0, 0, 0]


def line(x0, y0, x1, y1):
    return {"page_number": 1, "path_index": 1, "item_index": 0, "type": "line",
            "start": [x0, y0], "end": [x1, y1], "stroke": BLACK, "fill": None}


def arc(x0, y0, x1, y1):
    return {"page_number": 1, "path_index": 2, "item_index": 0, "type": "bezier",
            "points": [[x0, y1], [x0, y0], [x1, y0], [x1, y0]], "stroke": BLACK, "fill": None}


def wall(wall_id, x0, x1, y=100.0, t=5.0):
    return {"id": wall_id, "bbox": [x0, y - t / 2, x1, y + t / 2], "component": "brick wall"}


# A horizontal wall along y = 100 with three gaps: a door (arc + leaf), a window (glazing lines) and a passage.
WALLS = [wall("w1", 0, 40), wall("w2", 60, 100), wall("w3", 130, 170), wall("w4", 188, 230)]
PRIMITIVES = [
    arc(40, 80, 60, 98), line(40, 80, 40, 97.5),                       # door 40..60: swing arc and leaf at a jamb
    line(100, 99, 130, 99), line(100, 101, 130, 101),                  # window 100..130: two glazing lines
]


def scan():
    return hw.scan_primitives(PRIMITIVES, min_line_pt=12.0)


def test_gaps_are_typed_by_the_drawing_symbol():
    found = od.detect_openings(scan(), WALLS, [], millimetres_per_pdf_point=SCALE)
    assert [(o["kind"], o["width_mm"]) for o in found] == [("door", 1000.0), ("window", 1500.0), ("opening", 900.0)]
    assert found[0]["between"] == ["w1", "w2"]
    assert "swing arc" in found[0]["symbol_evidence"]


def test_a_column_in_the_gap_is_not_an_opening_but_a_column_inside_a_wall_run_is_ignored():
    columns = [{"id": "c-in-gap", "bbox": [45, 96, 55, 104]},          # fills the door gap
               {"id": "c-in-wall", "bbox": [135, 96, 145, 104]}]       # stands inside wall w3
    found = od.detect_openings(scan(), WALLS, columns, millimetres_per_pdf_point=SCALE)
    kinds = [o["kind"] for o in found]
    assert "door" not in kinds and kinds.count("window") == 1 and kinds.count("opening") == 1


def test_gaps_outside_the_size_range_are_not_openings():
    walls = [wall("a", 0, 40), wall("b", 45, 80), wall("c", 200, 240)]      # 250 mm and 6000 mm gaps
    assert od.detect_openings(scan(), walls, [], millimetres_per_pdf_point=SCALE) == []


def test_heights_come_from_the_schedule_then_notes_then_assumed():
    entry = {"opening_tags": [("W1", [110, 80, 116, 86]), ("D1", [44, 108, 50, 114])],
             "opening_schedule": {"W1": facts.schedule_row("W1 1500 X 1200 SILL 900"),
                                  "D1": facts.schedule_row("D1 900 X 2100")}}
    window = {"kind": "window", "bbox_pdf_points": [100, 97.5, 130, 102.5]}
    door = {"kind": "door", "bbox_pdf_points": [40, 97.5, 60, 102.5]}
    passage = {"kind": "opening", "bbox_pdf_points": [170, 97.5, 188, 102.5]}
    h = od.opening_heights(window, entry, millimetres_per_pdf_point=SCALE)
    assert (h["tag"], h["sill_mm"], h["head_mm"]) == ("W1", 900.0, 2100.0) and h["height_source"].startswith("schedule")
    d = od.opening_heights(door, entry, millimetres_per_pdf_point=SCALE)
    assert (d["tag"], d["head_mm"]) == ("D1", 2100.0)
    assert od.opening_heights(passage, {"head_notes": [2400.0]}, millimetres_per_pdf_point=SCALE)["head_mm"] == 2400.0
    assumed = od.opening_heights(window, {}, millimetres_per_pdf_point=SCALE)
    assert assumed["height_source"] == "assumed_typical" and (assumed["sill_mm"], assumed["head_mm"]) == (900.0, 2100.0)


def test_a_lone_letter_beside_a_door_is_a_tag_but_paired_grid_bubbles_are_not():
    entry = {"labels": {"D": [(45.0, 10.0), (45.0, 300.0), (50.0, 112.0)]},
             "opening_tags": [("D", [43, 7, 47, 13]), ("D", [48, 109, 52, 115])],
             "opening_schedule": {"D": facts.schedule_row("D 1000 X 2100 MAIN DOOR")}}
    door = {"kind": "door", "bbox_pdf_points": [40, 97.5, 60, 102.5]}
    assert od.opening_heights(door, entry, millimetres_per_pdf_point=SCALE)["tag"] == "D"


def test_opening_element_and_serving_gate():
    opening = od.detect_openings(scan(), WALLS, [], millimetres_per_pdf_point=SCALE)[1]
    heights = {"tag": "W1", "sill_mm": 900.0, "head_mm": 2100.0, "height_source": "schedule row: W1"}
    element = od.build_opening_element(opening, heights, element_id="win-1", millimetres_per_pdf_point=SCALE,
                                       wall_height_mm=3000.0)
    assert element["element_type"] == "window" and element["opening"]["sill_mm"] == 900.0
    assert element["lod"]["minimum_lod"] == 300 and element["mesh"]["closed_manifold"]
    served, withheld = ledger_service.screen_mesh_elements([element], {"verifications": [], "column_confirmations": []})
    assert served == [] and "not served" in withheld[0]["reasons"][0]


@pytest.mark.parametrize("text, name", [
    ("BEDROOM 1", "BEDROOM 1"), ("LIVING / DINING", "LIVING / DINING"), ("MASTER BEDROOM", "MASTER BEDROOM"),
    ("RMU ROOM", "RMU ROOM"), ("KITCHEN", "KITCHEN"), ("GROUND FLOOR PLAN", None), ("3900 X 3900", None),
    ("230 THK. BRICK MASONRY WALL", None), ("FFL +0.450", None), ("NOTES:", None),
])
def test_room_labels(text, name):
    assert facts.room_label(text) == name


def test_schedule_rows_sizes_and_notes():
    assert facts.size_text("5100 X 3300") == (5100.0, 3300.0)
    assert facts.schedule_row("V   600 X 450  SILL 1800")["sill_mm"] == 1800.0
    assert facts.schedule_row("RCC COLUMN 230 X 300") is None
    assert facts.opening_tag("W12") == "W12" and facts.opening_tag("WALL") is None
    assert facts.sill_note("ALL WINDOWS: SILL HEIGHT 900") == 900.0
    assert facts.head_note("LINTEL LEVEL 2100") == 2100.0


def test_a_fixture_curve_near_a_window_is_not_a_door_swing():
    # Window glazing in the 100..130 gap plus a WC-like quarter curve 10 pt inside the room: still a window.
    primitives = PRIMITIVES + [arc(105, 70, 115, 82)]
    found = od.detect_openings(hw.scan_primitives(primitives, min_line_pt=12.0), WALLS, [], millimetres_per_pdf_point=SCALE)
    assert [o["kind"] for o in found] == ["door", "window", "opening"]
