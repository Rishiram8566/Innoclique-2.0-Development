import pytest

from app.services.wall_height_evidence_association import (
    build_height_association_report,
    parse_abbreviation_context,
    parse_component_heights,
    parse_level_annotations,
)


PROJECT = "project-1"


def record(text, drawing_id="d1", page=1, index=0, project_id=PROJECT):
    return {
        "evidence_id": f"{drawing_id}-{page}-{index}",
        "project_id": project_id,
        "drawing_id": drawing_id,
        "page_number": page,
        "categories": ["unclassified_text"],
        "text": text,
        "source_reference": {
            "drawing_id": drawing_id,
            "page_number": page,
            "span_index": index,
            "text": text,
            "bbox_pdf_points": [0, 0, 10, 10],
            "source_sha256": "abc",
        },
        "approval_status": "unapproved",
    }


def report(*records):
    return {
        "stage": "5.3.3",
        "schema_version": "0.2.1",
        "project_id": PROJECT,
        "status": "completed_with_verification_required",
        "evidence": list(records),
    }


# ---------------------------------------------------------------- levels

@pytest.mark.parametrize("text,label,value,unit", [
    ("F.F.L = +0.55", "FFL", 0.55, None),
    ("S.S.L = - 7.60", "SSL", -7.60, None),
    ("S.S.L=+11.55", "SSL", 11.55, None),
    ("FFL=+0.00", "FFL", 0.0, None),
    ("FFL +6.15 m", "FFL", 6.15, "m"),
    ("T.R.L +28.30", "TRL", 28.30, None),
    ("T.R.L= +15.75", "TRL", 15.75, None),
])
def test_level_values_are_parsed(text, label, value, unit):
    levels = parse_level_annotations(text)
    assert len(levels) == 1
    assert levels[0]["label"] == label
    assert levels[0]["value"] == pytest.approx(value)
    assert levels[0]["unit"] == unit
    assert levels[0]["value_status"] == "parsed"


def test_truncated_level_value_is_not_parsed_as_number():
    levels = parse_level_annotations("F.F.L= +6.")
    assert levels[0]["value_status"] == "incomplete_text"
    assert levels[0]["value"] is None


@pytest.mark.parametrize("text", [
    "F.F.L", "BUILDING FFL", "TOP OF PARAPET (T.R.L)", "WORLD 5", "CONTROL 3",
])
def test_labels_without_values_are_not_levels(text):
    assert parse_level_annotations(text) == []


def test_two_levels_in_one_span():
    levels = parse_level_annotations("S.S.L= +0.45 F.F.L = +0.55")
    assert [level["label"] for level in levels] == ["SSL", "FFL"]


def test_abbreviation_context_is_detected():
    hit = parse_abbreviation_context("TOP OF PARAPET (T.R.L)")
    assert hit == {
        "description": "TOP OF PARAPET",
        "abbreviation": "TRL",
        "printed_abbreviation": "T.R.L",
    }


# ------------------------------------------------------ component heights

def test_parapet_height_is_wall_type():
    heights = parse_component_heights("PARAPET 1.2M HIGH")
    assert len(heights) == 1
    assert heights[0]["values_mm"] == [1200.0]
    assert heights[0]["component_class"] == "wall"
    assert heights[0]["components_named"] == {"wall": ["PARAPET"]}


def test_handrail_range_without_component_word_is_unspecified():
    # The KT case: this text belongs to a handrail detail but names no
    # component itself. It must never become a wall height.
    heights = parse_component_heights("HEIGHT 865 TO 965 MM")
    assert len(heights) == 1
    assert heights[0]["values_mm"] == [865.0, 965.0]
    assert heights[0]["is_range"] is True
    assert heights[0]["component_class"] == "unspecified"


def test_handrail_named_in_text_is_railing():
    heights = parse_component_heights("HANDRAIL HEIGHT 900 MM")
    assert heights[0]["component_class"] == "railing"


def test_door_height_without_unit_keeps_unit_unstated():
    heights = parse_component_heights("4500  DOOR OPENING HEIGHT (DH)")
    assert heights[0]["values"] == [4500.0]
    assert heights[0]["unit"] is None
    assert heights[0]["values_mm"] is None
    assert heights[0]["component_class"] == "door"


def test_diameter_and_height_only_takes_height():
    heights = parse_component_heights("(�30m & 26.5m HIGH)")
    assert [h["values_mm"] for h in heights] == [[26500.0]]
    assert heights[0]["component_class"] == "unspecified"


def test_finish_height_is_not_wall():
    heights = parse_component_heights("100mm HIGH PORCELAIN TILES TO MATCH THE FLOOR")
    assert heights[0]["component_class"] == "finish"


def test_wall_tiles_are_ambiguous():
    heights = parse_component_heights("WALL TILES 1200mm HIGH")
    assert heights[0]["component_class"] == "ambiguous"


@pytest.mark.parametrize("text", [
    "HEIGHT",
    "(2) AT UPPER LEVEL, OPENINGS AND FRAME HEIGHT TO BE",
    "TEMPERED DOUBLE GLAZED CONSISTING OF 6mm. THICK HIGH PERFORMANCE",
    "HIGH BUILD SOLVENT FREE - EPOXY COATING",
    "250mm THERMAL INSULATED BLOCK WALL",
])
def test_texts_without_height_measurement_are_ignored(text):
    assert parse_component_heights(text) == []


# ---------------------------------------------------------------- report

def test_report_never_approves_or_allows_generation():
    result = build_height_association_report(report(
        record("PARAPET 1.2M HIGH", index=0),
        record("PARAPET 1.2M HIGH", index=1),
        record("F.F.L = +0.55", index=2),
    ))
    assert result["status"] == "completed_with_verification_required"
    assert result["summary"]["approved_count"] == 0
    assert result["summary"]["geometry_generation_allowed_count"] == 0
    assert result["summary"]["geometry_associations"] == 0
    for item in result["component_heights"]:
        assert item["approval_status"] == "unapproved"
        assert item["geometry_generation_allowed"] is False
        assert item["spatial_proximity_used_as_proof"] is False
    for candidate in result["wall_type_height_candidates"]:
        assert candidate["builder_height_evidence"] is None
        assert candidate["geometry_association"] is None
        assert candidate["approval_status"] == "unapproved"


def test_parapet_occurrences_group_into_one_candidate():
    result = build_height_association_report(report(
        record("PARAPET 1.2M HIGH", drawing_id="a", index=0),
        record("PARAPET 1.2M HIGH", drawing_id="a", index=1),
        record("PARAPET 1.2M HIGH", drawing_id="b", index=0),
    ))
    candidates = result["wall_type_height_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["height_mm"] == 1200.0
    assert candidates[0]["occurrence_count"] == 3
    assert candidates[0]["drawing_count"] == 2
    assert len(candidates[0]["source_evidence_ids"]) == 3


def test_unspecified_height_on_stair_sheet_is_not_wall_candidate():
    result = build_height_association_report(
        report(record("HEIGHT 865 TO 965 MM", drawing_id="stairs")),
        drawing_titles={"stairs": "X-4002_STAIRCASE DETAILS - SHEET 2.pdf"},
    )
    assert result["wall_type_height_candidates"] == []
    item = result["component_heights"][0]
    assert item["wall_height_eligible"] is False
    assert item["not_wall_height_reasons"]
    assert any("stair_or_railing_detail" in w for w in item["warnings"])


def test_wall_word_on_stair_sheet_is_blocked_by_context():
    result = build_height_association_report(
        report(record("WALL 1.2M HIGH", drawing_id="stairs")),
        drawing_titles={"stairs": "STAIRCASE DETAILS"},
    )
    assert result["wall_type_height_candidates"] == []
    assert result["component_heights"][0]["wall_height_eligible"] is False


def test_level_unit_resolved_from_project_annotation():
    result = build_height_association_report(report(
        record("FFL +6.15 m", index=0),
        record("F.F.L= +6.15", index=1),
        record("F.F.L = +0.55", index=2),
    ))
    assert result["level_unit_convention"]["unit"] == "m"
    keys = {level["level_key"]: level for level in result["level_register"]}
    assert keys["FFL@+6150mm"]["occurrence_count"] == 2
    assert keys["FFL@+550mm"]["elevation_mm"] == 550.0
    intervals = result["adjacent_level_intervals"]
    assert len(intervals) == 1
    assert intervals[0]["interval_mm"] == 5600.0
    assert intervals[0]["is_wall_height"] is False


def test_level_unit_unresolved_without_explicit_unit():
    result = build_height_association_report(report(
        record("F.F.L = +0.55", index=0),
        record("S.S.L = +0.45", index=1),
    ))
    assert result["level_unit_convention"]["status"] == "unresolved"
    assert result["level_register"] == []
    assert result["summary"]["level_annotations_unit_unresolved"] == 2


def test_conflicting_explicit_level_units_stay_unresolved():
    result = build_height_association_report(report(
        record("FFL +6.15 m", index=0),
        record("FFL +6150 mm", index=1),
        record("FFL +0.55", index=2),
    ))
    assert result["level_unit_convention"]["status"] == "unresolved"
    keys = {level["level_key"] for level in result["level_register"]}
    assert keys == {"FFL@+6150mm"}
    assert result["summary"]["level_annotations_unit_unresolved"] == 1


def test_intervals_are_per_label_not_mixed():
    result = build_height_association_report(report(
        record("FFL +0.55 m", index=0),
        record("SSL +6.05 m", index=1),
    ))
    assert result["adjacent_level_intervals"] == []


def test_records_from_another_project_are_skipped():
    result = build_height_association_report(report(
        record("PARAPET 1.2M HIGH", project_id="other"),
    ))
    assert result["summary"]["evidence_records_skipped_other_project"] == 1
    assert result["component_heights"] == []


def test_source_reference_is_preserved():
    result = build_height_association_report(report(
        record("PARAPET 1.2M HIGH", drawing_id="dx", page=2, index=7),
    ))
    reference = result["component_heights"][0]["source_reference"]
    assert reference["drawing_id"] == "dx"
    assert reference["page_number"] == 2
    assert reference["span_index"] == 7
    assert reference["evidence_id"] == "dx-2-7"
    assert reference["source_sha256"] == "abc"


@pytest.mark.parametrize("bad", [
    None,
    [],
    {"stage": "5.3.2", "project_id": "p", "evidence": []},
    {"stage": "5.3.3", "evidence": []},
    {"stage": "5.3.3", "project_id": "p", "evidence": {}},
])
def test_invalid_source_reports_are_rejected(bad):
    with pytest.raises(ValueError):
        build_height_association_report(bad)
