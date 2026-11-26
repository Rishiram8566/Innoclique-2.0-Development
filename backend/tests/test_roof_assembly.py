"""LOD 500 envelope: exterior walls, the roof slab and parapets from printed notes only."""

from app.services import building_model
from app.services import roof_assembly as ra

T = 230.0
STOREY = {"placement_id": "s1", "drawing_id": "plan", "drawing_title": "GROUND FLOOR PLAN",
          "base_level_key": "SSL@+300mm", "base_elevation_mm": 300.0,
          "top_level_key": "SSL@+3300mm", "storey_height_mm": 3000.0}


def box(element_id, rect, element_type="wall", component="230 thk. brick masonry wall", z0=300.0, z1=3300.0):
    return {"element_id": element_id, "element_type": element_type, "component": component,
            "detected_by": "legend_hatch", "drawing_id": "plan",
            "storey": {"placement_id": "s1", "base_level_key": "SSL@+300mm", "base_elevation_mm": 300.0},
            "mesh": {"vertices": building_model._box_vertices(rect, z0, z1), "faces": ra.BOX_FACES}}


def house():
    """A 10 x 8 m room ring with a window gap in the south wall, one partition and a corner column."""
    return [
        box("north", (0, 0, 10000, T)),
        box("west", (0, 0, T, 8000)),
        box("east", (10000 - T, 0, 10000, 8000)),
        box("south-a", (0, 8000 - T, 4000, 8000)),
        box("window", (4000, 8000 - T, 5500, 8000), element_type="window", component="window"),
        box("south-b", (5500, 8000 - T, 10000, 8000)),
        box("partition", (5000, T, 5115, 8000 - T), component="115 thk. brick partition wall"),
        box("corner", (0, 0, T, T), element_type="column", component="column"),
        box("free", (2500, 4000, 2800, 4300), element_type="column", component="column"),
    ]


def note(height=1000.0, text="6. PARAPET 1.0M HIGH ON ROOF.", drawing_id="plan"):
    return {"height_mm": height, "text": text, "drawing_id": drawing_id, "drawing_title": "GROUND FLOOR PLAN",
            "candidate_id": "c1", "evidence_id": "e1"}


def test_outer_walls_columns_and_openings_face_outside():
    elements = house()
    ra.mark_exterior([STOREY], elements)
    external = {e["element_id"]: e["is_external"] for e in elements}
    assert external == {"north": True, "west": True, "east": True, "south-a": True, "window": True,
                        "south-b": True, "partition": False, "corner": True, "free": False}
    corner = next(e for e in elements if e["element_id"] == "corner")
    free = next(e for e in elements if e["element_id"] == "free")
    assert corner["host_component"] == "230 thk. brick masonry wall"       # finished like the wall it sits in
    assert free["host_component"] is None                                  # free-standing: bare concrete


def test_parapet_height_comes_only_from_a_printed_note():
    chosen, reason = ra.choose_parapet([note()], STOREY, {"plan"})
    assert chosen["height_mm"] == 1000.0 and "PARAPET 1.0M HIGH ON ROOF" in reason

    none, reason = ra.choose_parapet([], STOREY, {"plan"})
    assert none is None and "no parapet is built" in reason

    clash, reason = ra.choose_parapet([note(), note(1200.0, "PARAPET 1.2M HIGH")], STOREY, {"plan"})
    assert clash is None and "different parapet heights" in reason

    # On another placed sheet it counts only when it names the roof.
    other = note(drawing_id="section")
    assert ra.choose_parapet([other], STOREY, {"plan", "section"})[0] is not None
    assert ra.choose_parapet([{**other, "text": "PARAPET 1.0M HIGH"}], STOREY, {"plan", "section"})[0] is None
    assert ra.choose_parapet([note(50.0, "PARAPET 50MM HIGH")], STOREY, {"plan"})[0] is None


def test_parapet_notes_are_read_from_plain_text():
    found = ra.parapet_notes_from_texts(["NOTES:", "PARAPET 1.0M HIGH ON ROOF.", "SILL 900"],
                                        drawing_id="m1", drawing_title="plan.dxf")
    assert [(n["height_mm"], n["text"]) for n in found] == [(1000.0, "PARAPET 1.0M HIGH ON ROOF.")]


def test_parapet_sits_on_the_outer_wall_line_from_the_roof_level():
    elements = house()
    slabs = building_model.approximate_slabs([STOREY], elements)
    envelope = ra.finish_envelope([STOREY], elements, slabs, [note()], {"plan"})
    assert [s["role"] for s in slabs] == ["floor", "roof"]
    assert envelope["roof"] == slabs[1]["element_id"] and envelope["parapets"] == 4

    parapets = [e for e in elements if e["element_type"] == "parapet"]
    for p in parapets:
        zs = {v[2] for v in p["mesh"]["vertices"]}
        assert zs == {3300.0, 4300.0}                                       # roof level up by the note's 1.0 m
        assert p["lod"]["minimum_lod"] == 500 and p["is_external"]
        assert p["source_note"] == "6. PARAPET 1.0M HIGH ON ROOF."
        assert p["host_component"] == "230 thk. brick masonry wall"
    south = next(p for p in parapets if 8000 - T - 1 <= building_model._rect(p)[1] <= 8000 - T + 1)
    assert building_model._rect(south) == (0.0, 8000 - T, 10000.0, 8000.0)  # one run across the window
    assert set(south["provenance"]["source_element_ids"]) >= {"south-a", "window", "south-b"}
    sources = {i for p in parapets for i in p["provenance"]["source_element_ids"]}
    assert "free" not in sources and "partition" not in sources


def test_no_note_means_no_parapet_but_still_a_roof():
    elements = house()
    slabs = building_model.approximate_slabs([STOREY], elements)
    envelope = ra.finish_envelope([STOREY], elements, slabs, [], {"plan"})
    assert envelope["parapets"] == 0 and envelope["roof"] is not None
    assert not any(e["element_type"] == "parapet" for e in elements)


def test_roof_ends_at_the_outer_wall_faces_not_a_raster_cell_beyond():
    elements = house()
    footprint = building_model.approximate_slabs([STOREY], elements)[1]["footprint"]
    xs = [v for r in footprint["rects_mm"] for v in (r[0], r[2])]
    ys = [v for r in footprint["rects_mm"] for v in (r[1], r[3])]
    assert (min(xs), min(ys), max(xs), max(ys)) == (0.0, 0.0, 10000.0, 8000.0)
