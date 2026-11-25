import json

import pytest
from fastapi import HTTPException

from app.services import bim_geometry_builder as builder
from app.services import height_verification_ledger as ledger_service
from app.services import height_verification_router as router_module
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME,
    build_height_association_report,
)


PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "22222222-2222-4222-8222-222222222222"
OTHER_DRAWING = "33333333-3333-4333-8333-333333333333"
SHA = "f" * 64


def wall(path_a, path_b, y0, thickness=7.0, x0=100.0, x1=200.0, **overrides):
    candidate = {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": "parallel_horizontal_lines",
        "page_number": 1,
        "bbox_pdf_points": [x0, y0, x1, y0 + thickness],
        "confidence": 0.5,
        "verification_required": True,
        "source": {"line_a": [path_a, 0], "line_b": [path_b, 0]},
    }
    candidate.update(overrides)
    return candidate


def recognition():
    return {
        "project_id": PROJECT,
        "drawings": [{
            "drawing_id": DRAWING,
            "filename": "ROOF PLAN.pdf",
            "source_sha256": SHA,
            "pages": [{
                "page_number": 1,
                "page_size_pdf_points": [1000, 800],
                "candidates": [
                    wall(1, 2, 100.0),
                    wall(3, 4, 300.0),
                    wall(1, 5, 100.0, thickness=14.0),
                    {"element_type": "unclassified_parallel_line_pair_candidate",
                     "geometry_type": "parallel_horizontal_lines",
                     "bbox_pdf_points": [0, 0, 5, 5],
                     "verification_required": True,
                     "source": {"line_a": [9, 0], "line_b": [10, 0]}},
                ],
            }],
        }, {
            "drawing_id": OTHER_DRAWING,
            "filename": "MEZZANINE PLAN.pdf",
            "source_sha256": "e" * 64,
            "pages": [{"page_number": 1, "candidates": []}],
        }],
    }


def evidence(text, drawing_id, index):
    return {
        "evidence_id": f"ev-{drawing_id[:4]}-{index}",
        "project_id": PROJECT,
        "drawing_id": drawing_id,
        "page_number": 1,
        "text": text,
        "source_reference": {
            "drawing_id": drawing_id, "page_number": 1, "span_index": index,
            "text": text, "bbox_pdf_points": [1, 1, 2, 2], "source_sha256": SHA,
        },
    }


def height_report():
    return build_height_association_report({
        "stage": "5.3.3",
        "project_id": PROJECT,
        "evidence": [
            evidence("PARAPET 1.2M HIGH", DRAWING, 0),
            evidence("PARAPET 1.2M HIGH", OTHER_DRAWING, 1),
            evidence("PARAPET HEIGHT 900 TO 1100 MM", OTHER_DRAWING, 2),
        ],
    })


def candidate_id(report, height_mm=1200.0):
    for candidate in report["wall_type_height_candidates"]:
        if candidate["height_mm"] == height_mm:
            return candidate["candidate_id"]
    for candidate in report["wall_type_height_candidates"]:
        if candidate["height_mm"] is None and height_mm is None:
            return candidate["candidate_id"]
    raise AssertionError("candidate not found")


def element(path_a, path_b, drawing=DRAWING):
    return f"{drawing}:p1:wall:{path_a}-0:{path_b}-0"


def request(report, **overrides):
    body = {
        "height_candidate_id": candidate_id(report),
        "drawing_id": DRAWING,
        "page_number": 1,
        "element_ids": [element(1, 2)],
        "decision": "confirmed",
        "reviewer": "Test Reviewer",
        "justification": "Roof-plan parapet line pair labelled PARAPET 1.2M HIGH.",
        "cited_evidence_ids": [f"ev-{DRAWING[:4]}-0"],
    }
    body.update(overrides)
    return body


def make_record(ledger=None, report=None, validation=None, **overrides):
    report = report or height_report()
    ledger = ledger or ledger_service.empty_ledger(PROJECT)
    return ledger_service.build_verification_record(
        ledger=ledger,
        recognition_report=recognition(),
        height_report=report,
        height_report_sha256="abc",
        request=request(report, **overrides),
        validation_report=validation,
        now="2026-11-25T00:00:00+00:00",
    )


# ------------------------------------------------------------ pure logic

def test_element_id_matches_builder_format():
    assert ledger_service.wall_element_id(DRAWING, 1, wall(1, 2, 100.0)) == element(1, 2)


def test_confirmed_record_produces_builder_evidence():
    record = make_record()
    evidence = record["builder_height_evidence"]
    assert evidence["height_mm"] == 1200.0
    assert evidence["status"] == "drawing_derived"
    assert record["verification_id"] in evidence["source_reference"]
    assert builder._validate_height_evidence(evidence)[0] == 1200.0
    assert record["source_on_same_drawing"] is True
    assert record["semantic_confirmation"] == {
        "component": "parapet", "basis": "reviewer_confirmed",
    }
    assert record["gates"]["height"] == "passed"


def test_confirmed_evidence_builds_a_closed_wall_mesh():
    ledger = ledger_service.empty_ledger(PROJECT)
    ledger_service.apply_verification(ledger, make_record(ledger=ledger))
    height_evidence = ledger_service.resolve_builder_height_evidence(ledger, element(1, 2))

    mesh = builder.build_wall_mesh(
        recognition_report={"drawing_id": DRAWING, "source_sha256": SHA, "project_id": PROJECT},
        candidate=wall(1, 2, 100.0),
        source_primitives=[
            {"type": "line", "path_index": 1, "item_index": 0, "page_number": 1,
             "start": [100.0, 100.0], "end": [200.0, 100.0]},
            {"type": "line", "path_index": 2, "item_index": 0, "page_number": 1,
             "start": [100.0, 107.0], "end": [200.0, 107.0]},
        ],
        calibration_record={
            "drawing_id": DRAWING, "page_number": 1, "status": "calibrated",
            "source_sha256": SHA, "millimetres_per_pdf_point": 10.0,
            "verification_required": True, "source_reference": "test",
        },
        height_evidence=height_evidence,
    )
    assert mesh["element_id"] == element(1, 2)
    assert mesh["dimensions_mm"]["height"] == 1200.0
    assert mesh["mesh"]["closed_manifold"] is True


def test_rejected_record_produces_no_evidence():
    ledger = ledger_service.empty_ledger(PROJECT)
    ledger_service.apply_verification(ledger, make_record(ledger=ledger, decision="rejected"))
    assert ledger_service.resolve_builder_height_evidence(ledger, element(1, 2)) is None


def test_unverified_element_resolves_to_none():
    ledger = ledger_service.empty_ledger(PROJECT)
    assert ledger_service.resolve_builder_height_evidence(ledger, element(1, 2)) is None


def test_height_range_cannot_be_confirmed():
    report = height_report()
    with pytest.raises(ValueError, match="range"):
        make_record(
            report=report,
            height_candidate_id=candidate_id(report, None),
            cited_evidence_ids=[f"ev-{OTHER_DRAWING[:4]}-2"],
            cross_drawing_justification="Detail sheet range shown for the same parapet type.",
        )


def test_cross_drawing_citation_requires_justification():
    with pytest.raises(ValueError, match="cross_drawing_justification"):
        make_record(cited_evidence_ids=[f"ev-{OTHER_DRAWING[:4]}-1"])

    record = make_record(
        cited_evidence_ids=[f"ev-{OTHER_DRAWING[:4]}-1"],
        cross_drawing_justification="Mezzanine plan labels the same continuous parapet.",
    )
    assert record["source_on_same_drawing"] is False


def test_evidence_from_another_candidate_is_rejected():
    with pytest.raises(ValueError, match="does not belong"):
        make_record(cited_evidence_ids=[f"ev-{OTHER_DRAWING[:4]}-2"])


@pytest.mark.parametrize("overrides,match", [
    ({"element_ids": [element(9, 10)]}, "Not a supported wall"),
    ({"element_ids": [element(5, 6)]}, "Not a supported wall"),
    ({"element_ids": [element(1, 2), element(1, 2)]}, "unique"),
    ({"element_ids": []}, "At least one"),
    ({"justification": "too short"}, "justification"),
    ({"reviewer": " "}, "reviewer"),
    ({"decision": "approved"}, "decision"),
    ({"cited_evidence_ids": []}, "cited evidence"),
    ({"height_candidate_id": "nope"}, "Height candidate"),
    ({"drawing_id": "44444444-4444-4444-8444-444444444444"}, "Drawing"),
    ({"page_number": 2}, "page 2"),
])
def test_invalid_requests_are_rejected(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_record(**overrides)


def test_unvalidated_geometry_cannot_be_confirmed():
    validation = {"results": [{
        "drawing_id": DRAWING, "page_number": 1, "geometry_status": "geometry_invalid",
        "source_references": {"line_a": [1, 0], "line_b": [2, 0]},
    }]}
    with pytest.raises(ValueError, match="validated geometry"):
        make_record(validation=validation)


def test_active_verification_blocks_second_one_until_revoked():
    ledger = ledger_service.empty_ledger(PROJECT)
    first = make_record(ledger=ledger)
    ledger_service.apply_verification(ledger, first)

    with pytest.raises(ledger_service.VerificationConflict):
        make_record(ledger=ledger, element_ids=[element(1, 2), element(3, 4)])

    ledger_service.revoke_verification(
        ledger, first["verification_id"], reviewer="Test Reviewer",
        reason="Line pair is a roof upstand, not the parapet.",
    )
    assert first["status"] == "revoked"
    assert first["revocation"]["reviewer"] == "Test Reviewer"
    assert ledger_service.resolve_builder_height_evidence(ledger, element(1, 2)) is None

    second = make_record(ledger=ledger, element_ids=[element(1, 2), element(3, 4)])
    ledger_service.apply_verification(ledger, second)
    assert len(ledger["verifications"]) == 2
    assert ledger_service.resolve_builder_height_evidence(ledger, element(3, 4))["height_mm"] == 1200.0


def test_revoking_twice_is_a_conflict():
    ledger = ledger_service.empty_ledger(PROJECT)
    record = make_record(ledger=ledger)
    ledger_service.apply_verification(ledger, record)
    ledger_service.revoke_verification(
        ledger, record["verification_id"], reviewer="Test Reviewer",
        reason="Reviewer selected the wrong line pair.",
    )
    with pytest.raises(ledger_service.VerificationConflict):
        ledger_service.revoke_verification(
            ledger, record["verification_id"], reviewer="Test Reviewer",
            reason="Reviewer selected the wrong line pair.",
        )


def test_ledger_from_another_project_is_rejected():
    ledger = ledger_service.empty_ledger("other-project")
    with pytest.raises(ValueError, match="ownership"):
        make_record(ledger=ledger)


def test_page_listing_reports_status_and_provisional_mm():
    ledger = ledger_service.empty_ledger(PROJECT)
    ledger_service.apply_verification(ledger, make_record(ledger=ledger))
    view = ledger_service.list_page_wall_candidates(
        recognition_report=recognition(),
        drawing_id=DRAWING,
        page_number=1,
        ledger=ledger,
        height_report=height_report(),
        calibration_report={"calibrations": {DRAWING: {"1": {
            "status": "calibrated", "millimetres_per_pdf_point": 10.0,
            "verification_required": True,
        }}}},
    )
    assert view["summary"] == {
        "wall_candidates": 3, "height_confirmed": 1, "height_rejected": 0, "unverified": 2,
    }
    first = view["candidates"][0]
    assert first["shares_source_line_with"] == [element(1, 5)]
    assert view["candidates"][1]["shares_source_line_with"] == []
    assert first["length_mm_provisional"] == 1000.0
    assert first["thickness_mm_provisional"] == 70.0
    assert first["height_verification"]["status"] == "confirmed"
    parapet = [c for c in view["height_candidates"] if c["height_mm"] == 1200.0][0]
    assert parapet["occurrences_on_this_drawing"] == 1
    assert parapet["confirmable"] is True


# ---------------------------------------------------------------- router

@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    artifacts = tmp_path / PROJECT / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(
        json.dumps(recognition()), encoding="utf-8")
    (artifacts / OUTPUT_ARTIFACT_NAME).write_text(
        json.dumps(height_report()), encoding="utf-8")
    monkeypatch.setattr(router_module, "PROJECTS_DIR", tmp_path)
    return tmp_path / PROJECT


def payload(**overrides):
    return router_module.HeightVerificationRequest(**request(height_report(), **overrides))


def test_router_create_list_and_revoke(project_dir):
    created = router_module.create_height_verification(PROJECT, payload())
    vid = created["verification"]["verification_id"]

    saved = json.loads(
        (project_dir / "artifacts" / ledger_service.LEDGER_ARTIFACT_NAME).read_text("utf-8"))
    assert [r["verification_id"] for r in saved["verifications"]] == [vid]

    view = router_module.get_page_wall_candidates(PROJECT, drawing_id=DRAWING, page_number=1)
    assert view["summary"]["height_confirmed"] == 1
    assert view["calibration"]["status"] == "not_calibrated"

    with pytest.raises(HTTPException) as conflict:
        router_module.create_height_verification(PROJECT, payload())
    assert conflict.value.status_code == 409

    router_module.revoke_height_verification(PROJECT, vid, router_module.RevokeRequest(
        reviewer="Test Reviewer", reason="Wrong line pair selected for the parapet."))
    ledger = router_module.get_height_verifications(PROJECT)
    assert ledger["verifications"][0]["status"] == "revoked"


def test_router_rejects_bad_ids_and_missing_report(project_dir):
    for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(HTTPException) as error:
            router_module.get_height_verifications(bad)
        assert error.value.status_code == 404

    with pytest.raises(HTTPException) as error:
        router_module.create_height_verification(PROJECT, payload(element_ids=[element(9, 10)]))
    assert error.value.status_code == 422

    (project_dir / "artifacts" / OUTPUT_ARTIFACT_NAME).unlink()
    with pytest.raises(HTTPException) as error:
        router_module.create_height_verification(PROJECT, payload())
    assert error.value.status_code == 409


def test_router_missing_verification_is_404(project_dir):
    with pytest.raises(HTTPException) as error:
        router_module.revoke_height_verification(PROJECT, "missing", router_module.RevokeRequest(
            reviewer="Test Reviewer", reason="Nothing to revoke here at all."))
    assert error.value.status_code == 404


def test_candidates_sharing_a_source_line_cannot_be_confirmed_together():
    with pytest.raises(ValueError, match="share a source line"):
        make_record(element_ids=[element(1, 2), element(1, 5)])


def test_shared_line_with_confirmed_wall_is_a_conflict_but_rejection_is_allowed():
    ledger = ledger_service.empty_ledger(PROJECT)
    ledger_service.apply_verification(ledger, make_record(ledger=ledger))
    with pytest.raises(ledger_service.VerificationConflict, match="already confirmed"):
        make_record(ledger=ledger, element_ids=[element(1, 5)])
    rejected = make_record(ledger=ledger, element_ids=[element(1, 5)], decision="rejected")
    assert rejected["builder_height_evidence"] is None


def test_element_source_lines_parses_builder_ids():
    prefix, lines = ledger_service.element_source_lines(element(106530, 29))
    assert prefix == f"{DRAWING}:p1"
    assert lines == frozenset({(106530, 0), (29, 0)})


# ------------------------------------------------------------ mesh gate

def mesh_element(element_id, height=1200.0, height_status="drawing_derived_requires_verification",
                 evidence_status="drawing_derived"):
    return {
        "element_id": element_id,
        "height_status": height_status,
        "dimensions_mm": {"height": height},
        "provenance": {"height_evidence": {"height_mm": height, "status": evidence_status}},
        "mesh": {"vertices": [], "faces": []},
    }


def confirmed_ledger():
    ledger = ledger_service.empty_ledger(PROJECT)
    ledger_service.apply_verification(ledger, make_record(ledger=ledger))
    return ledger


def test_gate_serves_only_verified_matching_heights():
    served, withheld = ledger_service.screen_mesh_elements(
        [mesh_element(element(1, 2)), mesh_element(element(3, 4))], confirmed_ledger(),
    )
    assert [e["element_id"] for e in served] == [element(1, 2)]
    assert withheld[0]["element_id"] == element(3, 4)
    assert any("No active reviewer-confirmed" in r for r in withheld[0]["reasons"])


def test_gate_withholds_assumed_test_heights():
    # The old project's mesh batch used height_status "assumed_test_value".
    served, withheld = ledger_service.screen_mesh_elements(
        [mesh_element(element(1, 2), height=3000.0, height_status="assumed_test_value",
                      evidence_status="assumed")],
        confirmed_ledger(),
    )
    assert served == []
    reasons = " ".join(withheld[0]["reasons"])
    assert "assumed_test_value" in reasons
    assert "differs from the verified height" in reasons


def test_gate_withholds_height_mismatch_and_revoked_verifications():
    ledger = confirmed_ledger()
    served, withheld = ledger_service.screen_mesh_elements(
        [mesh_element(element(1, 2), height=1500.0)], ledger,
    )
    assert served == [] and "differs" in withheld[0]["reasons"][0]

    record = ledger["verifications"][0]
    ledger_service.revoke_verification(
        ledger, record["verification_id"], reviewer="Test Reviewer",
        reason="Verification revoked during gate testing.",
    )
    served, withheld = ledger_service.screen_mesh_elements([mesh_element(element(1, 2))], ledger)
    assert served == []


def test_gate_withholds_missing_provenance():
    element_without_provenance = mesh_element(element(1, 2))
    del element_without_provenance["provenance"]
    served, withheld = ledger_service.screen_mesh_elements(
        [element_without_provenance], confirmed_ledger(),
    )
    assert served == []
    assert any("drawing-derived height evidence" in r for r in withheld[0]["reasons"])
