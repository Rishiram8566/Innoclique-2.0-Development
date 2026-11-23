
import copy

import pytest

from app.services.cross_drawing_consistency import (
    analyze_cross_drawing_consistency,
    classify_measurements,
)


def make_comparison(all_present=True, all_equal=True):
    return {
        "all_present": all_present,
        "all_equal": all_equal,
    }


def make_relationship(relationship_id, comparisons):
    return {
        "relationship_id": relationship_id,
        "relationship_type": "possible_repeated_geometry",
        "match_method": "exact_bbox_match",
        "match_scope": "cross_drawing",
        "drawing_ids": ["DRAWING-A", "DRAWING-B"],
        "member_count": 2,
        "members": [],
        "bbox_pdf_points": [10, 20, 100, 200],
        "page_coordinate_signature": {},
        "measurement_comparisons": comparisons,
    }


def make_input(relationships):
    return {
        "schema_version": "0.1.0",
        "stage": "5.2.13",
        "status": "completed",
        "project_id": "test-project",
        "relationships": relationships,
    }


def test_matching_measurements_are_consistent():
    result = classify_measurements({
        "thickness": make_comparison(True, True),
        "length": make_comparison(True, True),
    })

    assert result["consistency_status"] == "consistent"
    assert len(result["matching_fields"]) == 2
    assert result["differing_fields"] == []
    assert result["missing_fields"] == []


def test_differing_measurements_are_inconsistent():
    result = classify_measurements({
        "thickness": make_comparison(True, True),
        "length": make_comparison(True, False),
    })

    assert result["consistency_status"] == "inconsistent"
    assert result["differing_fields"] == ["length"]


def test_missing_measurement_evidence_is_incomplete():
    result = classify_measurements({
        "thickness": make_comparison(True, True),
        "length": make_comparison(False, False),
    })

    assert result["consistency_status"] == "incomplete"
    assert result["missing_fields"] == ["length"]


def test_differences_take_priority_over_missing_evidence():
    result = classify_measurements({
        "thickness": make_comparison(True, False),
        "length": make_comparison(False, False),
    })

    assert result["consistency_status"] == "inconsistent"
    assert result["differing_fields"] == ["thickness"]
    assert result["missing_fields"] == ["length"]


def test_empty_comparisons_are_incomplete():
    result = classify_measurements({})

    assert result["consistency_status"] == "incomplete"


def test_malformed_comparison_is_incomplete():
    result = classify_measurements({
        "thickness": "invalid",
    })

    assert result["consistency_status"] == "incomplete"
    assert result["missing_fields"] == ["thickness"]


def test_analysis_summary_counts_relationships():
    relationships = [
        make_relationship(
            "relationship-1",
            {"length": make_comparison(True, True)},
        ),
        make_relationship(
            "relationship-2",
            {"length": make_comparison(True, False)},
        ),
        make_relationship(
            "relationship-3",
            {"length": make_comparison(False, False)},
        ),
    ]

    result = analyze_cross_drawing_consistency(
        make_input(relationships)
    )

    summary = result["summary"]

    assert summary["input_relationship_count"] == 3
    assert summary["consistent_relationship_count"] == 1
    assert summary["inconsistent_relationship_count"] == 1
    assert summary["incomplete_relationship_count"] == 1
    assert summary["relationships_with_differences"] == 1
    assert summary["relationships_with_missing_evidence"] == 1


def test_missing_comparison_dictionary_is_incomplete():
    relationship = make_relationship("relationship-1", {})
    relationship.pop("measurement_comparisons")

    result = analyze_cross_drawing_consistency(
        make_input([relationship])
    )

    assert result["consistency_results"][0]["consistency_status"] == "incomplete"


def test_analysis_does_not_approve_or_generate_geometry():
    result = analyze_cross_drawing_consistency(
        make_input([
            make_relationship(
                "relationship-1",
                {"length": make_comparison(True, True)},
            )
        ])
    )

    item = result["consistency_results"][0]

    assert item["verification_required"] is True
    assert item["approval_status"] == "unapproved"
    assert item["physical_identity_confirmed"] is False
    assert item["independent_confirmation"] is False
    assert item["physical_continuity_confirmed"] is False
    assert item["geometry_generation_allowed"] is False

    assert result["summary"]["approved_relationship_count"] == 0
    assert result["summary"]["geometry_generation_allowed_count"] == 0


def test_analysis_does_not_modify_input():
    source = make_input([
        make_relationship(
            "relationship-1",
            {"length": make_comparison(True, True)},
        )
    ])

    original = copy.deepcopy(source)

    analyze_cross_drawing_consistency(source)

    assert source == original


def test_invalid_input_stage_is_rejected():
    source = make_input([])
    source["stage"] = "5.2.15"

    with pytest.raises(ValueError):
        analyze_cross_drawing_consistency(source)


def test_missing_project_id_is_rejected():
    source = make_input([])
    source["project_id"] = ""

    with pytest.raises(ValueError):
        analyze_cross_drawing_consistency(source)


def test_non_list_relationships_are_rejected():
    source = make_input([])
    source["relationships"] = {}

    with pytest.raises(ValueError):
        analyze_cross_drawing_consistency(source)


def test_non_object_relationship_is_rejected():
    source = make_input(["invalid"])

    with pytest.raises(ValueError):
        analyze_cross_drawing_consistency(source)


def test_missing_relationship_id_is_rejected():
    relationship = make_relationship(
        "relationship-1",
        {"length": make_comparison(True, True)},
    )
    relationship.pop("relationship_id")

    with pytest.raises(ValueError):
        analyze_cross_drawing_consistency(make_input([relationship]))


def test_output_is_deterministic():
    relationships = [
        make_relationship(
            "relationship-2",
            {"length": make_comparison(True, True)},
        ),
        make_relationship(
            "relationship-1",
            {"length": make_comparison(True, False)},
        ),
    ]

    source = make_input(relationships)

    first = analyze_cross_drawing_consistency(source)
    second = analyze_cross_drawing_consistency(source)

    assert first == second
