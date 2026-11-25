import hashlib
import json

import pytest
from fastapi import HTTPException

from app.services import height_verification_ledger as ledger_service
from app.services import plan_placement_router as placement_router
from app.services import scale_calibration
from app.services import sheet_registration as registration
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ARTIFACT,
    build_height_association_report,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
A = "22222222-2222-4222-8222-222222222222"   # calibrated, placed
B = "33333333-3333-4333-8333-333333333333"   # same layout as A
C = "44444444-4444-4444-8444-444444444444"   # different layout
SHA = {A: "a" * 64, B: "b" * 64, C: "c" * 64}
SIZE = {"width": 1000.0, "height": 800.0}
GRID = [("A", 100, 50), ("B", 200, 50), ("C", 300, 50), ("D", 400, 50),
        ("1", 50, 100), ("2", 50, 200), ("3", 50, 300), ("4", 50, 400), ("5", 50, 500)]


def span(drawing, text, x, y, index, categories=("unclassified_text",), numeric=None, w=8.0, h=12.0):
    return {"evidence_id": f"{drawing[:2]}-{index}", "project_id": PROJECT, "drawing_id": drawing, "page_number": 1,
            "categories": list(categories), "text": text, "numeric_value": numeric,
            "source_reference": {"drawing_id": drawing, "page_number": 1, "span_index": index, "text": text,
                                 "bbox_pdf_points": [x - w / 2, y - h / 2, x + w / 2, y + h / 2],
                                 "source_sha256": SHA[drawing]}}


def sheet_evidence(drawing, shift=0.0, scale="SCALE 1:200"):
    records = [span(drawing, label, x + shift, y + shift, i) for i, (label, x, y) in enumerate(GRID)]
    records.append(span(drawing, scale, 900, 700, 50, w=40))
    records.append(span(drawing, "250mm THERMAL INSULATED BLOCK WALL", 900, 600, 51, w=120,
                        categories=("possible_wall_thickness",),
                        numeric={"values": [250.0], "unit": "mm", "is_range": False}))
    records.append(span(drawing, "S.S.L = - 7.60", 500, 300, 52, w=40))
    records.append(span(drawing, "FFL +6.15 m", 500, 320, 53, w=40))
    records.append(span(drawing, "S.S.L = - 3.40", 500, 340, 54, w=40))
    return records


EVIDENCE = sheet_evidence(A) + sheet_evidence(B, shift=0.2) + sheet_evidence(C, shift=40.0, scale="SCALE 1:100")


# ------------------------------------------------------------- comparison

def test_registered_sheets_are_recognised():
    index = registration.build_sheet_index(EVIDENCE)
    result = registration.compare_sheets(index, A, B, SIZE, SIZE)
    assert result["registered"] is True
    assert result["matched_grid_labels"] == len(GRID)
    assert result["max_offset_pt"] == pytest.approx(0.283, abs=0.001)
    assert result["common_printed_scales"] == ["1:200"]


@pytest.mark.parametrize("target,sizes,reason", [
    (C, (SIZE, SIZE), "grid labels match"),
    (B, (SIZE, {"width": 1200.0, "height": 800.0}), "sheet sizes differ"),
])
def test_unregistered_sheets_are_rejected(target, sizes, reason):
    index = registration.build_sheet_index(EVIDENCE)
    result = registration.compare_sheets(index, A, target, *sizes)
    assert result["registered"] is False
    assert any(reason in r for r in result["reasons"])


def test_sheet_legend_wall_thickness_is_read():
    index = registration.build_sheet_index(EVIDENCE)
    assert registration.typical_wall_thickness_mm(index, A) == 250.0


# ----------------------------------------------------------- suggestions

def candidate(element_id, thickness, length=5000.0, shares=(), status="geometry_validated", verified="unverified"):
    return {"element_id": element_id, "thickness_mm_provisional": thickness, "length_mm_provisional": length,
            "shares_source_line_with": list(shares), "geometry_validation_status": status,
            "height_verification": {"status": verified}}


def test_suggestions_pick_one_per_group_and_skip_dense_linework():
    walls = [
        candidate("w1", 250.0, shares=["w2", "w3"]),   # 3-line wall: closest to 250 wins
        candidate("w2", 20.0, shares=["w1"]),
        candidate("w3", 230.0, shares=["w1"]),
        candidate("solo", 270.0),
        candidate("short", 250.0, length=300.0),
        candidate("thin", 40.0),                        # too far from 250 mm
    ]
    hatch = [candidate(f"h{i}", 250.0, shares=[f"h{i - 1}"] if i else []) for i in range(5)]
    summary = ledger_service.suggest_walls(walls + hatch, 250.0)
    suggested = {c["element_id"] for c in walls + hatch if c["suggested"]}
    assert suggested == {"w1", "solo"}
    assert summary["skipped_as_dense_linework"] == 5
    assert "dense linework" in hatch[0]["suggestion_note"]


def test_no_suggestions_without_calibration():
    walls = [candidate("w1", None), candidate("w2", None)]
    assert ledger_service.suggest_walls(walls, 250.0)["suggested"] == 0


# ------------------------------------------------- transfer and placement

@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT
    artifacts = folder / "artifacts"
    artifacts.mkdir(parents=True)
    recognition = {"project_id": PROJECT, "drawings": [
        {"drawing_id": d, "filename": f"PLAN-{d[:2]}.pdf", "source_sha256": SHA[d],
         "pages": [{"page_number": 1, "page_size_pdf_points": SIZE, "candidates": []}]} for d in (A, B, C)]}
    (artifacts / ledger_service.RECOGNITION_ARTIFACT_NAME).write_text(json.dumps(recognition), "utf-8")
    (artifacts / registration.EVIDENCE_ARTIFACT).write_text(json.dumps({"evidence": EVIDENCE}), "utf-8")
    (artifacts / HEIGHT_ARTIFACT).write_text(json.dumps(build_height_association_report(
        {"stage": "5.3.3", "project_id": PROJECT, "evidence": EVIDENCE})), "utf-8")
    (artifacts / ledger_service.CALIBRATION_ARTIFACT_NAME).write_text(json.dumps({"calibrations": {A: {"1": {
        "status": "calibrated", "drawing_id": A, "page_number": 1, "source_sha256": SHA[A], "filename": "PLAN-22.pdf",
        "millimetres_per_pdf_point": 70.0, "verification_required": True, "source_reference": "grid 1-2 = 7000 mm",
        "method": "two_user_supplied_control_points_and_known_distance"}}}}), "utf-8")
    for module in (registration, placement_router):
        monkeypatch.setattr(module, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(scale_calibration, "_find_geometry_drawing",
                        lambda f, d: {"drawing_id": d, "filename": f"PLAN-{d[:2]}.pdf"})
    monkeypatch.setattr(scale_calibration, "_current_pdf_sha256", lambda f, d: SHA[d["drawing_id"]])
    registration._cached_index.cache_clear()
    return folder


def transfer(target, **overrides):
    body = {"source_drawing_id": A, "target_drawing_id": target, "reviewer": "Test Reviewer",
            "justification": "Same CAD layout and both sheets print 1:200."}
    body.update(overrides)
    return registration.transfer_scale(PROJECT, registration.ScaleTransferRequest(**body))


def test_scale_transfer_to_registered_sheet(project):
    record = transfer(B)["calibration"]
    assert record["method"] == "transferred_from_registered_sheet"
    assert record["millimetres_per_pdf_point"] == 70.0
    assert record["source_sha256"] == SHA[B]
    assert record["registration"]["registered"] is True
    assert record["verification_required"] is True
    saved = json.loads((project / "artifacts" / ledger_service.CALIBRATION_ARTIFACT_NAME).read_text("utf-8"))
    assert saved["calibrations"][B]["1"]["transferred_from"]["drawing_id"] == A

    with pytest.raises(HTTPException) as again:
        transfer(B)
    assert again.value.status_code == 409
    assert transfer(B, replace_existing=True)["calibration"]["drawing_id"] == B


def test_scale_transfer_refuses_unregistered_sheet(project):
    with pytest.raises(HTTPException) as error:
        transfer(C)
    assert error.value.status_code == 422 and "not registered" in error.value.detail


def test_registration_view_lists_sources(project):
    view = registration.get_sheet_registration(PROJECT, target_drawing_id=B)
    assert view["typical_wall_thickness_mm"] == 250.0
    assert [s["registered"] for s in view["calibrated_sources"]] == [True]


def placement_body(drawing, **overrides):
    body = {"drawing_id": drawing, "page_number": 1, "base_level_key": "SSL@-7600mm", "top_level_key": "SSL@-3400mm",
            "reviewer": "Test Reviewer", "justification": "Levels read from the plan's own annotations."}
    body.update(overrides)
    return placement_router.PlacementRequest(**body)


def test_reference_point_reused_on_registered_sheet(project):
    source = placement_router.create_placement(
        PROJECT, placement_body(A, reference_point_pdf=[100.0, 100.0], reference_label="Grid A/1"))["placement"]
    reused = placement_router.create_placement(
        PROJECT, placement_body(B, reference_from_placement_id=source["placement_id"]))["placement"]
    assert reused["reference_point"]["point_pdf"] == [100.0, 100.0]
    assert reused["reference_point"]["label"] == "Grid A/1"
    assert reused["reference_point"]["transferred_from"]["placement_id"] == source["placement_id"]

    with pytest.raises(HTTPException) as error:
        placement_router.create_placement(PROJECT, placement_body(C, reference_from_placement_id=source["placement_id"]))
    assert error.value.status_code == 422


def test_generate_all_reports_each_placed_plan(project):
    placement_router.create_placement(
        PROJECT, placement_body(A, reference_point_pdf=[100.0, 100.0], reference_label="Grid A/1"))
    result = placement_router.generate_all_placed_plans(PROJECT)
    assert [p["status"] for p in result["plans"]] == ["blocked"]
    assert "No reviewer-confirmed walls" in result["plans"][0]["reason"]
