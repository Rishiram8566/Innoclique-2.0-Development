
import pytest

from app.services.bim_geometry_builder import (
    build_wall_mesh,
    _mesh_is_closed,
)


def fixture(geometry_type):
    if geometry_type == "parallel_horizontal_lines":
        primitives = [
            {
                "type": "line",
                "page_number": 1,
                "path_index": 1,
                "item_index": 0,
                "start": [10, 20],
                "end": [110, 20],
            },
            {
                "type": "line",
                "page_number": 1,
                "path_index": 2,
                "item_index": 0,
                "start": [15, 30],
                "end": [105, 30],
            },
        ]
        bbox = [15, 20, 105, 30]

    else:
        primitives = [
            {
                "type": "line",
                "page_number": 1,
                "path_index": 1,
                "item_index": 0,
                "start": [20, 10],
                "end": [20, 110],
            },
            {
                "type": "line",
                "page_number": 1,
                "path_index": 2,
                "item_index": 0,
                "start": [30, 15],
                "end": [30, 105],
            },
        ]
        bbox = [20, 15, 30, 105]

    recognition = {
        "project_id": "synthetic-project",
        "drawing_id": "synthetic-drawing",
        "source_sha256": "synthetic-sha256",
    }

    candidate = {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": geometry_type,
        "page_number": 1,
        "bbox_pdf_points": bbox,
        "verification_required": True,
        "confidence": 0.7,
        "evidence": ["synthetic test geometry"],
        "source": {
            "line_a": [1, 0],
            "line_b": [2, 0],
        },
    }

    calibration = {
        "drawing_id": "synthetic-drawing",
        "page_number": 1,
        "status": "calibrated",
        "source_sha256": "synthetic-sha256",
        "millimetres_per_pdf_point": 2.0,
        "verification_required": True,
        "source_reference": "synthetic calibration fixture",
    }

    height_evidence = {
        "height_mm": 3000,
        "source_reference": "synthetic drawing height annotation",
        "status": "drawing_derived",
    }

    return (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    )


def build(geometry_type, **overrides):
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture(geometry_type)

    arguments = {
        "recognition_report": recognition,
        "candidate": candidate,
        "source_primitives": primitives,
        "calibration_record": calibration,
        "height_evidence": height_evidence,
    }

    arguments.update(overrides)

    return build_wall_mesh(**arguments)


@pytest.mark.parametrize(
    "geometry_type",
    [
        "parallel_horizontal_lines",
        "parallel_vertical_lines",
    ],
)
def test_builds_closed_wall_mesh(geometry_type):
    mesh = build(geometry_type)

    assert mesh["stage"] == "5.3"
    assert mesh["element_type"] == "wall"

    assert mesh["mesh"]["vertex_count"] == 8
    assert mesh["mesh"]["face_count"] == 12
    assert mesh["mesh"]["closed_manifold"] is True

    assert _mesh_is_closed(
        mesh["mesh"]["vertices"],
        mesh["mesh"]["faces"],
    )

    assert mesh["dimensions_mm"] == {
        "length": 180.0,
        "thickness": 20.0,
        "height": 3000.0,
        "volume": 10800000.0,
    }

    assert mesh["height_status"] == (
        "drawing_derived_requires_verification"
    )

    assert mesh["verification_required"] is True
    assert mesh["approval_status"] == "not_approved"

    assert (
        mesh["coordinate_system"]
        ["shared_building_coordinates_established"]
        is False
    )

    evidence = mesh["provenance"]["height_evidence"]

    assert evidence["height_mm"] == 3000.0
    assert evidence["source_reference"]
    assert evidence["status"] == "drawing_derived"


def test_missing_height_evidence_is_rejected():
    with pytest.raises(
        ValueError,
        match="height evidence is required",
    ):
        build(
            "parallel_vertical_lines",
            height_evidence=None,
        )


@pytest.mark.parametrize(
    "height_evidence",
    [
        {
            "height_mm": 3000,
            "source_reference": "test drawing",
            "status": "assumed",
        },
        {
            "height_mm": 3000,
            "source_reference": "",
            "status": "drawing_derived",
        },
        {
            "height_mm": 0,
            "source_reference": "test drawing",
            "status": "drawing_derived",
        },
        {
            "height_mm": -100,
            "source_reference": "test drawing",
            "status": "drawing_derived",
        },
        {
            "height_mm": "not-a-number",
            "source_reference": "test drawing",
            "status": "drawing_derived",
        },
    ],
)
def test_invalid_height_evidence_is_rejected(height_evidence):
    with pytest.raises(ValueError):
        build(
            "parallel_vertical_lines",
            height_evidence=height_evidence,
        )


def test_candidate_bbox_mismatch_is_rejected():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    candidate["bbox_pdf_points"] = [1, 2, 3, 4]

    with pytest.raises(
        ValueError,
        match="bounding box",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )


def test_source_pdf_hash_mismatch_is_rejected():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    calibration["source_sha256"] = "different-sha256"

    with pytest.raises(
        ValueError,
        match="hashes do not match",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )


def test_candidate_verification_requirement_cannot_be_removed():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    candidate["verification_required"] = False

    with pytest.raises(
        ValueError,
        match="verification status",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )


def test_calibration_must_remain_verification_required():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    calibration["verification_required"] = False

    with pytest.raises(
        ValueError,
        match="verification-required",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )


def test_source_line_from_wrong_page_is_rejected():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    primitives[0]["page_number"] = 2

    with pytest.raises(
        ValueError,
        match="different page",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )


def test_unknown_geometry_type_is_rejected():
    (
        recognition,
        candidate,
        primitives,
        calibration,
        height_evidence,
    ) = fixture("parallel_vertical_lines")

    candidate["geometry_type"] = "unknown_geometry"

    with pytest.raises(
        ValueError,
        match="parallel-line pairs",
    ):
        build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=primitives,
            calibration_record=calibration,
            height_evidence=height_evidence,
        )
