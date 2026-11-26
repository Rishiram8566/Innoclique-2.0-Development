"""Gable roof, gutters, plinth and slab thickness from the drawings' notes (LOD 500)."""

import math
from types import SimpleNamespace

from app.services import building_model
from app.services import envelope_details as ed
from app.services import opening_detection as od
from app.services import roof_assembly as ra

T = 230.0
NOTES = [
    "3. GROUND LEVEL: NGL +0.000",
    "4. ROOF EAVES / CEILING SLAB: SSL +6.300",
    "5. ROOF: GABLE, 30 DEG PITCH, RIDGE PARALLEL TO GRID 1.",
    "6. EAVES AND VERGE OVERHANG 450 MM.",
    "7. ROOF SHEET: RED STANDING SEAM METAL.",
    "8. PVC GUTTERS AND DOWNPIPES AT EAVES.",
    "9. ALL RCC SLABS 150 THK.",
]


def storey(pid, base, top_key, drawing):
    return {"placement_id": pid, "drawing_id": drawing, "drawing_title": drawing, "base_level_key": f"SSL@+{base:.0f}mm",
            "base_elevation_mm": base, "top_level_key": top_key, "storey_height_mm": 3000.0}


STOREYS = [storey("g", 300.0, "SSL@+3300mm", "ground"), storey("f", 3300.0, "SSL@+6300mm", "first")]


def box(element_id, rect, pid, base, element_type="wall", component="230 thk. brick masonry wall"):
    return {"element_id": f"{pid}:{element_id}", "element_type": element_type, "component": component,
            "detected_by": "legend_hatch", "drawing_id": pid,
            "storey": {"placement_id": pid, "base_level_key": f"SSL@+{base:.0f}mm", "base_elevation_mm": base},
            "mesh": {"vertices": building_model._box_vertices(rect, base, base + 3000.0), "faces": ed.BOX_FACES}}


def two_storey_house():
    elements = []
    for pid, base in (("g", 300.0), ("f", 3300.0)):
        elements += [
            box("north", (0, 0, 12000, T), pid, base), box("south", (0, 9000 - T, 12000, 9000), pid, base),
            box("west", (0, 0, T, 9000), pid, base), box("east", (12000 - T, 0, 12000, 9000), pid, base),
            box("mid", (0, 4500, 12000, 4500 + T), pid, base),
        ]
    return elements


def build(notes=NOTES, axes=None):
    elements = two_storey_house()
    slabs = building_model.approximate_slabs(STOREYS, elements)
    envelope = ra.finish_envelope(STOREYS, elements, slabs, [], {"ground", "first"},
                                  facts=ed.envelope_facts(notes), axes=axes if axes is not None else {"1": "x", "A": "y"})
    return elements, slabs, envelope


def of_type(elements, element_type):
    return [e for e in elements if e["element_type"] == element_type]


def test_roof_and_envelope_notes_are_read():
    facts = ed.envelope_facts(NOTES)
    roof = facts["roof"]
    assert (roof["form"], roof["pitch_deg"], roof["ridge_grid"], roof["overhang_mm"]) == ("gable", 30.0, "1", 450.0)
    assert (roof["sheet"], roof["colour"]) == ("standing seam metal", "red")
    assert facts["gutters"] and facts["downpipes"]
    assert facts["slab_thickness_mm"]["value_mm"] == 150.0 and facts["ground_level_mm"]["value_mm"] == 0.0
    ratio = ed.envelope_facts(["ROOF: GABLE, PITCH 1:2, RIDGE ALONG GRID B"])["roof"]
    assert ratio["pitch_deg"] == round(math.degrees(math.atan(0.5)), 2) and ratio["ridge_grid"] == "B"
    assert ed.envelope_facts(["NOTES:", "ALL DIMENSIONS IN MM"])["roof"] is None


def test_grid_direction_comes_from_the_bubbles():
    axes = ed.grid_axes({"1": [(100, 300), (900, 302)], "A": [(150, 120), (151, 700)], "D": [(500, 500)]})
    assert axes == {"1": "x", "A": "y"}


def test_gable_roof_is_built_from_the_note_at_the_eaves_level():
    elements, slabs, envelope = build()
    assert envelope["roof_form"] == "gable" and envelope["parapets"] == 0
    roofs = of_type(elements, "roof")
    assert len(roofs) == 2 and all(r["lod"]["minimum_lod"] == 500 for r in roofs)
    slope = math.tan(math.radians(30))
    ridge_z = 6300 + 4500 * slope                              # half of the 9 m span, from the outer faces
    eave_z = 6300 - 450 * slope                                # the overhang drops below the eaves level
    for roof in roofs:
        zs = sorted({round(v[2], 1) for v in roof["mesh"]["vertices"][4:]})
        assert zs == [round(eave_z, 1), round(ridge_z, 1)]
        xs = {v[0] for v in roof["mesh"]["vertices"]}
        assert min(xs) == -450 and max(xs) == 12450           # verge overhang at both gable ends
        assert roof["roof"]["ridge_axis"] == "x" and roof["color"] == "#8f2f2b"
        assert "RIDGE PARALLEL TO GRID 1" in roof["source_note"]
    gables = [e for e in elements if e.get("wall_type") == "gable"]
    assert len(gables) == 2 and {g["component"] for g in gables} == {"230 thk. brick masonry wall"}
    assert max(v[2] for g in gables for v in g["mesh"]["vertices"]) < ridge_z   # tucked under the roof sheet
    assert len(of_type(elements, "gutter")) == 2 and len(of_type(elements, "downpipe")) == 8
    assert len(of_type(elements, "fascia")) == 2 and len(of_type(elements, "ridge")) == 1
    assert [s["role"] for s in slabs] == ["floor", "floor", "ceiling"]          # the top slab is now a ceiling
    for e in of_type(elements, "gutter") + of_type(elements, "fascia"):
        assert e["provenance"]["display_only_sizes"]                            # profiles are display-only


def test_no_gable_without_a_ridge_direction_or_with_another_form():
    elements, slabs, envelope = build(axes={})
    assert envelope["roof_form"] == "flat" and not of_type(elements, "roof")
    assert "grid line" in envelope["roof_note"] and slabs[-1]["role"] == "roof"
    elements, _, envelope = build(notes=["ROOF: HIPPED, 25 DEG PITCH, RIDGE PARALLEL TO GRID 1."])
    assert envelope["roof_form"] == "flat" and "only gable roofs" in envelope["roof_note"]


def test_plinth_runs_round_the_ground_floor_from_ground_level():
    elements, _, envelope = build()
    plinths = of_type(elements, "plinth")
    assert envelope["plinth"] == len(plinths) == 4
    for p in plinths:
        assert {v[2] for v in p["mesh"]["vertices"]} == {0.0, 300.0}
    xs = [v[0] for p in plinths for v in p["mesh"]["vertices"]]
    assert min(xs) < 0 and max(xs) > 12000                    # stands just proud of the outer faces


def test_slab_thickness_comes_from_the_note():
    _, slabs, envelope = build()
    assert envelope["slab_thickness_mm"] == 150.0
    assert all(s["thickness_mm"] == 150.0 and "150 THK" in s["thickness_basis"] for s in slabs)
    _, slabs, _ = build(notes=[n for n in NOTES if "SLABS" not in n])
    assert all(s["thickness_mm"] is None for s in slabs)


def test_glazing_lines_beat_a_wall_face_at_the_jamb():
    # Window gap 0..60 pt in a vertical wall band x 100..106; three glazing lines run along it, and the face of a
    # wall meeting the jamb looks like a door leaf (perpendicular, at the jamb, about half the gap long).
    scan = SimpleNamespace(curves=[], axis=[
        ("v", 101.0, 0.0, 60.0), ("v", 103.0, 0.0, 60.0), ("v", 105.0, 0.0, 60.0),
        ("h", 60.5, 106.0, 136.0)])
    kind, evidence = od.classify(scan, "v", 0.0, 60.0, 100.0, 106.0)
    assert kind == "window" and evidence == ["3 glazing lines"]
