
import pytest

from app.services.geometry_source_evidence import (
    _classify_text,
    _extract_numeric_value,
    build_geometry_source_evidence_report,
)


def make_inputs(text, bbox=None, candidate_bbox=None):
    bbox = bbox or [100, 100, 150, 115]

    recognition = {
        "project_id": "test-project",
        "drawings": [{
            "drawing_id": "drawing-1",
            "source_sha256": "abc123",
            "pages": [{
                "page_number": 1,
                "page_size_pdf_points": [1000, 800],
                "candidates": (
                    [{
                        "element_type": "wall_geometry_supported_candidate",
                        "bbox_pdf_points": candidate_bbox,
                    }]
                    if candidate_bbox else []
                ),
            }],
        }],
    }

    geometry = {
        "drawings": [{
            "drawing_id": "drawing-1",
            "source_sha256": "abc123",
            "pages": [{
                "page_number": 1,
                "page_size_pdf_points": [1000, 800],
                "text_spans": [{
                    "text": text,
                    "bbox": bbox,
                }],
            }],
        }],
    }

    return recognition, geometry


def test_dotted_floor_level_is_classified():
    categories = _classify_text("F.F.L = -3.40")
    assert "level_annotation" in categories


def test_ssl_level_is_classified():
    categories = _classify_text("S.S.L = -3.40")
    assert "level_annotation" in categories


def test_wall_thickness_is_not_height():
    categories = _classify_text(
        "250mm THERMAL INSULATED BLOCK WALL"
    )

    assert "possible_wall_thickness" in categories
    assert "wall_height_annotation" not in categories
    assert "component_height_annotation" not in categories


def test_explicit_wall_height_is_detected():
    categories = _classify_text("WALL HEIGHT 3000mm")

    assert "wall_height_annotation" in categories
    assert "possible_wall_thickness" not in categories


def test_component_height_is_not_wall_height():
    categories = _classify_text("HEIGHT 865 TO 965 MM")

    assert "component_height_annotation" in categories
    assert "wall_height_annotation" not in categories


def test_railing_height_is_not_wall_height():
    categories = _classify_text("HANDRAIL HEIGHT 900mm")

    assert "railing_height_annotation" in categories
    assert "wall_height_annotation" not in categories


def test_guardrail_height_is_not_wall_height():
    categories = _classify_text("GUARDRAIL HEIGHT 900mm")

    assert "railing_height_annotation" in categories
    assert "wall_height_annotation" not in categories


def test_height_range_preserves_both_values():
    result = _extract_numeric_value("HEIGHT 865 TO 965 MM")

    assert result == {
        "values": [865.0, 965.0],
        "unit": "mm",
        "is_range": True,
    }


def test_single_height_preserves_value():
    result = _extract_numeric_value("WALL HEIGHT 3000mm")

    assert result == {
        "values": [3000.0],
        "unit": "mm",
        "is_range": False,
    }


def test_door_opening_height_without_unit_preserves_unspecified_unit():
    result = _extract_numeric_value(
        "4500 DOOR OPENING HEIGHT (DH)"
    )

    assert result == {
        "values": [4500.0],
        "unit": None,
        "is_range": False,
    }


def test_second_door_opening_height_without_unit():
    result = _extract_numeric_value(
        "3000 DOOR OPENING HEIGHT (DH)"
    )

    assert result == {
        "values": [3000.0],
        "unit": None,
        "is_range": False,
    }


def test_stair_annotation_is_detected():
    categories = _classify_text("STAIRCASE TO GROUND FLOOR")
    assert "stair_annotation" in categories


def test_material_annotation_is_detected():
    categories = _classify_text("REINFORCED CONCRETE WALL")
    assert "material_specification" in categories


def test_proximity_is_not_confirmed_association():
    recognition, geometry = make_inputs(
        "WALL HEIGHT 3000mm",
        bbox=[100, 100, 150, 115],
        candidate_bbox=[155, 100, 200, 115],
    )

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    record = report["evidence"][0]

    assert len(record["spatial_suggestions"]) == 1

    assert (
        record["spatial_suggestions"][0]["relationship_status"]
        == "proximity_only_not_association"
    )

    assert record["spatial_proximity_used_as_proof"] is False
    assert record["geometry_generation_allowed"] is False


def test_height_candidate_is_not_automatically_approved():
    recognition, geometry = make_inputs("WALL HEIGHT 3000mm")

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    record = report["evidence"][0]

    assert record["height_evidence_candidate"] is True
    assert record["builder_height_evidence"] is None
    assert record["approval_status"] == "unapproved"
    assert record["geometry_generation_allowed"] is False


def test_component_height_cannot_be_wall_height_candidate():
    recognition, geometry = make_inputs("HEIGHT 865 TO 965 MM")

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    record = report["evidence"][0]

    assert record["height_evidence_candidate"] is False
    assert record["builder_height_evidence"] is None
    assert record["geometry_generation_allowed"] is False


def test_railing_height_cannot_be_wall_height_candidate():
    recognition, geometry = make_inputs("HANDRAIL HEIGHT 900mm")

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    record = report["evidence"][0]

    assert record["height_evidence_candidate"] is False
    assert record["geometry_generation_allowed"] is False


def test_source_hash_mismatch_skips_drawing():
    recognition, geometry = make_inputs("WALL HEIGHT 3000mm")

    geometry["drawings"][0]["source_sha256"] = "different-hash"

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    assert report["summary"]["evidence_count"] == 0
    assert report["summary"]["source_hash_mismatches"] == 1


def test_project_mismatch_is_rejected():
    recognition, geometry = make_inputs("F.F.L = -3.40")

    geometry["project_id"] = "another-project"

    with pytest.raises(ValueError, match="Project ID mismatch"):
        build_geometry_source_evidence_report(
            recognition,
            geometry,
        )


def test_invalid_spatial_threshold_is_rejected():
    recognition, geometry = make_inputs("F.F.L = -3.40")

    with pytest.raises(ValueError):
        build_geometry_source_evidence_report(
            recognition,
            geometry,
            max_spatial_distance_pdf_points=-1,
        )


def test_page_size_mismatch_skips_page():
    recognition, geometry = make_inputs("F.F.L = -3.40")

    geometry["drawings"][0]["pages"][0]["page_size_pdf_points"] = [
        900, 700
    ]

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    assert report["summary"]["evidence_count"] == 0
    assert report["summary"]["skipped_pages"] == 1


def test_report_never_approves_geometry():
    recognition, geometry = make_inputs("WALL HEIGHT 3000mm")

    report = build_geometry_source_evidence_report(
        recognition,
        geometry,
    )

    assert report["summary"]["approved_count"] == 0
    assert report["summary"]["geometry_generation_allowed_count"] == 0

    for record in report["evidence"]:
        assert record["approval_status"] == "unapproved"
        assert record["geometry_generation_allowed"] is False
