
import copy

import pytest

from app.services.cross_drawing_geometry import (
    analyze_cross_drawing_geometry,
)


def make_candidate(
    bbox=(100.0, 200.0, 300.0, 206.0),
    thickness=6.0,
    overlap=200.0,
):
    return {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": "parallel_horizontal_lines",
        "bbox_pdf_points": list(bbox),
        "estimated_thickness_pdf_points": thickness,
        "overlap_length_pdf_points": overlap,
        "overlap_ratio": 0.95,
        "line_length_ratio": 0.90,
        "confidence": 0.42,
        "verification_required": True,
        "geometric_support": {
            "connected_endpoints_total": 4
        },
        "source": {
            "line_a": [10, 0],
            "line_b": [11, 0],
        },
    }


def make_drawing(drawing_id, candidate):
    return {
        "drawing_id": drawing_id,
        "filename": f"{drawing_id}.pdf",
        "coordinate_system": {
            "unit": "pdf_point",
            "origin": "top_left",
            "x_axis": "right",
            "y_axis": "down",
        },
        "pages": [
            {
                "page_number": 1,
                "page_size_pdf_points": {
                    "width": 2384.0,
                    "height": 1684.0,
                },
                "candidates": [candidate],
            }
        ],
    }


def make_input(drawings=None):
    return {
        "stage": "5.1.6",
        "project_id": "test-project",
        "drawings": drawings or [],
    }


def test_exact_match_across_two_drawings():
    data = make_input([
        make_drawing("drawing-A", make_candidate()),
        make_drawing("drawing-B", make_candidate()),
    ])

    report = analyze_cross_drawing_geometry(data)

    assert report["summary"]["wall_candidate_count"] == 2
    assert report["summary"]["exact_cross_drawing_relationship_count"] == 1

    relationship = report["relationships"][0]

    assert relationship["relationship_type"] == "possible_repeated_geometry"
    assert relationship["match_method"] == "exact_bbox_match"
    assert relationship["member_count"] == 2
    assert relationship["all_measurements_match"] is True


def test_different_bounding_boxes_do_not_match():
    data = make_input([
        make_drawing("drawing-A", make_candidate()),
        make_drawing(
            "drawing-B",
            make_candidate(bbox=(101.0, 200.0, 300.0, 206.0)),
        ),
    ])

    report = analyze_cross_drawing_geometry(data)

    assert report["summary"]["exact_cross_drawing_relationship_count"] == 0


def test_same_drawing_candidates_do_not_create_cross_drawing_group():
    drawing = make_drawing("drawing-A", make_candidate())
    drawing["pages"][0]["candidates"].append(make_candidate())

    report = analyze_cross_drawing_geometry(make_input([drawing]))

    assert report["summary"]["exact_cross_drawing_relationship_count"] == 0


def test_measurement_difference_is_preserved():
    data = make_input([
        make_drawing("drawing-A", make_candidate(thickness=6.0)),
        make_drawing("drawing-B", make_candidate(thickness=7.0)),
    ])

    report = analyze_cross_drawing_geometry(data)

    relationship = report["relationships"][0]

    assert relationship["all_measurements_match"] is False
    assert relationship["measurement_comparisons"][
        "estimated_thickness_pdf_points"
    ]["all_equal"] is False


def test_source_candidates_are_not_modified():
    original = make_input([
        make_drawing("drawing-A", make_candidate()),
        make_drawing("drawing-B", make_candidate()),
    ])

    before = copy.deepcopy(original)

    analyze_cross_drawing_geometry(original)

    assert original == before


def test_relationships_are_never_approved_or_geometry_ready():
    data = make_input([
        make_drawing("drawing-A", make_candidate()),
        make_drawing("drawing-B", make_candidate()),
    ])

    report = analyze_cross_drawing_geometry(data)
    relationship = report["relationships"][0]

    assert relationship["verification_required"] is True
    assert relationship["approval_status"] == "unapproved"
    assert relationship["independent_confirmation"] is False
    assert relationship["physical_continuity_confirmed"] is False
    assert relationship["geometry_generation_allowed"] is False


def test_incompatible_page_sizes_are_not_compared():
    a = make_drawing("drawing-A", make_candidate())
    b = make_drawing("drawing-B", make_candidate())
    b["pages"][0]["page_size_pdf_points"]["width"] = 2000.0

    report = analyze_cross_drawing_geometry(make_input([a, b]))

    assert report["summary"]["exact_cross_drawing_relationship_count"] == 0


def test_wrong_input_stage_is_rejected():
    data = make_input()
    data["stage"] = "5.2.11"

    with pytest.raises(ValueError):
        analyze_cross_drawing_geometry(data)


def test_missing_project_id_is_rejected():
    data = make_input()
    data["project_id"] = None

    with pytest.raises(ValueError):
        analyze_cross_drawing_geometry(data)


def test_invalid_bbox_is_counted():
    data = make_input([
        make_drawing(
            "drawing-A",
            make_candidate(bbox=(100.0, 200.0, 100.0, 206.0)),
        )
    ])

    report = analyze_cross_drawing_geometry(data)

    assert report["summary"]["invalid_bbox_count"] == 1
    assert report["summary"]["exact_cross_drawing_relationship_count"] == 0
