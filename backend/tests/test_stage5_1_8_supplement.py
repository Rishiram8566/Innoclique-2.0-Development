import copy
import hashlib
import json

import pytest

from app import main as main_module
from app.services import bim_geometry_builder as builder
from app.services import engineering_element_recognition as recognizer
from app.services import height_verification_ledger as ledger_service
from app.services import recognition_supplement as supplement
from app.services import wall_mesh_generation as generation
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)


PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "22222222-2222-4222-8222-222222222222"
SHA = "a" * 64


def pair(a, b, bbox, kind="unclassified_parallel_line_pair_candidate", geometry="parallel_vertical_lines"):
    return {"element_type": kind, "geometry_type": geometry, "page_number": 1, "bbox_pdf_points": bbox,
            "confidence": 0.3, "verification_required": True,
            "source": {"line_a": [a, 0], "line_b": [b, 0]}}


def hline(path, y, x0=100.0, x1=200.0):
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": "line", "start": [x0, y], "end": [x1, y]}


def vline(path, x, y0=300.0, y1=500.0):
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": "line", "start": [x, y0], "end": [x, y1]}


EXISTING_WALL = pair(1, 2, [100.0, 100.0, 200.0, 107.0], "wall_geometry_supported_candidate", "parallel_horizontal_lines")
EXISTING_UNCLASSIFIED = pair(3, 4, [100.0, 200.0, 200.0, 207.0], geometry="parallel_horizontal_lines")
NEW_VERTICAL = pair(5, 6, [600.0, 300.0, 607.0, 500.0])
BAD_VERTICAL = pair(7, 8, [700.0, 300.0, 709.0, 500.0])     # bbox does not match its lines

SIDECAR_LINES = [hline(1, 100.0), hline(2, 107.0), hline(3, 200.0), hline(4, 207.0),
                 vline(5, 600.0), vline(6, 607.0), vline(7, 700.0), vline(8, 705.0)]


def base_recognition():
    return {"project_id": PROJECT, "drawings": [{
        "drawing_id": DRAWING, "filename": "ROOF.pdf", "source_sha256": SHA,
        "pages": [{"page_number": 1, "page_size_pdf_points": {"width": 1000.0, "height": 800.0},
                   "candidates": [copy.deepcopy(EXISTING_WALL), copy.deepcopy(EXISTING_UNCLASSIFIED)]}],
    }]}


def fresh_recognition():
    return {"drawing_id": DRAWING, "source_sha256": SHA, "recognition_version": "fixed",
            "pages": [{"page_number": 1, "candidates": [
                copy.deepcopy(EXISTING_WALL), copy.deepcopy(NEW_VERTICAL), copy.deepcopy(BAD_VERTICAL),
                {"element_type": "column_candidate", "geometry_type": "rectangle", "source": {}},
            ]}]}


def lines_by_key(page_number, wanted):
    found = {key: [] for key in wanted}
    for line in SIDECAR_LINES:
        key = (line["path_index"], line["item_index"])
        if key in found:
            found[key].append(line)
    return found


def build_entry():
    return supplement.build_drawing_supplement(
        base_drawing=base_recognition()["drawings"][0], fresh_drawing=fresh_recognition(),
        line_loader=lines_by_key,
    )


# ------------------------------------------------------------- pure logic

def test_supplement_adds_only_new_pairs_and_checks_their_geometry():
    entry = build_entry()
    page = entry["pages"][0]
    added = {tuple(c["source"]["line_a"]): c for c in page["added_candidates"]}
    assert set(added) == {(5, 0), (7, 0)}                      # existing wall and the column are skipped
    assert added[(5, 0)]["geometry_validation_status"] == "geometry_validated"
    assert added[(7, 0)]["geometry_validation_status"].startswith("geometry_invalid")
    assert added[(5, 0)]["recognition_source"] == "stage5_1_8_supplement"
    assert entry["summary"]["added_by_orientation"] == {"horizontal": 0, "vertical": 2}
    assert page["added_geometry_validated"] == 1


def test_existing_unclassified_pairs_get_a_geometry_check():
    validation = build_entry()["pages"][0]["existing_candidate_validation"]
    assert validation == {f"{DRAWING}:p1:wall:3-0:4-0": "geometry_validated"}


def test_missing_sidecar_lines_are_reported_not_guessed():
    entry = supplement.build_drawing_supplement(
        base_drawing=base_recognition()["drawings"][0], fresh_drawing=fresh_recognition(),
        line_loader=lambda page, wanted: {},
    )
    statuses = {c["geometry_validation_status"] for c in entry["pages"][0]["added_candidates"]}
    assert statuses == {"source_line_missing_or_duplicated"}


def test_changed_pdf_is_rejected():
    fresh = fresh_recognition()
    fresh["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="PDF changed"):
        supplement.build_drawing_supplement(
            base_drawing=base_recognition()["drawings"][0], fresh_drawing=fresh, line_loader=lines_by_key,
        )


def test_merge_adds_candidates_without_modifying_the_base_report():
    base = base_recognition()
    snapshot = json.dumps(base, sort_keys=True)
    report = {"project_id": PROJECT, "drawings": [build_entry()]}
    merged = supplement.merge_supplement(base, report)
    assert json.dumps(base, sort_keys=True) == snapshot
    candidates = merged["drawings"][0]["pages"][0]["candidates"]
    assert len(candidates) == 4
    assert candidates[1]["geometry_validation_status"] == "geometry_validated"   # existing unclassified
    assert candidates[0].get("geometry_validation_status") is None              # existing wall untouched


@pytest.mark.parametrize("change", ["other_project", "other_pdf"])
def test_merge_ignores_mismatched_supplements(change):
    entry = build_entry()
    report = {"project_id": PROJECT, "drawings": [entry]}
    if change == "other_project":
        report["project_id"] = "other"
    else:
        entry["source_sha256"] = "b" * 64
    merged = supplement.merge_supplement(base_recognition(), report)
    assert len(merged["drawings"][0]["pages"][0]["candidates"]) == 2


def test_builder_needs_reviewer_flag_for_unclassified_pairs():
    kwargs = dict(
        recognition_report={"drawing_id": DRAWING, "source_sha256": SHA, "project_id": PROJECT},
        source_primitives=[vline(5, 600.0), vline(6, 607.0)],
        calibration_record={"drawing_id": DRAWING, "page_number": 1, "status": "calibrated", "source_sha256": SHA,
                            "millimetres_per_pdf_point": 10.0, "verification_required": True},
        height_evidence={"height_mm": 1200.0, "source_reference": "test", "status": "drawing_derived"},
    )
    with pytest.raises(ValueError, match="reviewer confirmed"):
        builder.build_wall_mesh(candidate=copy.deepcopy(NEW_VERTICAL), **kwargs)
    mesh = builder.build_wall_mesh(candidate={**NEW_VERTICAL, "reviewer_confirmed_wall": True}, **kwargs)
    assert mesh["dimensions_mm"] == {"length": 2000.0, "thickness": 70.0, "height": 1200.0, "volume": 168000000.0}


# ------------------------------------------------------------ end to end

@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT
    artifacts = folder / "artifacts"
    (artifacts / "primitive_sidecars").mkdir(parents=True)
    (folder / "drawings").mkdir()
    (folder / "drawings" / f"{DRAWING}.pdf").write_bytes(b"%PDF-1.4 test")
    (folder / "project.json").write_text(json.dumps({"id": PROJECT, "drawings": [
        {"id": DRAWING, "filename": "ROOF.pdf", "stored_filename": f"{DRAWING}.pdf"}]}), "utf-8")
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(base_recognition()), "utf-8")
    (artifacts / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(json.dumps({"calibrations": {DRAWING: {"1": {
        "status": "calibrated", "drawing_id": DRAWING, "page_number": 1, "source_sha256": SHA,
        "millimetres_per_pdf_point": 10.0, "verification_required": True, "source_reference": "test"}}}}), "utf-8")
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(build_height_association_report({
        "stage": "5.3.3", "project_id": PROJECT, "evidence": [{
            "evidence_id": "ev-1", "project_id": PROJECT, "drawing_id": DRAWING, "page_number": 1,
            "text": "PARAPET 1.2M HIGH", "source_reference": {"drawing_id": DRAWING, "page_number": 1,
                                                              "span_index": 0, "text": "PARAPET 1.2M HIGH",
                                                              "bbox_pdf_points": [1, 1, 2, 2], "source_sha256": SHA}}]})), "utf-8")
    body = "".join(json.dumps(line) + "\n" for line in SIDECAR_LINES).encode()
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1.jsonl").write_bytes(body)
    (artifacts / "primitive_sidecars" / f"{DRAWING}_page_1_manifest.json").write_text(json.dumps(
        {"source_pdf_sha256": SHA, "sidecar_sha256": hashlib.sha256(body).hexdigest()}), "utf-8")
    monkeypatch.setattr(recognizer, "recognize_pdf_elements", lambda pdf, record: fresh_recognition())
    monkeypatch.setattr(supplement, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "PROJECTS_DIR", tmp_path)
    return folder


def test_supplement_route_then_confirm_build_and_serve_a_new_vertical_wall(project):
    result = supplement.create_supplement(PROJECT, supplement.SupplementRequest(drawing_ids=[DRAWING]))
    assert result["drawings"][0]["added_candidates"] == 2

    merged = supplement.read_recognition(project)
    view = ledger_service.list_page_wall_candidates(
        recognition_report=merged, drawing_id=DRAWING, page_number=1,
        ledger=ledger_service.load_ledger(project, PROJECT),
    )
    by_id = {c["element_id"]: c for c in view["candidates"]}
    new_id = f"{DRAWING}:p1:wall:5-0:6-0"
    assert by_id[new_id]["recognition_source"] == "stage5_1_8_supplement"
    assert by_id[new_id]["geometry_validation_status"] == "geometry_validated"

    report = json.loads((project / "artifacts" / HEIGHT_ARTIFACT).read_text("utf-8"))
    ledger = ledger_service.load_ledger(project, PROJECT)
    bad_request = {
        "height_candidate_id": report["wall_type_height_candidates"][0]["candidate_id"],
        "drawing_id": DRAWING, "page_number": 1, "element_ids": [f"{DRAWING}:p1:wall:7-0:8-0"],
        "decision": "confirmed", "reviewer": "Test Reviewer",
        "justification": "Vertical side parapet found by the fixed recogniser.",
        "cited_evidence_ids": ["ev-1"],
    }
    with pytest.raises(ValueError, match="validated geometry"):
        ledger_service.build_verification_record(
            ledger=ledger, recognition_report=merged, height_report=report,
            height_report_sha256=None, request=bad_request)
    record = ledger_service.build_verification_record(
        ledger=ledger, recognition_report=merged, height_report=report, height_report_sha256=None,
        request={**bad_request, "element_ids": [new_id]})
    ledger_service.apply_verification(ledger, record)
    ledger_service.save_ledger(project, ledger)

    artifact, _ = generation.generate_page_model(project, PROJECT, DRAWING, 1)
    assert artifact["summary"] == {"confirmed_walls": 1, "confirmed_columns": 0, "meshes_built": 1, "failed": 0}
    assert artifact["elements"][0]["provenance"]["candidate_type"] == "unclassified_parallel_line_pair_candidate"

    served = json.loads(main_module.get_drawing_meshes(PROJECT, DRAWING).body)
    assert served["serving_gate"]["served"] == 1
