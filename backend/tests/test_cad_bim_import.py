import asyncio
import io
import json

import ezdxf
import numpy as np
import pytest
import ifcopenshell.api as api
from fastapi import UploadFile

from app import main as main_module
from app.services import imported_models
from app.services.cad_import import import_dxf
from app.services.ifc_import import import_ifc


# ------------------------------------------------------------ DXF

def make_dxf(path, *, levels=True):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    door = doc.blocks.new("DOOR_900")
    door.add_line((0, 0), (0, 900)); door.add_arc((0, 0), 900, 0, 90)
    win = doc.blocks.new("WIN_1500")
    for y in (-60, 0, 60):
        win.add_line((0, y), (1500, y))
    # One 230 mm wall along y = 0 from 0 to 8000 with a door gap 1000..1900 and a window gap 4000..5500.
    for x0, x1 in [(0, 1000), (1900, 4000), (5500, 8000)]:
        msp.add_lwpolyline([(x0, -115), (x1, -115), (x1, 115), (x0, 115)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_blockref("DOOR_900", (1000, 115), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref("WIN_1500", (4000, 0), dxfattribs={"layer": "A-GLAZ"})
    msp.add_lwpolyline([(-150, -150), (150, -150), (150, 150), (-150, 150)], close=True, dxfattribs={"layer": "S-COLS"})
    msp.add_text("LIVING ROOM", height=200).set_placement((2000, -2000))
    msp.add_text("4000 X 3500", height=150).set_placement((2000, -2400))
    if levels:
        for text in ["ALL LEVELS ARE IN METRES.", "FLOOR SLAB: SSL +0.000", "ROOF SLAB: SSL +3.200",
                     "W1 1500 X 1200 SILL 900"]:
            msp.add_text(text, height=150).set_placement((10000, 0))
        msp.add_text("W1", height=150).set_placement((4600, 600))
    doc.saveas(path)


def test_dxf_walls_doors_windows_levels_and_rooms(tmp_path):
    path = tmp_path / "plan.dxf"
    make_dxf(path)
    model = import_dxf(path, model_id="m", title="plan.dxf")
    kinds = sorted(e["element_type"] for e in model["elements"])
    assert kinds == ["column", "door", "wall", "wall", "wall", "window"]
    storey = model["storeys"][0]
    assert storey["storey_height_mm"] == 3200.0 and storey["base_elevation_mm"] == 0.0
    window = next(e for e in model["elements"] if e["element_type"] == "window")
    assert window["opening"]["sill_mm"] == 900.0 and window["opening"]["head_mm"] == 2100.0
    assert window["opening"]["tag"] == "W1" and window["opening"]["width_mm"] == 1500.0
    door = next(e for e in model["elements"] if e["element_type"] == "door")
    assert door["opening"]["height_source"] == "assumed_typical" and "door block DOOR_900" in door["opening"]["symbol_evidence"]
    wall = next(e for e in model["elements"] if e["element_type"] == "wall")
    assert max(v[2] for v in wall["mesh"]["vertices"]) == 3200.0
    assert all(v[1] <= 115 for v in wall["mesh"]["vertices"])          # CAD y up -> plan y down
    assert [r["name"] for r in model["rooms"]] == []                     # room text is outside the walls


def test_dxf_without_levels_assumes_a_storey_height_and_says_so(tmp_path):
    path = tmp_path / "plan.dxf"
    make_dxf(path, levels=False)
    model = import_dxf(path, model_id="m", title="plan.dxf")
    assert model["storeys"][0]["storey_height_mm"] is None
    assert any("assumed" in note for note in model["notes"])
    assert all(e["height_status"] == "assumed_typical" for e in model["elements"] if e["element_type"] == "wall")


# ------------------------------------------------------------ IFC

def make_ifc(path):
    model = api.run("project.create_file", version="IFC4")
    project = api.run("root.create_entity", model, ifc_class="IfcProject", name="P")
    api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = api.run("context.add_context", model, context_type="Model")
    body = api.run("context.add_context", model, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=context)
    building = api.run("root.create_entity", model, ifc_class="IfcBuilding")
    api.run("aggregate.assign_object", model, relating_object=project, products=[building])
    storeys = []
    for name, z in (("Ground", 0.0), ("First", 3000.0)):
        storey = api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=name)
        storey.Elevation = z
        api.run("aggregate.assign_object", model, relating_object=building, products=[storey])
        storeys.append(storey)
    for i, storey in enumerate(storeys):
        for ifc_class, (length, height, thickness) in (("IfcWall", (5.0, 3.0, 0.2)), ("IfcDoor", (0.9, 2.1, 0.05))):
            product = api.run("root.create_entity", model, ifc_class=ifc_class, name=ifc_class)
            rep = api.run("geometry.add_wall_representation", model, context=body, length=length, height=height,
                          thickness=thickness)
            api.run("geometry.assign_representation", model, product=product, representation=rep)
            matrix = np.eye(4); matrix[2][3] = 3.0 * i
            api.run("geometry.edit_object_placement", model, product=product, matrix=matrix)
            api.run("spatial.assign_container", model, relating_structure=storey, products=[product])
    model.write(str(path))


def test_ifc_elements_storeys_and_heights(tmp_path):
    path = tmp_path / "house.ifc"
    make_ifc(path)
    model = import_ifc(path, model_id="i", title="house.ifc")
    assert sorted(e["element_type"] for e in model["elements"]) == ["door", "door", "wall", "wall"]
    assert [(s["base_level_key"], s["base_elevation_mm"], s["storey_height_mm"]) for s in model["storeys"]] == [
        ("Ground", 0.0, 3000.0), ("First", 3000.0, None)]
    wall = next(e for e in model["elements"] if e["element_type"] == "wall" and e["storey"]["base_level_key"] == "First")
    zs = [v[2] for v in wall["mesh"]["vertices"]]
    assert (min(zs), max(zs)) == (3000.0, 6000.0)
    assert wall["lod"]["minimum_lod"] == 300 and wall["detected_by"] == "ifc"


def test_ifc_wins_over_dxf_and_pdf_in_the_building_model(tmp_path):
    folder = tmp_path / "project"
    (folder / "models").mkdir(parents=True)
    make_ifc(folder / "models" / "a.ifc")
    make_dxf(folder / "models" / "b.dxf")
    project = {"drawings": [{"id": "pdf-1", "filename": "plan.pdf"}], "models": [
        {"id": "a", "filename": "house.ifc", "stored_filename": "a.ifc", "model_type": "ifc"},
        {"id": "b", "filename": "plan.dxf", "stored_filename": "b.dxf", "model_type": "dxf"},
    ]}
    (folder / "project.json").write_text(json.dumps(project), "utf-8")
    results = imported_models.import_project_models(folder, project)
    assert [r["status"] for r in results] == ["imported", "imported"]
    assert [r["status"] for r in imported_models.import_project_models(folder, project)] == ["kept", "kept"]
    from app.services.building_model import build_building_model
    model = build_building_model(folder, "p")
    assert model["source"] == {"type": "ifc", "files": ["house.ifc"]}
    assert model["summary"]["doors"] == 2 and model["summary"]["plans_in_model"] == 2
    assert {item["drawing_title"] for item in model["excluded_plans"]} == {"plan.pdf", "plan.dxf"}


# ------------------------------------------------------------ upload rules

def _upload(name, data):
    return UploadFile(filename=name, file=io.BytesIO(data))


def test_upload_accepts_models_and_explains_closed_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "PROJECTS_DIR", tmp_path)
    project_id = "11111111-1111-4111-8111-111111111111"
    (tmp_path / project_id).mkdir()
    (tmp_path / project_id / "project.json").write_text(json.dumps({"id": project_id, "drawings": []}), "utf-8")
    dxf_path = tmp_path / "plan.dxf"
    make_dxf(dxf_path)
    files = [_upload("plan.dxf", dxf_path.read_bytes()), _upload("house.rvt", b"revit"),
             _upload("fake.ifc", b"not a step file"), _upload("notes.txt", b"hello")]
    result = asyncio.run(main_module.upload_drawings(project_id, files))
    assert [a["model_type"] for a in result["accepted"]] == ["dxf"]
    reasons = {r["filename"]: r["reason"] for r in result["rejected"]}
    assert "Export > IFC" in reasons["house.rvt"] and "Not an IFC" in reasons["fake.ifc"]
    assert result["total_models_in_project"] == 1


# ------------------------------------------------------------ multi-floor CAD (DWG/DXF sets like Dubai Science Park)

from app.services import cad_import  # noqa: E402


def test_wall_side_drawn_in_pieces_pairs_along_its_whole_length():
    # One long face line, the other face drawn as two pieces (and a short stray piece listed first).
    segments = [((0, 0), (0, 10000)), ((450, 6000), (450, 8000)), ((450, 0), (450, 6000)), ((450, 8000), (450, 10000))]
    rects = sorted(cad_import._line_pairs(segments, 1.0), key=lambda r: r[1])
    assert [(r[1], r[3]) for r in rects] == [(0, 6000), (6000, 8000), (8000, 10000)]
    assert all(r[0] == 0 and r[2] == 450 for r in rects)


def test_chamfered_u_wall_splits_into_straight_runs():
    u = [(0, 0), (5000, 0), (5000, 4000), (4800, 4000), (4800, 250), (4750, 200), (250, 200), (200, 250),
         (200, 4000), (0, 4000)]
    pieces = cad_import._rectilinear_pieces(cad_import._square_chamfers(u, 600))
    assert pieces and all(min(p[2] - p[0], p[3] - p[1]) <= 200 for p in pieces)
    assert sum((p[2] - p[0]) * (p[3] - p[1]) for p in pieces) == pytest.approx(5000 * 200 + 2 * 200 * 3800, rel=0.01)
    assert cad_import._square_chamfers([(0, 0), (3000, 2000), (0, 2000)], 600) is None      # a long slanted edge


def test_room_tag_levels_read_in_metres_with_their_sign():
    texts = [("S.S.L = - 7.60", [0, 0, 1, 1])] * 3 + [("F.F.L = - 7.60", [0, 0, 1, 1])]
    base, top, note = cad_import._levels(texts)
    assert base == -7600.0 and top is None and "metres" in note


def make_plant_dxf(path, *, level="+0.45", roof=False):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    tag = doc.blocks.new("ROOM TAG")
    tag.add_attdef("SSL", (0, 0), dxfattribs={"height": 100})
    chiller = doc.blocks.new("CHILLER (DS)")
    chiller.add_lwpolyline([(0, 0), (10000, 0), (10000, 3000), (0, 3000)], close=True)
    walls = doc.blocks.new("STRUCT WALLS")                       # walls drawn inside a block on a wall layer
    for x in (0, 300):
        walls.add_line((x, 0), (x, 20000))
    msp.add_blockref("STRUCT WALLS", (0, 0), dxfattribs={"layer": "STR WALL"})
    for x in (20000, 20300):
        msp.add_line((x, 0), (x, 20000), dxfattribs={"layer": "A-ARCH-WALL"})
    hatch = msp.add_hatch(dxfattribs={"layer": "200 MM HB"})      # a block-work wall as a hatch
    hatch.paths.add_polyline_path([(300, 0), (20000, 0), (20000, 200), (300, 200)], is_closed=True)
    msp.add_lwpolyline([(2000, 5000), (8000, 5000), (8000, 9000), (2000, 9000)], close=True,
                       dxfattribs={"layer": "A-ARCH-WALL"})       # a room outline on the wall layer: not a wall
    msp.add_blockref("CHILLER (DS)", (5000, 12000), dxfattribs={"layer": "AA-ARCH-WALL"})
    for x in (3000, 9000):
        ref = msp.add_blockref("ROOM TAG", (x, 3000), dxfattribs={"layer": "0-A-Tags"})
        ref.add_attrib("SSL", f"S.S.L= {level}", (x, 3000))
    label = msp.add_mtext("ROOF" if roof else "CHILLER HALL", dxfattribs={"char_height": 200, "attachment_point": 5})
    label.dxf.insert = (10000, 10000)
    doc.saveas(path)


def test_plant_floor_from_cad_walls_equipment_and_levels(tmp_path):
    path = tmp_path / "gf.dxf"
    make_plant_dxf(path)
    assert cad_import.quick_slab_level(path) == 450.0
    model = cad_import.import_dxf(path, model_id="gf", title="GROUND FLOOR PLAN.dxf", top_mm=6050.0)
    storey = model["storeys"][0]
    assert storey["base_elevation_mm"] == 450.0 and storey["storey_height_mm"] == 5600.0
    walls = [e for e in model["elements"] if e["element_type"] == "wall"]
    thick = sorted(e["dimensions_mm"]["thickness"] for e in walls)
    assert thick == [200.0, 300.0, 300.0]                       # block wall, hatch wall, loose lines; no room outline
    chiller = next(e for e in model["elements"] if e["component"] == "chiller")
    assert chiller["detected_by"] == "cad_block" and chiller["dimensions_mm"]["length"] == 10000
    assert chiller["height_status"] == "display_only" and "typical" in chiller["provenance"]["basis"]
    hall = next(r for r in model["rooms"] if r["name"] == "CHILLER HALL")
    assert hall["position_mm"][:2] == pytest.approx([10000, -10000], abs=150)   # a centred label stays centred


def test_floor_plans_stack_on_each_others_levels_and_a_roof_plan_closes_the_top(tmp_path):
    for name, level, roof in (("GF.dxf", "+0.45", False), ("MEZZ.dxf", "+6.05", False), ("ROOF PLAN.dxf", "+11.55", True)):
        make_plant_dxf(tmp_path / name, level=level, roof=roof)
    records = [{"id": n, "filename": n, "stored_filename": n, "model_type": "dxf"} for n in ("GF.dxf", "MEZZ.dxf", "ROOF PLAN.dxf")]
    (tmp_path / "models").mkdir()
    for n in ("GF.dxf", "MEZZ.dxf", "ROOF PLAN.dxf"):
        (tmp_path / n).rename(tmp_path / "models" / n)
    tops = imported_models._storey_tops(tmp_path, records, cad_import.quick_slab_level, cad_import.is_roof_plan)
    assert tops == {"GF.dxf": 6050.0, "MEZZ.dxf": 11550.0}
    roof = cad_import.import_dxf(tmp_path / "models" / "ROOF PLAN.dxf", model_id="r", title="ROOF PLAN.dxf")
    assert roof["roof_plan"] and roof["storeys"] == [] and roof["base_mm"] == 11550.0


def test_slanted_wall_from_angled_line_pairs_keeps_its_shape_and_a_thin_footprint(tmp_path):
    import math as _m
    a = _m.radians(30)
    d, n = (_m.cos(a), _m.sin(a)), (-_m.sin(a), _m.cos(a))
    face = lambda off, s0, s1: ((s0 * d[0] + off * n[0], s0 * d[1] + off * n[1]), (s1 * d[0] + off * n[0], s1 * d[1] + off * n[1]))  # noqa: E731
    segments = [face(0, 0, 20000), face(450, 0, 8000), face(450, 8000, 20000)]       # inner face in two pieces
    quads = cad_import._angled_line_pairs(segments, 1.0)
    assert len(quads) == 2
    for quad in quads:
        thickness = abs((quad[3][0] - quad[0][0]) * n[0] + (quad[3][1] - quad[0][1]) * n[1])
        assert thickness == pytest.approx(450, abs=1)
    strip = cad_import._plan_rects(quads[0] if quads[0][1][0] > 10000 else quads[1])
    assert strip and max(r[2] - r[0] for r in strip) < 2000           # small pieces, not one 17 m x 10 m box

    doc = ezdxf.new("R2018"); doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for (x0, y0), (x1, y1) in segments:
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "STR WALL"})
    msp.add_line((0, 0), (0, 6000), dxfattribs={"layer": "STR WALL"})
    msp.add_line((300, 200), (300, 6000), dxfattribs={"layer": "STR WALL"})
    doc.saveas(tmp_path / "b.dxf")
    model = cad_import.import_dxf(tmp_path / "b.dxf", model_id="b", title="b.dxf")
    slanted = [e for e in model["elements"] if e.get("plan_rects")]
    assert len(slanted) == 2 and all(e["dimensions_mm"]["thickness"] == pytest.approx(450, abs=2) for e in slanted)
    assert all(r[3] <= 0.5 for e in slanted for r in e["plan_rects"])      # building frame: plan y points down


def test_top_floor_opening_does_not_hole_the_roof():
    from app.services import building_model as bm
    storey = {"placement_id": "t", "drawing_title": "t", "base_level_key": "SSL@+0mm", "base_elevation_mm": 0.0,
              "top_level_key": "SSL@+3000mm", "storey_height_mm": 3000.0}
    walls = []
    for k, rect in enumerate([(0, 0, 10000, 200), (0, 7800, 10000, 8000), (0, 0, 200, 8000), (9800, 0, 10000, 8000)]):
        walls.append({"element_id": f"w{k}", "element_type": "wall", "storey": {"placement_id": "t"},
                      "mesh": {"vertices": bm._box_vertices(rect, 0.0, 3000.0), "faces": []}})
    walls.append({"element_id": "c", "element_type": "column", "storey": {"placement_id": "t"},
                  "mesh": {"vertices": bm._box_vertices((200, 200, 9800, 7800), 0.0, 3000.0), "faces": []}})
    slabs = bm.approximate_slabs([storey], walls)
    base = next(s for s in slabs if s["role"] == "floor"); roof = next(s for s in slabs if s["role"] == "roof")
    assert base["footprint"] is not roof["footprint"]
    base["footprint"]["rects_mm"] = []
    assert roof["footprint"]["rects_mm"]


def _box_element(element_id, kind, rect, pid, z0=0.0, z1=3000.0):
    from app.services import building_model as bm
    return {"element_id": element_id, "element_type": kind, "storey": {"placement_id": pid},
            "mesh": {"vertices": bm._box_vertices(rect, z0, z1), "faces": []}}


def _ring(pid, x0, y0, x1, y1, t=300.0):
    return [_box_element(f"{pid}:w{k}", "wall", r, pid) for k, r in enumerate(
        [(x0, y0, x1, y0 + t), (x0, y1 - t, x1, y1), (x0, y0, x0 + t, y1), (x1 - t, y0, x1, y1)])]


def test_floor_slab_also_roofs_the_wider_storey_below_and_basement_columns_in_soil_go():
    from app.services import building_model as bm
    basement = {"placement_id": "b", "drawing_title": "b", "base_level_key": "SSL@-4000mm", "base_elevation_mm": -4000.0}
    ground = {"placement_id": "g", "drawing_title": "g", "base_level_key": "SSL@+0mm", "base_elevation_mm": 0.0,
              "top_level_key": "SSL@+3000mm", "storey_height_mm": 3000.0}
    elements = _ring("b", 0, 0, 20000, 10000) + _ring("g", 0, 0, 10000, 10000)
    elements += [_box_element("b:c-in", "column", (5000, 5000, 5500, 5500), "b"),
                 _box_element("b:c-soil", "column", (-6000, 5000, -5500, 5500), "b"),     # outside the basement walls
                 _box_element("g:c", "column", (5000, 5000, 5500, 5500), "g")]
    notes = bm.drop_columns_in_soil([basement, ground], elements)
    ids = {e["element_id"] for e in elements}
    assert "b:c-soil" not in ids and "b:c-in" in ids and notes
    slabs = {s["element_id"]: s for s in bm.approximate_slabs([basement, ground], elements)}
    ground_floor = slabs["slab:g:base"]["footprint"]
    assert ground_floor["ceiling_area_m2"] == pytest.approx(100, rel=0.1)          # the basement's extra 10 m x 10 m
    assert slabs["slab:g:top"]["footprint"]["area_m2"] < ground_floor["area_m2"]    # the roof covers the ground floor only


def test_facade_proposal_block_on_a_wall_layer_is_not_a_wall(tmp_path):
    doc = ezdxf.new("R2018"); doc.header["$INSUNITS"] = 4
    proposal = doc.blocks.new("RF - PROPOSED FACADE")
    for y in (0, 200):
        proposal.add_line((0, y), (30000, y), dxfattribs={"layer": "A-ARCH-WALL"})
    msp = doc.modelspace()
    msp.add_blockref("RF - PROPOSED FACADE", (0, 5000), dxfattribs={"layer": "A-ARCH-WALL"})
    for x in (0, 300):
        msp.add_line((x, 0), (x, 6000), dxfattribs={"layer": "A-ARCH-WALL"})
    doc.saveas(tmp_path / "p.dxf")
    model = cad_import.import_dxf(tmp_path / "p.dxf", model_id="p", title="p.dxf")
    walls = [e for e in model["elements"] if e["element_type"] == "wall"]
    assert len(walls) == 1 and walls[0]["dimensions_mm"]["length"] == 6000.0


def make_plant_ifc(path):
    model = api.run("project.create_file", version="IFC4")
    project = api.run("root.create_entity", model, ifc_class="IfcProject", name="Plant")
    api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = api.run("context.add_context", model, context_type="Model")
    body = api.run("context.add_context", model, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=context)
    building = api.run("root.create_entity", model, ifc_class="IfcBuilding")
    api.run("aggregate.assign_object", model, relating_object=project, products=[building])
    storey = api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Ground")
    storey.Elevation = 0.0
    api.run("aggregate.assign_object", model, relating_object=building, products=[storey])
    items = [("IfcWall", "Basic Wall:Concrete 300mm"), ("IfcChiller", "Water Cooled Chiller:1000TR:1"),
             ("IfcPump", "M_End Suction Pump:CHW:2"), ("IfcElectricDistributionBoard", "LV Switchboard:MDB-1:3"),
             ("IfcBuildingElementProxy", "Transformer 1500kVA:TX-1:4"), ("IfcPipeSegment", "Pipe Types:Steel:5")]
    for k, (ifc_class, name) in enumerate(items):
        product = api.run("root.create_entity", model, ifc_class=ifc_class, name=name)
        rep = api.run("geometry.add_wall_representation", model, context=body, length=2.0, height=2.0, thickness=1.0)
        api.run("geometry.assign_representation", model, product=product, representation=rep)
        matrix = np.eye(4); matrix[0][3] = 3.0 * k
        api.run("geometry.edit_object_placement", model, product=product, matrix=matrix)
        api.run("spatial.assign_container", model, relating_structure=storey, products=[product])
    model.write(str(path))


def test_ifc_plant_equipment_is_read_and_pipes_are_counted(tmp_path):
    path = tmp_path / "plant.ifc"
    make_plant_ifc(path)
    model = import_ifc(path, model_id="p", title="plant.ifc")
    kinds = {e["ifc_class"]: (e["element_type"], e["component"]) for e in model["elements"]}
    assert kinds["IfcChiller"] == ("furniture", "chiller")
    assert kinds["IfcPump"] == ("furniture", "pump set")
    assert kinds["IfcElectricDistributionBoard"] == ("furniture", "electrical panel")
    assert kinds["IfcBuildingElementProxy"] == ("furniture", "transformer")
    assert kinds["IfcWall"][0] == "wall" and "IfcPipeSegment" not in kinds
    assert any("pipe, duct" in n for n in model["notes"])
    chiller = next(e for e in model["elements"] if e["ifc_class"] == "IfcChiller")
    assert chiller["lod"]["minimum_lod"] == 500 and chiller["furniture"]["modelled"] is True


def test_cad_sheets_are_sorted_into_plans_roofs_and_references():
    role = lambda t: imported_models.cad_sheet_role(t)[0]     # noqa: E731
    assert role("DSP-DCP-DWG-ARC-1003_ GROUND FLOOR PLAN.dwg") == "floor_plan"
    assert role("DSP-DCP-DWG-ARC-1002_BASEMENT FLOOR - UPPER LEVEL.dwg") == "floor_plan"
    assert role("DSP-DCP-DWG-ARC-1007_ROOF FLOOR - TOP ROOF PLAN.dwg") == "roof_plan"
    for name in ("SECTION A - A.dwg", "ELEVATION B & D.dwg", "DOOR SCHEDULE - SHEETS.dwg", "WET AREA DETAILS.dwg",
                 "REFLECTED CEILING FINISHES LAYOUT.dwg", "SITE SETTING OUT PLAN.dwg", "SIGNAGE WAY FINDING - 1.dwg",
                 "PARTIAL PLANS & ELEVATIONS.dwg", "COVERPAGE.dwg"):
        assert role(name) == "reference", name
    assert role("HOUSE.dxf") == "floor_plan"          # a name that says nothing is read as a plan


def test_a_section_uploaded_with_the_plans_is_not_built_and_does_not_change_the_levels(tmp_path):
    (tmp_path / "models").mkdir()
    for name, level in (("GF PLAN.dxf", "+0.45"), ("FF PLAN.dxf", "+4.05")):
        make_plant_dxf(tmp_path / "models" / name, level=level)
    (tmp_path / "models" / "SECTION A.dxf").write_text("S.S.L= +1.20 S.S.L= +1.20 S.S.L= +1.20", "utf-8")
    project = {"models": [{"id": n, "filename": n, "stored_filename": n, "model_type": "dxf"}
                          for n in ("GF PLAN.dxf", "FF PLAN.dxf", "SECTION A.dxf")]}
    results = {r["model_id"]: r["status"] for r in imported_models.import_project_models(tmp_path, project)}
    assert results == {"GF PLAN.dxf": "imported", "FF PLAN.dxf": "imported", "SECTION A.dxf": "reference"}
    ground = json.loads((tmp_path / "artifacts" / "imported" / "GF PLAN.dxf.json").read_text("utf-8"))
    assert ground["storeys"][0]["storey_height_mm"] == 3600.0          # up to the first floor, not the section's +1.20


def test_two_plans_of_the_same_floor_build_it_once():
    def plan(mid, walls):
        return {"model_id": mid, "title": mid, "storeys": [{"base_elevation_mm": 450.0}],
                "elements": [{"element_type": "wall", "mesh": {"vertices": [[0, 0, 0], [x, 5000, 0]]}} for x in walls]}
    main, copy_, wing = plan("GF", [9000, 10000, 10000]), plan("GF furniture copy", [10000]), plan("GF", [])
    wing["elements"] = [{"element_type": "wall", "mesh": {"vertices": [[50000, 0, 0], [60000, 5000, 0]]}}]
    kept, duplicates = imported_models._drop_duplicate_floors([copy_, main, wing])
    assert [m["title"] for m in kept] == ["GF", "GF"] and duplicates[0][0] is copy_   # the other wing stays


def test_slab_cells_are_cut_along_a_slanted_wall():
    from app.services import building_model as bm
    storey = {"placement_id": "b", "base_elevation_mm": -4000.0}
    # A slanted wall from (0, 0) to (10000, 10000), 450 thick, building on its lower-right side.
    wall = {"element_id": "w", "element_type": "wall", "storey": {"placement_id": "b"},
            "plan_rects": [[0, 0, 10000, 10000]],
            "mesh": {"vertices": [[0, 0, 0], [10000, 10000, 0], [10318, 9682, 0], [318, -318, 0]] * 2, "faces": []}}
    slab = {"element_id": "slab:b:base", "footprint": {"rects_mm": [[0, 0, 10000, 10000], [10000, 0, 20000, 10000]]}}
    bm.trim_slabs_to_slanted_walls([slab], [wall], [storey])
    polygons = slab["footprint"]["polygons_mm"]
    assert slab["footprint"]["trimmed_to_slanted_walls"] >= 1
    assert all(x - y >= -1 for poly in polygons for x, y in poly)      # nothing left beyond the wall's outer face


def test_roof_plan_equipment_stands_on_the_roof_once():
    roof_unit = lambda mid: {"element_id": f"{mid}:ct", "component": "cooling tower", "furniture": {"room": "roof"},   # noqa: E731
                             "mesh": {"vertices": [[0, 0, 11550], [15000, 40000, 17050]], "faces": []}}
    storeys = [{"placement_id": "g", "base_level_key": "G", "base_elevation_mm": 450.0},
               {"placement_id": "m", "base_level_key": "M", "base_elevation_mm": 6050.0}]
    placed = imported_models._roof_equipment([{"roof_elements": [roof_unit("a")]}, {"roof_elements": [roof_unit("b")]}], storeys)
    assert len(placed) == 1 and placed[0]["storey"]["placement_id"] == "m"


def test_cooling_tower_fan_stacks_follow_the_drawn_rings():
    mesh, parts = cad_import._equipment_mesh([(0, 0), (15000, 0), (15000, -40000), (0, -40000)], 11550.0,
                                             "cooling tower", 5500.0, "#cfd6dc", [(7500, -13000, 5600)])
    assert parts[-1]["color"] == cad_import.FAN_STACK_COLOUR
    assert max(v[2] for v in mesh["vertices"]) == 11550.0 + 5500.0 + cad_import.FAN_STACK_MM
