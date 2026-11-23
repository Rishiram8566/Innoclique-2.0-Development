
import copy

import pytest

from app.services.source_geometry_relationships import (
    analyze_source_geometry,
    intersection_area,
    bbox_values,
)


def make_candidate(line_a, line_b, bbox):
    return {
        "element_type": "wall_geometry_supported_candidate",
        "source": {
            "line_a": line_a,
            "line_b": line_b,
        },
        "bbox_pdf_points": bbox,
        "geometry_type": "parallel_horizontal_lines",
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


def test_shared_source_lines_are_detected():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    result = analyze_source_geometry(data)

    assert result["summary"]["shared_source_line_pair_count"] == 1
    assert result["summary"]["source_line_component_count"] == 1


def test_bbox_overlap_is_detected():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([300, 0], [400, 0], [50, 0, 150, 10]),
    ])

    result = analyze_source_geometry(data)

    assert result["summary"]["overlapping_candidate_pair_count"] == 1

    relationship = result["pair_relationships"][0]

    assert relationship["relationship_type"] == "bbox_overlap"
    assert relationship["bbox_intersection_area_pdf_points2"] == 500


def test_shared_lines_and_bbox_overlap_are_reported_together():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [50, 0, 150, 10]),
    ])

    result = analyze_source_geometry(data)

    relationship = result["pair_relationships"][0]

    assert relationship["relationship_type"] == (
        "shared_source_lines_and_bbox_overlap"
    )
    assert relationship["shared_source_lines"] == [[100, 0]]
    assert relationship["bbox_intersection_area_pdf_points2"] == 500


def test_three_candidate_shared_line_component():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
        make_candidate([200, 0], [300, 0], [0, 0, 100, 30]),
    ])

    result = analyze_source_geometry(data)

    assert result["summary"]["source_line_component_count"] == 1

    group = result["candidate_groups"][0]

    assert group["member_count"] == 3


def test_invalid_bbox_is_counted():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 0, 10]),
    ])

    result = analyze_source_geometry(data)

    assert result["summary"]["invalid_bbox_count"] == 1


def test_bbox_values_reject_degenerate_boxes():
    assert bbox_values({
        "bbox_pdf_points": [10, 10, 10, 20]
    }) is None


def test_intersection_area_is_correct():
    area = intersection_area(
        (0, 0, 100, 10),
        (50, 0, 150, 10),
    )

    assert area == 500


def test_original_input_is_not_modified():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    original = copy.deepcopy(data)

    analyze_source_geometry(data)

    assert data == original


def test_relationships_never_approve_geometry():
    data = make_input([
        make_candidate([100, 0], [200, 0], [0, 0, 100, 10]),
        make_candidate([100, 0], [300, 0], [0, 0, 100, 20]),
    ])

    result = analyze_source_geometry(data)

    assert result["summary"]["approved_relationship_count"] == 0
    assert result["summary"]["geometry_generation_allowed_count"] == 0

    for relationship in result["pair_relationships"]:
        assert relationship["approval_status"] == "unapproved"
        assert relationship["geometry_generation_allowed"] is False


def test_wrong_input_stage_is_rejected():
    data = make_input([])
    data["stage"] = "5.2.13"

    with pytest.raises(ValueError, match="Stage 5.1.6"):
        analyze_source_geometry(data)


def test_missing_project_id_is_rejected():
    data = make_input([])
    data["project_id"] = None

    with pytest.raises(ValueError, match="project_id"):
        analyze_source_geometry(data)
