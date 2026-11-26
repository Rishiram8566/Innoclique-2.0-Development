import hashlib
import json

import pytest
from fastapi import HTTPException

from app.services import building_model
from app.services import column_detection as cd
from app.services import column_router
from app.services import hatch_wall_detection as hw
from app.services import height_verification_ledger as ledger_service
from app.services import sheet_registration
from app.services import wall_mesh_generation as generation
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "33333333-3333-4333-8333-333333333333"
SHA = "c" * 64
SIZE = (1000.0, 800.0)
SCALE = 50.0           # mm per PDF point: 5 pt = 250 mm wall
GREY = [0.3, 0.3, 0.3]
BLACK = [0.0, 0.0, 0.0]
LEGEND_TEXT = "250mm THERMAL INSULATED BLOCK WALL"
LEGEND_BBOX = [850.0, 698.0, 858.0, 710.0]


def line(path, start, end, stroke=BLACK):
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": "line",
            "start": list(start), "end": list(end), "stroke": stroke, "fill": None}


def quad(path, x0, y0, x1, y1):
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": "quad",
            "points": [[x0, y0], [x0, y1], [x1, y1], [x1, y0]], "stroke": BLACK, "fill": None}


def hatch_h(path, x0, x1, y0, y1, stroke=GREY, step=2.0):
    """45-degree hatch strokes inside a horizontal strip."""
    out, x, n = [], x0 + 0.5, 0
    while x + 3 < x1:
        out.append(line(path + n, (x, y0 + 1), (x + (y1 - y0 - 2), y1 - 1), stroke))
        x += step
        n += 1
    return out


def hatch_v(path, x0, x1, y0, y1, stroke=GREY, step=2.0):
    out, y, n = [], y0 + 0.5, 0
    while y + 3 < y1:
        out.append(line(path + n, (x0 + 1, y), (x1 - 1, y + (x1 - x0 - 2)), stroke))
        y += step
        n += 1
    return out


def page_primitives():
    prims = [
        # columns on grid A/1, B/1, A/2 (16 pt = 800 mm)
        quad(1, 192, 192, 208, 208), quad(2, 392, 192, 408, 208), quad(3, 192, 492, 208, 508),
        # perimeter column at B/2 drawn with its left face on grid B
        quad(4, 400, 492, 416, 508),
        # legend swatch + its hatch
        quad(5, 800, 696, 840, 712),
        # horizontal wall A/1-B/1 stopping 200 mm short of both columns
        line(10, (212, 198), (388, 198)), line(11, (212, 203), (388, 203)),
        # vertical wall A/1-A/2
        line(12, (198, 212), (198, 488)), line(13, (203, 212), (203, 488)),
        # paving-like strip outside: black vertical strokes, must not match the grey legend
        line(14, (600, 600), (900, 600)), line(15, (600, 605), (900, 605)),
        # two lines with nothing between them
        line(16, (600, 300), (900, 300)), line(17, (600, 304), (900, 304)),
    ]
    prims += [line(100 + i, (802 + i * 2, 698), (802 + i * 2 + 10, 708), GREY) for i in range(14)]
    prims += hatch_h(1000, 212, 388, 198, 203)
    prims += hatch_v(2000, 198, 203, 212, 488)
    prims += [line(3000 + i, (601 + i * 3, 601), (601 + i * 3, 604)) for i in range(99)]
    return prims


def scan():
    return hw.scan_primitives(page_primitives(), min_line_pt=hw.MIN_WALL_LENGTH_MM / SCALE)


# ------------------------------------------------------------ pure logic

def test_hatch_key_angles():
    assert hw.hatch_key(GREY, [0, 0], [3, 3]) == ((0.3, 0.3, 0.3), "45")
    assert hw.hatch_key(BLACK, [0, 0], [0, 4]) == ((0.0, 0.0, 0.0), "90")
    assert hw.hatch_key(BLACK, [1, 1], [1.1, 1.1])[1] == "dot"


def test_legend_signature_comes_from_the_swatch_beside_the_text():
    signatures = hw.legend_signatures(scan(), [(LEGEND_TEXT, LEGEND_BBOX), ("NOT A LEGEND WALL", [100, 100, 108, 112])])
    assert list(signatures) == [LEGEND_TEXT]
    assert signatures[LEGEND_TEXT]["signature"] == {((0.3, 0.3, 0.3), "45"): 1.0}


def test_hatched_strips_become_walls_and_other_strips_do_not():
    walls, _ = hw.detect_hatch_walls(scan(), drawing_id=DRAWING, page_number=1,
                                     legend_labels=[(LEGEND_TEXT, LEGEND_BBOX)], millimetres_per_pdf_point=SCALE)
    assert [(w["orientation"], w["bbox_pdf_points"]) for w in walls] == [
        ("horizontal", [212.0, 198.0, 388.0, 203.0]),
        ("vertical", [198.0, 212.0, 203.0, 488.0]),
    ]
    assert walls[0]["thickness_mm"] == 250.0 and walls[0]["length_mm"] == 8800.0
    assert walls[0]["wall_type"] == LEGEND_TEXT and walls[0]["hatch_coverage"] == 1.0


def test_no_legend_means_no_hatch_walls():
    walls, signatures = hw.detect_hatch_walls(scan(), drawing_id=DRAWING, page_number=1,
                                              legend_labels=[], millimetres_per_pdf_point=SCALE)
    assert walls == [] and signatures == {}


def test_a_dashed_line_along_the_wall_does_not_split_it():
    prims = page_primitives()
    # a dashed grid line along the middle of the horizontal wall
    prims += [line(4000 + i, (215 + i * 40, 200.5), (240 + i * 40, 200.5)) for i in range(4)]
    walls, _ = hw.detect_hatch_walls(hw.scan_primitives(prims, min_line_pt=12.0), drawing_id=DRAWING, page_number=1,
                                     legend_labels=[(LEGEND_TEXT, LEGEND_BBOX)], millimetres_per_pdf_point=SCALE)
    horizontal = [w["bbox_pdf_points"] for w in walls if w["orientation"] == "horizontal"]
    assert horizontal == [[212.0, 198.0, 388.0, 203.0]]


def test_suggestions_inside_outline_and_concrete_needs_a_connection():
    columns = [{"bbox_pdf_points": b} for b in ([0, 0, 10, 10], [200, 0, 210, 10], [0, 200, 10, 210], [200, 200, 210, 210])]

    def wall(wall_id, bbox, wall_type, length_mm):
        return {"wall_id": wall_id, "bbox_pdf_points": bbox, "wall_type": wall_type, "length_mm": length_mm}

    walls = [
        wall("masonry", [10, 3, 200, 8], "BLOCK WALL", 9500),
        wall("concrete-linked", [3, 10, 8, 120], "REINFORCED CONCRETE WALL", 5500),
        wall("concrete-plinth", [80, 80, 120, 90], "REINFORCED CONCRETE WALL", 2000),
        wall("concrete-free", [60, 150, 150, 158], "REINFORCED CONCRETE WALL", 4500),
        wall("outside", [600, 600, 700, 605], "BLOCK WALL", 5000),
    ]
    hw.mark_suggestions(walls, columns, millimetres_per_pdf_point=SCALE)
    assert {w["wall_id"]: w["suggested"] for w in walls} == {
        "masonry": True, "concrete-linked": True, "concrete-plinth": False, "concrete-free": False, "outside": False,
    }
    assert "outside the column outline" in walls[4]["suggestion_reason"]


def test_perimeter_column_with_its_face_on_the_grid_line():
    grid = {"vertical": [("A", 200.0), ("B", 400.0)], "horizontal": [("1", 200.0), ("2", 500.0)]}
    columns = cd.detect_columns([p for p in page_primitives() if p["type"] == "quad"], drawing_id=DRAWING,
                                page_number=1, grid=grid, millimetres_per_pdf_point=SCALE)
    by_label = {c["grid_label"]: c["grid_alignment"] for c in columns}
    assert by_label == {"A/1": "centred", "B/1": "centred", "A/2": "centred", "B/2": "face_on_grid"}


def test_hatch_wall_mesh_is_a_closed_box_of_storey_height():
    walls, _ = hw.detect_hatch_walls(scan(), drawing_id=DRAWING, page_number=1,
                                     legend_labels=[(LEGEND_TEXT, LEGEND_BBOX)], millimetres_per_pdf_point=SCALE)
    mesh = hw.build_hatch_wall_mesh(walls[0], millimetres_per_pdf_point=SCALE, height_mm=4200.0)
    assert mesh["mesh"]["closed_manifold"] and mesh["detected_by"] == "legend_hatch"
    assert mesh["component"] == "250mm thermal insulated block wall"
    assert mesh["dimensions_mm"]["height"] == 4200.0 and mesh["dimensions_mm"]["thickness"] == 250.0
    with pytest.raises(ValueError):
        hw.build_hatch_wall_mesh(walls[0], millimetres_per_pdf_point=SCALE, height_mm=0)


# ------------------------------------------------------------ joins and superseding

def box(element_id, rect, element_type="wall", storey="s1", detected_by="legend_hatch"):
    x0, y0, x1, y1 = rect
    return {"element_id": element_id, "element_type": element_type, "detected_by": detected_by,
            "drawing_id": "d", "storey": {"placement_id": storey},
            "mesh": {"vertices": building_model._box_vertices(rect, 0.0, 3000.0), "faces": []}}


def joined(elements, element_id):
    element = next(e for e in elements if e["element_id"] == element_id)
    return building_model._rect({"mesh": element.get("joined_mesh") or element["mesh"]}), element.get("joins")


def test_corner_tee_column_and_collinear_joins():
    elements = [
        box("h", (0, 0, 4750, 250)), box("v", (4750, 250, 5000, 4000)),                    # L corner, notch missing
        box("long", (0, 6000, 10000, 6250)), box("stem", (4000, 6400, 4250, 9000)),       # tee, 150 mm short
        box("col", (0, 10000, 400, 10400), element_type="column", detected_by=None),
        box("to-col", (500, 10075, 4000, 10325)),                                          # 100 mm short of column
        box("door-a", (0, 12000, 3000, 12250)), box("door-b", (3900, 12000, 8000, 12250)),  # 900 mm door: no join
        box("gap-a", (0, 14000, 3000, 14250)), box("gap-b", (3100, 14000, 8000, 14250)),    # 100 mm drawing gap
        box("other-storey", (5000, 0, 5250, 4000), storey="s2"),
    ]
    building_model.add_wall_joins(elements)
    assert joined(elements, "h")[0] == (0, 0, 5000, 250)
    assert joined(elements, "v")[0] == (4750, 0, 5000, 4000)
    assert joined(elements, "stem")[0] == (4000, 6250, 4250, 9000)
    assert joined(elements, "stem")[1][0]["join"] == "tee into wall face"
    assert joined(elements, "to-col")[0] == (400, 10075, 4000, 10325)
    assert joined(elements, "door-a")[1] is None and joined(elements, "door-b")[1] is None
    assert joined(elements, "gap-a")[0] == (0, 14000, 3100, 14250)
    assert joined(elements, "other-storey")[1] is None


def test_line_pair_wall_on_a_hatch_wall_is_superseded():
    elements = [
        box("hatch", (0, 0, 5000, 250)),
        box("pair-on-it", (100, 0, 4900, 240), detected_by=None),
        box("pair-elsewhere", (0, 3000, 5000, 3250), detected_by=None),
        box("pair-other-storey", (100, 0, 4900, 240), storey="s2", detected_by=None),
    ]
    kept, superseded = building_model.supersede_line_pair_walls(elements)
    assert [e["element_id"] for e in kept] == ["hatch", "pair-elsewhere", "pair-other-storey"]
    assert superseded[0]["element_id"] == "pair-on-it" and superseded[0]["superseded_by"] == "hatch"


def test_isolated_column_is_left_out_of_the_slab_outline():
    columns = [box(f"c{i}", (x, y, x + 500, y + 500), element_type="column", detected_by=None)
               for i, (x, y) in enumerate([(0, 0), (6000, 0), (0, 6000), (6000, 6000), (60000, 30000)])]
    assert building_model._isolated_columns(columns) == {"c4"}


# ------------------------------------------------------------ end to end

@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT
    artifacts = folder / "artifacts"
    (artifacts / "primitive_sidecars").mkdir(parents=True)
    recognition = {"project_id": PROJECT, "drawings": [{
        "drawing_id": DRAWING, "filename": "BASEMENT.pdf", "source_sha256": SHA,
        "pages": [{"page_number": 1, "page_size_pdf_points": {"width": SIZE[0], "height": SIZE[1]}, "candidates": []}]}]}
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(recognition), "utf-8")
    (artifacts / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(json.dumps({"calibrations": {DRAWING: {"1": {
        "status": "calibrated", "drawing_id": DRAWING, "page_number": 1, "source_sha256": SHA,
        "millimetres_per_pdf_point": SCALE, "verification_required": True, "source_reference": "test"}}}}), "utf-8")

    def span(text, bbox, i):
        return {"evidence_id": f"ev-{i}", "project_id": PROJECT, "drawing_id": DRAWING, "page_number": 1,
                "categories": ["unclassified_text"], "text": text,
                "source_reference": {"drawing_id": DRAWING, "page_number": 1, "span_index": i, "text": text,
                                     "bbox_pdf_points": bbox, "source_sha256": SHA}}

    labels = {"A": [(200.0, 40.0), (200.0, 760.0)], "B": [(400.0, 40.0), (400.0, 760.0)],
              "1": [(40.0, 200.0), (960.0, 200.0)], "2": [(40.0, 500.0), (960.0, 500.0)]}
    evidence = [span(label, [x - 4, y - 6, x + 4, y + 6], 0) for label, points in labels.items() for x, y in points]
    evidence += [span("S.S.L = - 7.60", [496, 294, 504, 306], 0), span("S.S.L = - 3.40", [496, 314, 504, 326], 0),
                 span("FFL +6.15 m", [496, 334, 504, 346], 0), span(LEGEND_TEXT, LEGEND_BBOX, 0)]
    evidence = [{**e, "evidence_id": f"ev-{n}", "source_reference": {**e["source_reference"], "span_index": n}}
                for n, e in enumerate(evidence)]
    (artifacts / sheet_registration.EVIDENCE_ARTIFACT).write_text(json.dumps({"evidence": evidence}), "utf-8")
    report = build_height_association_report({"stage": "5.3.3", "project_id": PROJECT, "evidence": evidence})
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(report), "utf-8")
    body = "".join(json.dumps(p) + "\n" for p in page_primitives()).encode()
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1.jsonl").write_bytes(body)
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1_manifest.json").write_text(
        json.dumps({"source_pdf_sha256": SHA, "sidecar_sha256": hashlib.sha256(body).hexdigest()}), "utf-8")
    monkeypatch.setattr(column_router, "PROJECTS_DIR", tmp_path)
    sheet_registration._cached_index.cache_clear()
    hw._cached_scan.cache_clear()
    return folder


def place(folder):
    ledger = ledger_service.load_ledger(folder, PROJECT)
    recognition = json.loads((folder / "artifacts" / ledger_service.RECOGNITION_ARTIFACT_NAME).read_text("utf-8"))
    report = json.loads((folder / "artifacts" / HEIGHT_ARTIFACT).read_text("utf-8"))
    record = ledger_service.build_placement_record(ledger=ledger, recognition_report=recognition, height_report=report, request={
        "drawing_id": DRAWING, "page_number": 1, "base_level_key": "SSL@-7600mm", "top_level_key": "SSL@-3400mm",
        "reference_point_pdf": [200.0, 200.0], "reference_label": "Grid A/1",
        "reviewer": "Test Reviewer", "justification": "Basement plan at SSL -7.60, next slab SSL -3.40."})
    ledger_service.apply_placement(ledger, record)
    ledger_service.save_ledger(folder, ledger)
    return record


def confirm_walls(ids):
    return column_router.confirm_hatch_walls(PROJECT, column_router.HatchWallConfirmRequest(
        drawing_id=DRAWING, page_number=1, element_ids=ids, reviewer="Test Reviewer",
        justification="Hatched block walls between the grid columns."))["confirmation"]


def test_hatch_walls_confirm_generate_join_and_serve(project):
    view = column_router.get_page_hatch_walls(PROJECT, drawing_id=DRAWING, page_number=1)
    assert view["summary"]["detected"] == 2 and view["summary"]["suggested"] == 2
    assert view["legend"][0]["label"] == LEGEND_TEXT
    ids = [w["wall_id"] for w in view["candidates"]]
    with pytest.raises(HTTPException) as unplaced:
        confirm_walls(ids)
    assert unplaced.value.status_code == 422 and "Place this page" in unplaced.value.detail

    placement = place(project)
    record = confirm_walls(ids)
    assert record["element_kind"] == "hatch_wall" and record["height_mm"] == 4200.0
    assert record["wall_types"] == {LEGEND_TEXT: 2}
    with pytest.raises(HTTPException) as again:
        confirm_walls(ids[:1])
    assert again.value.status_code == 409
    with pytest.raises(HTTPException) as unknown:
        confirm_walls(["not-a-wall"])
    assert unknown.value.status_code == 422

    columns = column_router.get_page_columns(PROJECT, drawing_id=DRAWING, page_number=1)
    assert columns["confirmations_on_page"] == []          # wall confirmations are listed separately
    column_router.confirm_columns(PROJECT, column_router.ColumnConfirmRequest(
        drawing_id=DRAWING, page_number=1, column_ids=[c["column_id"] for c in columns["candidates"]],
        reviewer="Test Reviewer", justification="Column squares on grid intersections, slab to slab."))

    artifact, _ = generation.generate_page_model(project, PROJECT, DRAWING, 1)
    assert artifact["summary"] == {"confirmed_walls": 0, "confirmed_columns": 4, "confirmed_hatch_walls": 2,
                                   "meshes_built": 6, "failed": 0}

    model = building_model.build_building_model(project, PROJECT)
    walls = {e["element_id"]: e for e in model["elements"] if e["element_type"] == "wall"}
    assert len(walls) == 2 and model["summary"]["walls_joined"] == 2
    horizontal = walls[ids[0]]
    # As drawn: x 212..388 pt -> 600..9400 mm from grid A; LOD 400 meets the column faces at 400 and 9600 mm.
    assert building_model._rect(horizontal)[0::2] == (600.0, 9400.0)
    assert building_model._rect({"mesh": horizontal["joined_mesh"]})[0::2] == (400.0, 9600.0)
    assert {j["join"] for j in horizontal["joins"]} == {"to column face"}
    assert horizontal["component"] == "250mm thermal insulated block wall"

    ledger = ledger_service.load_ledger(project, PROJECT)
    ledger_service.revoke_placement(ledger, placement["placement_id"], reviewer="Test Reviewer",
                                    reason="Wrong base level was chosen for this plan.")
    ledger_service.save_ledger(project, ledger)
    served, withheld = ledger_service.screen_mesh_elements(artifact["elements"], ledger)
    assert served == [] and len(withheld) == 6
    assert any("hatch wall" in reason for item in withheld for reason in item["reasons"])


# ------------------------------------------------------------ slab footprints

def ring(x0, y0, x1, y1, t=250.0, gap=None):
    """Four walls around a rectangle; gap=(x_from, x_to) leaves an opening in the south wall."""
    south = [(x0, y1 - t, x1, y1)] if gap is None else [(x0, y1 - t, gap[0], y1), (gap[1], y1 - t, x1, y1)]
    return [(x0, y0, x1, y0 + t), (x0, y0, x0 + t, y1), (x1 - t, y0, x1, y1)] + south


def test_slab_footprint_is_the_area_enclosed_by_walls():
    footprint = building_model.slab_footprint(ring(0, 0, 20000, 10000))
    assert footprint["method"] == "enclosed_by_walls_and_columns"
    cell = footprint["cell_mm"]
    assert 100 <= cell <= 500
    assert abs(footprint["area_m2"] - 200.0) <= 2 * 30000 * cell / 1e6      # within a cell around the outline
    xs = [v for r in footprint["rects_mm"] for v in (r[0], r[2])]
    ys = [v for r in footprint["rects_mm"] for v in (r[1], r[3])]
    assert abs(min(xs)) <= cell and abs(max(xs) - 20000) <= cell
    assert abs(min(ys)) <= cell and abs(max(ys) - 10000) <= cell
    assert len(footprint["edges_mm"]) == 4


def test_slab_footprint_bridges_openings_but_not_open_sides():
    door = building_model.slab_footprint(ring(0, 0, 20000, 10000, gap=(9000, 10500)))
    closed = building_model.slab_footprint(ring(0, 0, 20000, 10000))
    assert door is not None and door["area_m2"] == closed["area_m2"]
    assert building_model.slab_footprint(ring(0, 0, 40000, 20000, gap=(2000, 38000))) is None
    assert building_model.slab_footprint([]) is None


def test_slab_uses_storey_below_outline_when_its_own_is_open():
    storeys = [
        {"placement_id": "low", "base_level_key": "L0", "base_elevation_mm": 0.0, "drawing_title": "LOW",
         "top_level_key": "L1", "storey_height_mm": 4000.0},
        {"placement_id": "up", "base_level_key": "L1", "base_elevation_mm": 4000.0, "drawing_title": "UP",
         "top_level_key": None, "storey_height_mm": None},
    ]
    elements = [box(f"low{i}", r, storey="low") for i, r in enumerate(ring(0, 0, 20000, 10000))]
    elements += [box(f"up{i}", r, storey="up") for i, r in enumerate(ring(0, 0, 40000, 20000, gap=(2000, 38000)))]
    elements += [box(f"c{i}", (x, y, x + 400, y + 400), element_type="column", storey="up", detected_by=None)
                 for i, (x, y) in enumerate([(5000, 5000), (15000, 5000), (5000, 12000), (15000, 12000)])]
    slabs = building_model.approximate_slabs(storeys, elements)
    assert [s["outline_method"] for s in slabs] == ["enclosed_by_walls_and_columns", "enclosed_by_walls_of_storey_below"]
    assert slabs[1]["footprint"]["from_storey"] == "LOW"


def test_isolated_columns_are_flagged_not_removed():
    storeys = [{"placement_id": "s1", "base_level_key": "L0"}]
    elements = [box(f"c{i}", (x, y, x + 500, y + 500), element_type="column", detected_by=None)
                for i, (x, y) in enumerate([(0, 0), (6000, 0), (0, 6000), (6000, 6000), (60000, 30000)])]
    flagged = building_model.flag_isolated_columns(storeys, elements)
    assert [f["element_id"] for f in flagged] == ["c4"]
    assert elements[4]["review_flags"] and not elements[0].get("review_flags")


# ------------------------------------------------------------ found by the 2BHK test drawing

def test_pdf_re_rectangles_are_read_as_quads():
    rect = {"page_number": 1, "path_index": 7, "item_index": 0, "type": "rectangle",
            "bbox": [10.0, 20.0, 30.0, 26.0], "stroke": BLACK, "fill": BLACK}
    quad_form = hw.as_quad(rect)
    assert quad_form["type"] == "quad" and quad_form["points"] == [[10.0, 20.0], [10.0, 26.0], [30.0, 26.0], [30.0, 20.0]]
    scan_result = hw.scan_primitives([rect], min_line_pt=5.0)
    assert len(scan_result.quads) == 1 and len(scan_result.horizontal) == 2


def test_small_rectangular_columns_fill_only_empty_intersections():
    grid = {"vertical": [("A", 200.0), ("B", 400.0)], "horizontal": [("1", 200.0), ("2", 500.0)]}
    small = {"page_number": 1, "path_index": 50, "item_index": 0, "type": "rectangle",
             "bbox": [397.7, 497.0, 402.3, 503.0], "stroke": BLACK, "fill": BLACK}   # 230 x 300 mm at B/2
    also_small_at_a1 = {**small, "path_index": 51, "bbox": [197.7, 197.0, 202.3, 203.0]}
    columns = cd.detect_columns([p for p in page_primitives() if p["type"] == "quad" and p["path_index"] != 4]
                                + [small, also_small_at_a1],
                                drawing_id=DRAWING, page_number=1, grid=grid, millimetres_per_pdf_point=SCALE)
    by_label = {c["grid_label"]: (c["grid_alignment"], c["size_mm"]) for c in columns}
    assert by_label["B/2"] == ("small_or_elongated", [230.0, 300.0])
    assert by_label["A/1"][0] == "centred"                       # the 800 mm column keeps its intersection


def test_a_grid_line_through_a_wall_does_not_halve_it():
    prims = page_primitives()
    prims.append(line(5000, (150, 200.5), (450, 200.5), GREY))   # one long grid line along the wall's middle
    walls, _ = hw.detect_hatch_walls(hw.scan_primitives(prims, min_line_pt=12.0), drawing_id=DRAWING, page_number=1,
                                     legend_labels=[(LEGEND_TEXT, LEGEND_BBOX)], millimetres_per_pdf_point=SCALE)
    assert [w["bbox_pdf_points"] for w in walls if w["orientation"] == "horizontal"] == [[212.0, 198.0, 388.0, 203.0]]


def test_same_hatch_legend_entries_are_told_apart_by_printed_thickness():
    signature = {"swatch_pdf": [0, 0, 1, 1], "signature": {((0.3, 0.3, 0.3), "45"): 1.0}}
    signatures = {"230 THK. BRICK WALL": signature, "115 THK. BRICK PARTITION WALL": signature}
    found = [(float(i), ((0.3, 0.3, 0.3), "45")) for i in range(10)]
    assert hw.match_hatch(found, signatures, 118.0)[0] == "115 THK. BRICK PARTITION WALL"
    assert hw.match_hatch(found, signatures, 226.0)[0] == "230 THK. BRICK WALL"
    assert hw.stated_thickness_mm("250mm THERMAL INSULATED BLOCK WALL") == 250.0
    assert hw.stated_thickness_mm("HOLLOW BLOCK WALL") is None
