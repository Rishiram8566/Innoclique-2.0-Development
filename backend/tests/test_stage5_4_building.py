import hashlib
import json

import pytest
from fastapi import HTTPException

from app.services import building_model
from app.services import height_verification_ledger as ledger_service
from app.services import plan_placement_router as placement_router
from app.services import wall_mesh_generation as generation
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)


PROJECT = "11111111-1111-4111-8111-111111111111"
LOWER = "22222222-2222-4222-8222-222222222222"   # basement lower, SSL -7.60
UPPER = "33333333-3333-4333-8333-333333333333"   # basement upper, SSL -3.40
SHA = {LOWER: "a" * 64, UPPER: "b" * 64}
SCALE = 10.0


def wall(path_a, path_b, y0=100.0, thickness=7.0):
    return {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": "parallel_horizontal_lines",
        "page_number": 1,
        "bbox_pdf_points": [100.0, y0, 200.0, y0 + thickness],
        "confidence": 0.5,
        "verification_required": True,
        "source": {"line_a": [path_a, 0], "line_b": [path_b, 0]},
    }


def element(drawing, a=1, b=2):
    return f"{drawing}:p1:wall:{a}-0:{b}-0"


RECOGNITION = {
    "project_id": PROJECT,
    "drawings": [
        {"drawing_id": drawing, "filename": f"{name}.pdf", "source_sha256": SHA[drawing],
         "pages": [{"page_number": 1, "page_size_pdf_points": {"width": 1000.0, "height": 800.0},
                    "candidates": [wall(1, 2)]}]}
        for drawing, name in ((LOWER, "BASEMENT LOWER"), (UPPER, "BASEMENT UPPER"))
    ],
}


def evidence(text, drawing, index):
    return {
        "evidence_id": f"ev-{drawing[:2]}-{index}", "project_id": PROJECT, "drawing_id": drawing,
        "page_number": 1, "text": text,
        "source_reference": {"drawing_id": drawing, "page_number": 1, "span_index": index, "text": text,
                             "bbox_pdf_points": [1, 1, 2, 2], "source_sha256": SHA[drawing]},
    }


def height_report():
    return build_height_association_report({
        "stage": "5.3.3", "project_id": PROJECT,
        "evidence": [
            evidence("S.S.L = - 7.60", LOWER, 0), evidence("S.S.L = - 7.60", LOWER, 1),
            evidence("S.S.L = - 3.40", UPPER, 0), evidence("S.S.L= +0.45", UPPER, 1),
            evidence("FFL +6.15 m", UPPER, 2),       # explicit unit -> metres convention
            evidence("PARAPET 1.2M HIGH", UPPER, 3),
        ],
    })


def write_sidecar(folder, drawing):
    sidecars = folder / "artifacts" / "primitive_sidecars"
    sidecars.mkdir(parents=True, exist_ok=True)
    rows = [{"page_number": 1, "path_index": p, "item_index": 0, "type": "line",
             "start": [100.0, y], "end": [200.0, y]} for p, y in ((1, 100.0), (2, 107.0))]
    body = "".join(json.dumps(r) + "\n" for r in rows).encode()
    (sidecars / f"{drawing}_page_1.jsonl").write_bytes(body)
    (sidecars / f"{drawing}_page_1_manifest.json").write_text(json.dumps(
        {"source_pdf_sha256": SHA[drawing], "sidecar_sha256": hashlib.sha256(body).hexdigest()}), "utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT
    (folder / "artifacts").mkdir(parents=True)
    artifacts = folder / "artifacts"
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(RECOGNITION), "utf-8")
    (artifacts / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(json.dumps({"calibrations": {
        drawing: {"1": {"status": "calibrated", "drawing_id": drawing, "page_number": 1,
                        "source_sha256": SHA[drawing], "millimetres_per_pdf_point": SCALE,
                        "verification_required": True, "source_reference": "test"}}
        for drawing in (LOWER, UPPER)}}), "utf-8")
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(height_report()), "utf-8")
    for drawing in (LOWER, UPPER):
        write_sidecar(folder, drawing)
    monkeypatch.setattr(placement_router, "PROJECTS_DIR", tmp_path)
    return folder


def placement_request(drawing, base, top, point, **overrides):
    body = {
        "drawing_id": drawing, "page_number": 1, "base_level_key": base, "top_level_key": top,
        "reference_point_pdf": point, "reference_label": "Grid A/1",
        "reviewer": "Test Reviewer", "justification": "Plan level and grid A/1 read from the drawing.",
    }
    body.update(overrides)
    return body


def place(folder, drawing, base, top, point, **overrides):
    ledger = ledger_service.load_ledger(folder, PROJECT)
    record = ledger_service.build_placement_record(
        ledger=ledger, recognition_report=RECOGNITION, height_report=height_report(),
        request=placement_request(drawing, base, top, point, **overrides),
    )
    ledger_service.apply_placement(ledger, record)
    ledger_service.save_ledger(folder, ledger)
    return record


def confirm_full_height(folder, drawing):
    ledger = ledger_service.load_ledger(folder, PROJECT)
    record = ledger_service.build_verification_record(
        ledger=ledger, recognition_report=RECOGNITION, height_report=height_report(),
        height_report_sha256=None,
        request={"height_source": "storey_height", "drawing_id": drawing, "page_number": 1,
                 "element_ids": [element(drawing)], "decision": "confirmed", "reviewer": "Test Reviewer",
                 "justification": "Wall runs slab to slab on the section and plan."},
    )
    ledger_service.apply_verification(ledger, record)
    ledger_service.save_ledger(folder, ledger)
    return record


# ------------------------------------------------------------- placements

def test_placement_options_suggest_slab_level_and_next_slab_up():
    options = ledger_service.placement_options(
        height_report=height_report(), ledger=ledger_service.empty_ledger(PROJECT),
        drawing_id=LOWER, page_number=1,
    )
    assert options["suggested_base_level_key"] == "SSL@-7600mm"
    assert options["suggested_top_level_key"] == "SSL@-3400mm"
    assert options["active_placement"] is None


def test_placement_records_storey_height_and_level_evidence(project):
    record = place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    assert record["storey_height_mm"] == 4200.0
    assert record["levels_on_this_drawing"] is True
    assert {e["evidence_id"] for e in record["level_evidence"]} >= {"ev-22-0", "ev-22-1"}
    assert record["reference_point"] == {"point_pdf": [100.0, 100.0], "label": "Grid A/1",
                                         "snapped_line": None, "transferred_from": None}


@pytest.mark.parametrize("base,top,point,match", [
    ("SSL@-7600mm", "FFL@+6150mm", [100, 100], "same type"),
    ("SSL@-3400mm", "SSL@-7600mm", [100, 100], "above the base"),
    ("SSL@-7600mm", None, [5000, 100], "outside the page"),
    ("SSL@+9999mm", None, [100, 100], "Base level not found"),
])
def test_invalid_placements_are_rejected(project, base, top, point, match):
    with pytest.raises(ValueError, match=match):
        place(project, LOWER, base, top, point)


def test_base_level_from_another_drawing_needs_cross_justification(project):
    with pytest.raises(ValueError, match="cross_drawing_justification"):
        place(project, LOWER, "SSL@+450mm", None, [100, 100])
    record = place(project, LOWER, "SSL@+450mm", None, [100, 100],
                   cross_drawing_justification="Level shown on the upper basement plan only.")
    assert record["levels_on_this_drawing"] is False


def test_one_active_placement_per_page(project):
    first = place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    with pytest.raises(ledger_service.VerificationConflict):
        place(project, LOWER, "SSL@-7600mm", None, [100, 100])
    ledger = ledger_service.load_ledger(project, PROJECT)
    ledger_service.revoke_placement(ledger, first["placement_id"], reviewer="Test Reviewer",
                                    reason="Wrong reference grid intersection picked.")
    ledger_service.save_ledger(project, ledger)
    assert place(project, LOWER, "SSL@-7600mm", None, [100, 100])["storey_height_mm"] is None


# ------------------------------------------------------ storey-height walls

def test_full_height_wall_needs_a_placement_with_top_level(project):
    with pytest.raises(ValueError, match="no plan placement"):
        confirm_full_height(project, LOWER)
    place(project, LOWER, "SSL@-7600mm", None, [100, 100])
    with pytest.raises(ValueError, match="no top level"):
        confirm_full_height(project, LOWER)


def test_full_height_wall_takes_storey_height(project):
    placement = place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    record = confirm_full_height(project, LOWER)
    assert record["height_candidate"]["source"] == "storey_height"
    assert record["height_candidate"]["placement_id"] == placement["placement_id"]
    assert record["semantic_confirmation"]["component"] == "full-height wall"
    ledger = ledger_service.load_ledger(project, PROJECT)
    evidence = ledger_service.resolve_builder_height_evidence(ledger, element(LOWER))
    assert evidence["height_mm"] == 4200.0 and evidence["status"] == "drawing_derived"


def test_revoking_placement_withdraws_storey_height(project):
    placement = place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    confirm_full_height(project, LOWER)
    ledger = ledger_service.load_ledger(project, PROJECT)
    ledger_service.revoke_placement(ledger, placement["placement_id"], reviewer="Test Reviewer",
                                    reason="Base level was read from the wrong plan.")
    assert ledger_service.resolve_builder_height_evidence(ledger, element(LOWER)) is None


def test_page_view_offers_storey_option_only_with_placement(project):
    view = lambda: ledger_service.list_page_wall_candidates(
        recognition_report=RECOGNITION, drawing_id=LOWER, page_number=1,
        ledger=ledger_service.load_ledger(project, PROJECT), height_report=height_report(),
    )
    assert view()["storey_height_option"]["available"] is False
    place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    option = view()["storey_height_option"]
    assert option["available"] is True and option["height_mm"] == 4200.0


# ------------------------------------------------------------ building model

def build_two_storeys(project):
    place(project, LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100])
    place(project, UPPER, "SSL@-3400mm", "SSL@+450mm", [150, 120])
    for drawing in (LOWER, UPPER):
        confirm_full_height(project, drawing)
        generation.generate_page_model(project, PROJECT, drawing, 1)
    return building_model.build_building_model(project, PROJECT)


def test_building_model_stacks_storeys_at_their_levels(project):
    model = build_two_storeys(project)
    assert model["summary"]["plans_in_model"] == 2
    assert model["summary"]["walls_served"] == 2
    assert [s["base_level_key"] for s in model["storeys"]] == ["SSL@-7600mm", "SSL@-3400mm"]

    by_drawing = {e["drawing_id"]: e for e in model["elements"]}
    lower = by_drawing[LOWER]["mesh"]["vertices"]
    upper = by_drawing[UPPER]["mesh"]["vertices"]
    # Lower plan: reference (100, 100) pt -> (1000, 1000) mm; wall x 1000-2000, y 1000-1070.
    assert {v[0] for v in lower} == {0.0, 1000.0} and {v[1] for v in lower} == {0.0, 70.0}
    assert {v[2] for v in lower} == {-7600.0, -3400.0}
    # Upper plan: reference (150, 120) pt -> (1500, 1200) mm; storey 3850 mm from -3400.
    assert {v[0] for v in upper} == {-500.0, 500.0} and {v[1] for v in upper} == {-200.0, -130.0}
    assert {v[2] for v in upper} == {-3400.0, 450.0}


def test_unplaced_and_recalibrated_plans_are_excluded(project):
    build_two_storeys(project)
    ledger = ledger_service.load_ledger(project, PROJECT)
    upper_placement = ledger_service.find_active_placement(ledger, UPPER, 1)
    ledger_service.revoke_placement(ledger, upper_placement["placement_id"], reviewer="Test Reviewer",
                                    reason="Reference point needs to be re-picked.")
    ledger_service.save_ledger(project, ledger)

    calibration_path = project / "artifacts" / ledger_service.CALIBRATION_ARTIFACT_NAME
    calibration = json.loads(calibration_path.read_text("utf-8"))
    calibration["calibrations"][LOWER]["1"]["millimetres_per_pdf_point"] = 12.0
    calibration_path.write_text(json.dumps(calibration), "utf-8")

    model = building_model.build_building_model(project, PROJECT)
    assert model["elements"] == []
    reasons = {e["drawing_id"]: e["reason"] for e in model["excluded_plans"]}
    assert "calibration changed" in reasons[LOWER]
    assert "no placement" in reasons[UPPER]
    titles = {e["drawing_id"]: e["drawing_title"] for e in model["excluded_plans"]}
    assert titles[UPPER] == "BASEMENT UPPER.pdf"


# ------------------------------------------------------------------- router

def test_placement_routes(project):
    body = placement_router.PlacementRequest(**placement_request(LOWER, "SSL@-7600mm", "SSL@-3400mm", [100, 100]))
    created = placement_router.create_placement(PROJECT, body)
    placement_id = created["placement"]["placement_id"]
    with pytest.raises(HTTPException) as conflict:
        placement_router.create_placement(PROJECT, body)
    assert conflict.value.status_code == 409

    options = placement_router.get_placement_options(PROJECT, drawing_id=LOWER, page_number=1)
    assert options["active_placement"]["placement_id"] == placement_id

    placement_router.revoke_placement(PROJECT, placement_id, placement_router.RevokeRequest(
        reviewer="Test Reviewer", reason="Testing the revoke route end to end."))
    assert placement_router.get_placement_options(PROJECT, drawing_id=LOWER, page_number=1)["active_placement"] is None
    assert placement_router.get_building_model(PROJECT)["summary"]["placed_plans"] == 0

    with pytest.raises(HTTPException) as missing:
        placement_router.get_building_model("44444444-4444-4444-8444-444444444444")
    assert missing.value.status_code == 404
