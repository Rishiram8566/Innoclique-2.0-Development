import hashlib
import json

import pytest
from fastapi import HTTPException

from app import main as main_module
from app.services import height_verification_ledger as ledger_service
from app.services import wall_mesh_generation as generation
from app.services import wall_mesh_generation_router as router_module
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)


PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "22222222-2222-4222-8222-222222222222"
SHA = "a" * 64
SCALE = 10.0  # mm per PDF point


def wall(path_a, path_b, y0, thickness=7.0):
    return {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": "parallel_horizontal_lines",
        "page_number": 1,
        "bbox_pdf_points": [100.0, y0, 200.0, y0 + thickness],
        "confidence": 0.5,
        "verification_required": True,
        "source": {"line_a": [path_a, 0], "line_b": [path_b, 0]},
    }


def line(path_index, y):
    return {"page_number": 1, "path_index": path_index, "item_index": 0, "type": "line",
            "start": [100.0, y], "end": [200.0, y]}


def element(a, b):
    return f"{DRAWING}:p1:wall:{a}-0:{b}-0"


RECOGNITION = {
    "project_id": PROJECT,
    "drawings": [{
        "drawing_id": DRAWING,
        "filename": "ROOF PLAN.pdf",
        "source_sha256": SHA,
        "pages": [{
            "page_number": 1,
            "page_size_pdf_points": [1000, 800],
            "candidates": [wall(1, 2, 100.0), wall(3, 4, 300.0)],
        }],
    }],
}

CALIBRATION = {"calibrations": {DRAWING: {"1": {
    "status": "calibrated", "drawing_id": DRAWING, "page_number": 1,
    "source_sha256": SHA, "millimetres_per_pdf_point": SCALE,
    "verification_required": True, "source_reference": "test calibration",
    "method": "two_user_supplied_control_points_and_known_distance",
}}}}


def height_report():
    return build_height_association_report({
        "stage": "5.3.3",
        "project_id": PROJECT,
        "evidence": [{
            "evidence_id": "ev-1", "project_id": PROJECT, "drawing_id": DRAWING,
            "page_number": 1, "text": "PARAPET 1.2M HIGH",
            "source_reference": {"drawing_id": DRAWING, "page_number": 1, "span_index": 0,
                                 "text": "PARAPET 1.2M HIGH", "bbox_pdf_points": [1, 1, 2, 2],
                                 "source_sha256": SHA},
        }],
    })


def confirm(folder, element_ids):
    report = json.loads((folder / "artifacts" / HEIGHT_ARTIFACT).read_text("utf-8"))
    ledger = ledger_service.load_ledger(folder, PROJECT)
    record = ledger_service.build_verification_record(
        ledger=ledger,
        recognition_report=RECOGNITION,
        height_report=report,
        height_report_sha256="abc",
        request={
            "height_candidate_id": report["wall_type_height_candidates"][0]["candidate_id"],
            "drawing_id": DRAWING, "page_number": 1, "element_ids": element_ids,
            "decision": "confirmed", "reviewer": "Test Reviewer",
            "justification": "Roof-plan parapet labelled PARAPET 1.2M HIGH.",
            "cited_evidence_ids": ["ev-1"],
        },
    )
    ledger_service.apply_verification(ledger, record)
    ledger_service.save_ledger(folder, ledger)
    return record


def write_sidecar(folder, primitives, *, pdf_sha=SHA, tamper=False):
    sidecars = folder / "artifacts" / "primitive_sidecars"
    sidecars.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(p) + "\n" for p in primitives).encode("utf-8")
    (sidecars / f"{DRAWING}_page_1.jsonl").write_bytes(body)
    digest = hashlib.sha256(body + (b"x" if tamper else b"")).hexdigest()
    (sidecars / f"{DRAWING}_page_1_manifest.json").write_text(json.dumps({
        "source_pdf_sha256": pdf_sha, "sidecar_sha256": digest,
        "primitive_count": len(primitives), "coordinate_system": "original_pdf_page_points",
    }), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT
    artifacts = folder / "artifacts"
    artifacts.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"id": PROJECT, "drawings": [{"id": DRAWING}]}), "utf-8")
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(RECOGNITION), "utf-8")
    (artifacts / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(json.dumps(CALIBRATION), "utf-8")
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(height_report()), "utf-8")
    write_sidecar(folder, [line(1, 100.0), line(2, 107.0), line(3, 300.0), line(4, 307.0), line(9, 5.0)])
    monkeypatch.setattr(router_module, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "PROJECTS_DIR", tmp_path)
    return folder


def served_meshes():
    response = main_module.get_drawing_meshes(PROJECT, DRAWING)
    return json.loads(response.body)


def test_confirmed_wall_becomes_served_metric_mesh(project):
    record = confirm(project, [element(1, 2)])
    result = router_module.generate_verified_wall_model(PROJECT, drawing_id=DRAWING, page_number=1)
    assert result["summary"] == {"confirmed_walls": 1, "meshes_built": 1, "failed": 0}

    artifact = json.loads((project / "artifacts" / "mesh_batches" / f"{DRAWING}_stage5_4.json").read_text("utf-8"))
    mesh = artifact["elements"][0]
    assert mesh["element_id"] == element(1, 2)
    assert mesh["dimensions_mm"] == {"length": 1000.0, "thickness": 70.0, "height": 1200.0,
                                     "volume": 84000000.0}
    assert mesh["mesh"]["closed_manifold"] is True
    assert mesh["height_verification"]["verification_id"] == record["verification_id"]
    assert mesh["component"] == "parapet"
    assert mesh["lod"]["minimum_lod"] == 300
    assert mesh["placement"]["base_status"] == "page_local_zero_level_not_established"
    assert artifact["page"]["size_mm"] == [10000.0, 8000.0]
    assert artifact["coordinate_system"]["shared_building_coordinates_established"] is False

    body = served_meshes()
    assert body["stage"] == "5.4"
    assert body["serving_gate"]["served"] == 1
    assert body["withheld_elements"] == []


def test_unconfirmed_walls_are_not_built(project):
    confirm(project, [element(1, 2)])
    generation.generate_page_model(project, PROJECT, DRAWING, 1)
    body = served_meshes()
    assert [e["element_id"] for e in body["elements"]] == [element(1, 2)]


def test_revoking_withholds_the_built_wall_without_regenerating(project):
    record = confirm(project, [element(1, 2)])
    generation.generate_page_model(project, PROJECT, DRAWING, 1)
    ledger = ledger_service.load_ledger(project, PROJECT)
    ledger_service.revoke_verification(ledger, record["verification_id"], reviewer="Test Reviewer",
                                       reason="Revoked to check the serving gate.")
    ledger_service.save_ledger(project, ledger)
    body = served_meshes()
    assert body["serving_gate"] == {**body["serving_gate"], "served": 0, "withheld": 1}


def test_no_confirmed_walls_blocks_generation(project):
    with pytest.raises(HTTPException) as error:
        router_module.generate_verified_wall_model(PROJECT, drawing_id=DRAWING, page_number=1)
    assert error.value.status_code == 409
    assert "No reviewer-confirmed walls" in error.value.detail


def test_uncalibrated_page_blocks_generation(project):
    confirm(project, [element(1, 2)])
    (project / "artifacts" / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(
        json.dumps({"calibrations": {}}), "utf-8")
    with pytest.raises(generation.GenerationBlocked, match="calibration"):
        generation.generate_page_model(project, PROJECT, DRAWING, 1)


@pytest.mark.parametrize("setup,match", [
    (lambda folder: (folder / "artifacts" / "primitive_sidecars" / f"{DRAWING}_page_1.jsonl").unlink(), "missing"),
    (lambda folder: write_sidecar(folder, [line(1, 100.0), line(2, 107.0)], pdf_sha="b" * 64), "different PDF"),
    (lambda folder: write_sidecar(folder, [line(1, 100.0), line(2, 107.0)], tamper=True), "manifest hash"),
])
def test_untrustworthy_sidecar_blocks_generation(project, setup, match):
    confirm(project, [element(1, 2)])
    setup(project)
    with pytest.raises(generation.GenerationBlocked, match=match):
        generation.generate_page_model(project, PROJECT, DRAWING, 1)


def test_duplicate_source_line_fails_that_wall_only(project):
    confirm(project, [element(1, 2), element(3, 4)])
    write_sidecar(project, [line(1, 100.0), line(1, 100.0), line(2, 107.0), line(3, 300.0), line(4, 307.0)])
    artifact, _ = generation.generate_page_model(project, PROJECT, DRAWING, 1)
    assert [e["element_id"] for e in artifact["elements"]] == [element(3, 4)]
    assert artifact["failed"][0]["element_id"] == element(1, 2)
    assert "exactly one source line" in artifact["failed"][0]["reason"]


def test_bad_ids_are_404(project):
    for project_id, drawing_id in (("nope", DRAWING), (PROJECT, "nope"),
                                   ("33333333-3333-4333-8333-333333333333", DRAWING)):
        with pytest.raises(HTTPException) as error:
            router_module.generate_verified_wall_model(project_id, drawing_id=drawing_id, page_number=1)
        assert error.value.status_code == 404


@pytest.mark.parametrize("value,expected", [
    ({"width": 2384.0, "height": 1684.0}, (2384.0, 1684.0)),   # format used by Stage 5.1.6
    ([1000, 800], (1000.0, 800.0)),
    (None, None),
    ({"width": 0, "height": 10}, None),
    ("bad", None),
])
def test_page_size_accepts_recognition_formats(value, expected):
    assert generation._page_size(value) == expected


def test_dict_page_size_reaches_artifact(project):
    recognition = json.loads(json.dumps(RECOGNITION))
    recognition["drawings"][0]["pages"][0]["page_size_pdf_points"] = {"width": 1000.0, "height": 800.0}
    (project / "artifacts" / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(recognition), "utf-8")
    confirm(project, [element(1, 2)])
    artifact, _ = generation.generate_page_model(project, PROJECT, DRAWING, 1)
    assert artifact["page"]["size_mm"] == [10000.0, 8000.0]
