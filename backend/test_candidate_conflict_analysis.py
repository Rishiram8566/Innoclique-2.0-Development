
import copy

import pytest

from app.services.candidate_conflict_analysis import (
    analyze_candidate_conflicts,
    _bbox,
    _intersection,
)


def make_candidate(
    line_a,
    line_b,
    bbox,
    geometry="parallel_horizontal_lines",
):
    return {
        "element_type": "wall_geometry_supported_candidate",
        "source": {
            "line_a": line_a,
            "line_b": line_b,
        },
        "bbox_pdf_points": bbox,
        "geometry_type": geometry,
        "estimated_thickness_pdf_points": 6.84,
        "overlap_length_pdf_points": 94.5,
        "confidence": 0.42,
        "verification_required": True,
    }


def make_input(candidates):
    return {
        "stage": "5.1.6",
        "project_id": "test-project",
        "drawings": [
            {
                "drawing_id": "drawing-1",
                "filename": "test-drawing.pdf",
                "pages": [
                    {
                        "page_number": 1,
                        "candidates": candidates,
                    }
                ],
            }
        ],
    }


def test_shared_source_lines_detect_conflict():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["shared_source_pair_count"] == 1
    assert "shared_source_geometry" in result["conflicts"][0]["findings"]


def test_horizontal_vertical_overlap_is_unverified_not_junction():
    data = make_input([
        make_candidate(
            [100, 0], [200, 0],
            [0, 0, 100, 10],
            "parallel_horizontal_lines",
        ),
        make_candidate(
            [300, 0], [400, 0],
            [50, 0, 60, 100],
            "parallel_vertical_lines",
        ),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["orthogonal_bbox_overlap_unverified_count"] == 1
    assert result["summary"]["possible_geometric_junction_count"] == 0

    conflict = result["conflicts"][0]

    assert "orthogonal_bbox_overlap_unverified" in conflict["findings"]
    assert "possible_geometric_junction" not in conflict["findings"]
    assert conflict["physical_intersection_confirmed"] is False
    assert conflict["source_line_intersection_evidence_available"] is False
    assert conflict["geometry_generation_allowed"] is False


def test_parallel_overlap_does_not_create_junction():
    data = make_input([
        make_candidate(
            [100, 0], [200, 0],
            [0, 0, 100, 10],
            "parallel_horizontal_lines",
        ),
        make_candidate(
            [300, 0], [400, 0],
            [50, 0, 150, 10],
            "parallel_horizontal_lines",
        ),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["possible_geometric_junction_count"] == 0
    assert result["summary"]["bbox_overlap_pair_count"] == 1


def test_bbox_overlap_without_shared_source_is_detected():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([300, 0], [400, 0], [50, 0, 150, 10]),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["bbox_overlap_pair_count"] == 1
    assert result["summary"]["shared_source_pair_count"] == 0


def test_bbox_intersection_metrics():
    result = _intersection(
        (0, 0, 100, 10),
        (50, 0, 150, 10),
    )

    assert result["area"] == 500
    assert result["iou"] == pytest.approx(1 / 3)
    assert result["overlap_of_smaller"] == 0.5


def test_containment_is_detected():
    result = _intersection(
        (0, 0, 100, 100),
        (20, 20, 40, 40),
    )

    assert result["area"] == 400
    assert result["overlap_of_smaller"] == 1.0


def test_touching_boxes_have_zero_intersection_area():
    result = _intersection(
        (0, 0, 100, 10),
        (100, 0, 150, 10),
    )

    assert result["area"] == 0
    assert result["touches"] is True


def test_invalid_bbox_is_counted():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 0, 10]),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["invalid_bbox_count"] == 1


def test_bbox_rejects_degenerate_coordinates():
    assert _bbox({
        "bbox_pdf_points": [10, 10, 10, 20]
    }) is None


def test_original_input_is_not_modified():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    original = copy.deepcopy(data)

    analyze_candidate_conflicts(data)

    assert data == original


def test_no_conflict_can_approve_bim_geometry():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    result = analyze_candidate_conflicts(data)

    assert result["summary"]["approved_conflict_count"] == 0
    assert result["summary"]["geometry_generation_allowed_count"] == 0

    for conflict in result["conflicts"]:
        assert conflict["approval_status"] == "unapproved"
        assert conflict["geometry_generation_allowed"] is False
        assert conflict["physical_identity_confirmed"] is False


def test_wrong_input_stage_is_rejected():
    data = make_input([])
    data["stage"] = "5.2.14"

    with pytest.raises(ValueError, match="Stage 5.1.6"):
        analyze_candidate_conflicts(data)


def test_missing_project_id_is_rejected():
    data = make_input([])
    data["project_id"] = None

    with pytest.raises(ValueError, match="project_id"):
        analyze_candidate_conflicts(data)
