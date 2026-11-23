
import copy

from app.services.evidence_deduplication import (
    deduplicate_evidence,
)


def make_candidate(
    drawing_id="drawing-1",
    filename="drawing-1.pdf",
    value="+6.05",
    source_text="F.F.L = +6.05",
    bbox=(100, 100, 150, 115),
    span_index=1,
):
    return {
        "schema_version": "0.2.0",
        "stage": "5.2.11",
        "project_id": "test-project",
        "drawing_id": drawing_id,
        "filename": filename,
        "page_number": 1,
        "semantic_type": "finished_floor_level",
        "value_text": value,
        "source": {
            "text": source_text,
            "bbox": list(bbox) if bbox is not None else None,
            "span_index": span_index,
            "block_index": 1,
            "line_index": 1,
            "label_text": "F.F.L",
            "label_bbox": [90, 100, 98, 115],
        },
        "evidence_status": "provisional_candidate",
        "verification_required": True,
        "approval_status": "unapproved",
        "calibration_eligible": False,
        "geometry_generation_allowed": False,
    }


def make_report(candidates):
    return {
        "schema_version": "0.2.0",
        "stage": "5.2.11",
        "project_id": "test-project",
        "generated_at": "2026-11-15T00:00:00Z",
        "candidates": candidates,
    }


def test_cross_drawing_duplicates_are_grouped():
    report = make_report([
        make_candidate("drawing-1", "one.pdf"),
        make_candidate("drawing-2", "two.pdf"),
    ])

    result = deduplicate_evidence(report)

    candidates = result["candidates"]

    assert len(candidates) == 2
    assert candidates[0]["duplicate_group_id"] is not None
    assert (
        candidates[0]["duplicate_group_id"]
        == candidates[1]["duplicate_group_id"]
    )
    assert candidates[0]["duplicate_scope"] == "cross_drawing"
    assert candidates[1]["duplicate_scope"] == "cross_drawing"
    assert result["summary"]["possible_cross_drawing_group_count"] == 1


def test_within_drawing_duplicates_are_grouped():
    report = make_report([
        make_candidate(span_index=1),
        make_candidate(span_index=2),
    ])

    result = deduplicate_evidence(report)

    candidates = result["candidates"]

    assert len(candidates) == 2
    assert candidates[0]["duplicate_group_id"] == (
        candidates[1]["duplicate_group_id"]
    )
    assert candidates[0]["duplicate_scope"] == "within_drawing"
    assert result["summary"]["possible_within_drawing_group_count"] == 1


def test_different_values_are_not_grouped():
    report = make_report([
        make_candidate(value="+6.05"),
        make_candidate(
            drawing_id="drawing-2",
            value="+6.15",
            source_text="F.F.L = +6.15",
        ),
    ])

    result = deduplicate_evidence(report)

    assert all(
        item["duplicate_group_id"] is None
        for item in result["candidates"]
    )


def test_different_bounding_boxes_are_not_grouped():
    report = make_report([
        make_candidate(bbox=(100, 100, 150, 115)),
        make_candidate(
            drawing_id="drawing-2",
            bbox=(200, 200, 250, 215),
        ),
    ])

    result = deduplicate_evidence(report)

    assert all(
        item["duplicate_group_id"] is None
        for item in result["candidates"]
    )


def test_missing_bounding_box_is_not_grouped():
    report = make_report([
        make_candidate(bbox=None),
        make_candidate(
            drawing_id="drawing-2",
            bbox=None,
        ),
    ])

    result = deduplicate_evidence(report)

    assert all(
        item["duplicate_group_id"] is None
        for item in result["candidates"]
    )


def test_all_candidates_are_preserved():
    original = make_report([
        make_candidate("drawing-1"),
        make_candidate("drawing-2"),
        make_candidate("drawing-3", value="+6.15"),
    ])

    result = deduplicate_evidence(original)

    assert len(result["candidates"]) == 3
    assert result["summary"]["input_candidate_count"] == 3
    assert result["summary"]["output_candidate_count"] == 3


def test_source_report_is_not_modified():
    original = make_report([
        make_candidate("drawing-1"),
        make_candidate("drawing-2"),
    ])

    original_copy = copy.deepcopy(original)

    deduplicate_evidence(original)

    assert original == original_copy


def test_no_candidate_is_automatically_approved():
    report = make_report([
        make_candidate("drawing-1"),
        make_candidate("drawing-2"),
    ])

    result = deduplicate_evidence(report)

    for item in result["candidates"]:
        assert item["approval_status"] == "unapproved"
        assert item["verification_required"] is True
        assert item["calibration_eligible"] is False
        assert item["geometry_generation_allowed"] is False


def test_evidence_ids_are_stable():
    report = make_report([
        make_candidate("drawing-1"),
    ])

    first = deduplicate_evidence(report)
    second = deduplicate_evidence(report)

    assert (
        first["candidates"][0]["evidence_id"]
        == second["candidates"][0]["evidence_id"]
    )


def test_invalid_report_is_rejected():
    try:
        deduplicate_evidence({"stage": "5.2.11"})
        assert False, "Expected ValueError"
    except ValueError:
        pass
