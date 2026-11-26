import hashlib
import json

import pytest
from fastapi import HTTPException

from app.services import building_model
from app.services import column_detection as cd
from app.services import column_router
from app.services import height_verification_ledger as ledger_service
from app.services import sheet_registration
from app.services import wall_mesh_generation as generation
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "22222222-2222-4222-8222-222222222222"
SHA = "a" * 64
SIZE = (1000.0, 800.0)
SCALE = 50.0     # mm per PDF point -> a 16 pt square is an 800 mm column


def labels_entry():
    # Grid A at x=200, B at x=400 (bubbles top and bottom); 1 at y=200, 2 at y=500 (left and right).
    # A second "B" is a section-cut marker elsewhere and must not replace grid B.
    return {"labels": {
        "A": [(200.0, 40.0), (200.0, 760.0)],
        "B": [(400.0, 40.0), (400.0, 760.0), (700.0, 100.0), (700.0, 700.0)],
        "1": [(40.0, 200.0), (960.0, 200.0)],
        "2": [(40.0, 500.0), (960.0, 500.0)],
    }}


def quad(path, cx, cy, half_w, half_h=None):
    half_h = half_w if half_h is None else half_h
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": "quad",
            "points": [[cx - half_w, cy - half_h], [cx - half_w, cy + half_h],
                       [cx + half_w, cy + half_h], [cx + half_w, cy - half_h]]}


PRIMITIVES = [
    quad(1, 200, 200, 8), quad(2, 400, 200, 8), quad(3, 200, 500, 8), quad(4, 400, 500, 8.5),
    quad(5, 300, 350, 8),            # not on a grid intersection
    quad(6, 200.5, 200.5, 8),        # same column drawn twice
    quad(7, 400, 500, 2),            # too small (200 mm)
    quad(8, 200, 500, 8, 20),        # too elongated
]


def test_grid_lines_keep_every_line_for_a_repeated_label():
    grid = cd.grid_lines(labels_entry(), SIZE)
    assert grid["vertical"] == [("A", 200.0), ("B", 400.0), ("B", 700.0)]
    assert grid["horizontal"] == [("1", 200.0), ("2", 500.0)]


def test_detect_columns_on_grid_intersections_only():
    columns = cd.detect_columns(PRIMITIVES, drawing_id=DRAWING, page_number=1,
                                grid=cd.grid_lines(labels_entry(), SIZE), millimetres_per_pdf_point=SCALE)
    assert [c["grid_label"] for c in columns] == ["A/1", "B/1", "A/2", "B/2"]
    assert columns[0]["size_mm"] == [800.0, 800.0]
    assert columns[3]["size_mm"] == [850.0, 850.0]
    assert columns[0]["column_id"] == f"{DRAWING}:p1:column:1-0"


def test_column_mesh_is_a_closed_box_of_storey_height():
    column = cd.detect_columns(PRIMITIVES, drawing_id=DRAWING, page_number=1,
                               grid=cd.grid_lines(labels_entry(), SIZE), millimetres_per_pdf_point=SCALE)[0]
    mesh = cd.build_column_mesh(column, millimetres_per_pdf_point=SCALE, height_mm=4200.0)
    assert mesh["mesh"]["closed_manifold"] is True
    assert mesh["dimensions_mm"]["height"] == 4200.0
    assert mesh["lod"]["minimum_lod"] == 100
    with pytest.raises(ValueError):
        cd.build_column_mesh(column, millimetres_per_pdf_point=SCALE, height_mm=0)


def test_convex_hull_slab_outline():
    hull = building_model._convex_hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (5, 0)])
    assert sorted(hull) == [(0, 0), (0, 10), (10, 0), (10, 10)]


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

    def span(text, x, y, i):
        return {"evidence_id": f"ev-{i}", "project_id": PROJECT, "drawing_id": DRAWING, "page_number": 1,
                "categories": ["unclassified_text"], "text": text,
                "source_reference": {"drawing_id": DRAWING, "page_number": 1, "span_index": i, "text": text,
                                     "bbox_pdf_points": [x - 4, y - 6, x + 4, y + 6], "source_sha256": SHA}}

    evidence = [span(label, x, y, i) for i, (label, points) in enumerate(labels_entry()["labels"].items())
                for x, y in points]
    evidence = [{**e, "evidence_id": f"ev-{n}"} for n, e in enumerate(evidence)]
    evidence += [span("S.S.L = - 7.60", 500, 300, 90), span("S.S.L = - 3.40", 500, 320, 91), span("FFL +6.15 m", 500, 340, 92)]
    (artifacts / sheet_registration.EVIDENCE_ARTIFACT).write_text(json.dumps({"evidence": evidence}), "utf-8")
    report = build_height_association_report({"stage": "5.3.3", "project_id": PROJECT, "evidence": evidence})
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(report), "utf-8")
    body = "".join(json.dumps(p) + "\n" for p in PRIMITIVES).encode()
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1.jsonl").write_bytes(body)
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1_manifest.json").write_text(
        json.dumps({"source_pdf_sha256": SHA, "sidecar_sha256": hashlib.sha256(body).hexdigest()}), "utf-8")
    monkeypatch.setattr(column_router, "PROJECTS_DIR", tmp_path)
    sheet_registration._cached_index.cache_clear()
    return folder


def place(folder, top="SSL@-3400mm"):
    ledger = ledger_service.load_ledger(folder, PROJECT)
    recognition = json.loads((folder / "artifacts" / ledger_service.RECOGNITION_ARTIFACT_NAME).read_text("utf-8"))
    report = json.loads((folder / "artifacts" / HEIGHT_ARTIFACT).read_text("utf-8"))
    record = ledger_service.build_placement_record(ledger=ledger, recognition_report=recognition, height_report=report, request={
        "drawing_id": DRAWING, "page_number": 1, "base_level_key": "SSL@-7600mm", "top_level_key": top,
        "reference_point_pdf": [200.0, 200.0], "reference_label": "Grid A/1",
        "reviewer": "Test Reviewer", "justification": "Basement plan at SSL -7.60, next slab SSL -3.40."})
    ledger_service.apply_placement(ledger, record)
    ledger_service.save_ledger(folder, ledger)
    return record


def confirm(column_ids):
    return column_router.confirm_columns(PROJECT, column_router.ColumnConfirmRequest(
        drawing_id=DRAWING, page_number=1, column_ids=column_ids, reviewer="Test Reviewer",
        justification="Column squares on grid intersections, slab to slab."))["confirmation"]


def test_columns_need_a_placed_page_with_storey_height(project):
    view = column_router.get_page_columns(PROJECT, drawing_id=DRAWING, page_number=1)
    assert view["summary"] == {"detected": 4, "confirmed": 0}
    ids = [c["column_id"] for c in view["candidates"]]
    with pytest.raises(HTTPException) as error:
        confirm(ids)
    assert error.value.status_code == 422 and "Place this page" in error.value.detail
    place(project, top=None)
    with pytest.raises(HTTPException):
        confirm(ids)


def test_confirmed_columns_generate_and_reach_the_building_with_slabs(project):
    placement = place(project)
    ids = [c["column_id"] for c in column_router.get_page_columns(PROJECT, drawing_id=DRAWING, page_number=1)["candidates"]]
    record = confirm(ids)
    assert record["height_mm"] == 4200.0 and record["grid_labels"] == ["A/1", "B/1", "A/2", "B/2"]

    with pytest.raises(HTTPException) as conflict:
        confirm(ids[:1])
    assert conflict.value.status_code == 409

    artifact, _ = generation.generate_page_model(project, PROJECT, DRAWING, 1)
    assert artifact["summary"] == {"confirmed_walls": 0, "confirmed_columns": 4, "confirmed_hatch_walls": 0, "meshes_built": 4, "failed": 0}
    assert {e["element_type"] for e in artifact["elements"]} == {"column"}

    model = building_model.build_building_model(project, PROJECT)
    assert model["summary"]["walls_served"] == 4          # columns are served elements too
    zs = {v[2] for e in model["elements"] for v in e["mesh"]["vertices"]}
    assert zs == {-7600.0, -3400.0}
    assert [s["level_key"] for s in model["slabs"]] == ["SSL@-7600mm", "SSL@-3400mm"]
    outline = model["slabs"][0]["outline_mm"]
    xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
    # Columns span A..B (x 200..400 pt) and 1..2 (y 200..500 pt); reference A/1 at (200, 200) pt.
    assert (min(xs), max(xs)) == (-400.0, 10425.0) and (min(ys), max(ys)) == (-400.0, 15425.0)
    assert model["slabs"][0]["thickness_mm"] is None and model["slabs"][0]["approximate"] is True

    # Revoking the placement withholds every column and removes the slabs.
    ledger = ledger_service.load_ledger(project, PROJECT)
    ledger_service.revoke_placement(ledger, placement["placement_id"], reviewer="Test Reviewer",
                                    reason="Wrong base level was chosen for this plan.")
    ledger_service.save_ledger(project, ledger)
    served, withheld = ledger_service.screen_mesh_elements(artifact["elements"], ledger)
    assert served == [] and len(withheld) == 4


def test_column_revoke_route(project):
    place(project)
    ids = [c["column_id"] for c in column_router.get_page_columns(PROJECT, drawing_id=DRAWING, page_number=1)["candidates"]]
    record = confirm(ids[:2])
    column_router.revoke_columns(PROJECT, record["confirmation_id"], column_router.RevokeRequest(
        reviewer="Test Reviewer", reason="Two outlines were stair cores, not columns."))
    view = column_router.get_page_columns(PROJECT, drawing_id=DRAWING, page_number=1)
    assert view["summary"]["confirmed"] == 0
    with pytest.raises(HTTPException) as missing:
        column_router.revoke_columns(PROJECT, "nope", column_router.RevokeRequest(
            reviewer="Test Reviewer", reason="Nothing to revoke here at all."))
    assert missing.value.status_code == 404
