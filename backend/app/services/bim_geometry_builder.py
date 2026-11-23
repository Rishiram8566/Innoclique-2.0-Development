
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.1.0"
BUILDER_VERSION = "0.3.0"

SUPPORTED_WALL_CANDIDATE_TYPES = {
    "wall_edge_pair_candidate",
    "wall_geometry_supported_candidate",
}

SUPPORTED_WALL_GEOMETRIES = {
    "parallel_horizontal_lines",
    "parallel_vertical_lines",
}


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric.") from None

    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite.")

    return result


def _validate_height_evidence(height_evidence: dict) -> tuple[float, str]:
    """
    Validate drawing-derived wall height evidence.

    A test height or assumed default is not accepted.
    """

    if not isinstance(height_evidence, dict):
        raise ValueError(
            "Drawing-derived wall height evidence is required."
        )

    height = _finite(
        height_evidence.get("height_mm"),
        "height_evidence.height_mm",
    )

    if height <= 0:
        raise ValueError(
            "Drawing-derived wall height must be positive."
        )

    source_reference = height_evidence.get("source_reference")

    if (
        not isinstance(source_reference, str)
        or not source_reference.strip()
    ):
        raise ValueError(
            "Height evidence requires a non-empty source_reference."
        )

    if height_evidence.get("status") != "drawing_derived":
        raise ValueError(
            "Height evidence status must be drawing_derived."
        )

    return height, source_reference.strip()


def _find_source_line(
    primitives: list[dict],
    path_index: int,
    item_index: int,
) -> dict:
    matches = [
        primitive
        for primitive in primitives
        if primitive.get("path_index") == path_index
        and primitive.get("item_index") == item_index
        and str(primitive.get("type", "")).lower() == "line"
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one source line for "
            f"({path_index}, {item_index}); found {len(matches)}."
        )

    return matches[0]


def _transform_pdf_point(
    point: list | tuple,
    scale: float,
    origin: tuple[float, float] = (0.0, 0.0),
) -> list[float]:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("PDF point must be [x, y].")

    x = _finite(point[0], "PDF x")
    y = _finite(point[1], "PDF y")

    return [
        round((x - origin[0]) * scale, 6),
        round((y - origin[1]) * scale, 6),
    ]


def _mesh_is_closed(vertices: list, faces: list) -> bool:
    edge_counts = Counter()

    for face in faces:
        if len(face) != 3 or len(set(face)) != 3:
            return False

        if any(index < 0 or index >= len(vertices) for index in face):
            return False

        for index in range(3):
            a = face[index]
            b = face[(index + 1) % 3]
            edge_counts[tuple(sorted((a, b)))] += 1

    return bool(edge_counts) and all(
        count == 2 for count in edge_counts.values()
    )


def _line_endpoints(
    line: dict,
    label: str,
) -> tuple[float, float, float, float]:
    start = line.get("start")
    end = line.get("end")

    if (
        not isinstance(start, (list, tuple))
        or len(start) != 2
        or not isinstance(end, (list, tuple))
        or len(end) != 2
    ):
        raise ValueError(f"{label} has invalid endpoints.")

    return (
        _finite(start[0], f"{label} start x"),
        _finite(start[1], f"{label} start y"),
        _finite(end[0], f"{label} end x"),
        _finite(end[1], f"{label} end y"),
    )


def _wall_footprint(
    line_a: dict,
    line_b: dict,
    geometry_type: str,
) -> tuple[float, float, float, float]:
    """Return normalized [x_min, y_min, x_max, y_max] in PDF points."""

    ax1, ay1, ax2, ay2 = _line_endpoints(line_a, "line_a")
    bx1, by1, bx2, by2 = _line_endpoints(line_b, "line_b")

    axis_tolerance = 0.65

    if geometry_type == "parallel_horizontal_lines":
        if (
            abs(ay2 - ay1) > axis_tolerance
            or abs(by2 - by1) > axis_tolerance
        ):
            raise ValueError(
                "Source lines are not sufficiently horizontal."
            )

        a_min, a_max = sorted((ax1, ax2))
        b_min, b_max = sorted((bx1, bx2))

        x_min = max(a_min, b_min)
        x_max = min(a_max, b_max)

        y_a = (ay1 + ay2) / 2.0
        y_b = (by1 + by2) / 2.0

        y_min, y_max = sorted((y_a, y_b))

    elif geometry_type == "parallel_vertical_lines":
        if (
            abs(ax2 - ax1) > axis_tolerance
            or abs(bx2 - bx1) > axis_tolerance
        ):
            raise ValueError(
                "Source lines are not sufficiently vertical."
            )

        a_min, a_max = sorted((ay1, ay2))
        b_min, b_max = sorted((by1, by2))

        y_min = max(a_min, b_min)
        y_max = min(a_max, b_max)

        x_a = (ax1 + ax2) / 2.0
        x_b = (bx1 + bx2) / 2.0

        x_min, x_max = sorted((x_a, x_b))

    else:
        raise ValueError(
            f"Unsupported wall geometry type: {geometry_type!r}."
        )

    if x_max <= x_min or y_max <= y_min:
        raise ValueError(
            "Source lines do not define a valid overlapping wall footprint."
        )

    return x_min, y_min, x_max, y_max


def _validate_candidate_bbox(
    candidate: dict,
    expected_bbox: list[float],
) -> None:
    bbox = candidate.get("bbox_pdf_points")

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(
            "Candidate bounding box must contain four values."
        )

    # Normalize both axes before comparing.
    raw = [
        _finite(value, "candidate bbox")
        for value in bbox
    ]

    normalized = [
        min(raw[0], raw[2]),
        min(raw[1], raw[3]),
        max(raw[0], raw[2]),
        max(raw[1], raw[3]),
    ]

    for actual, expected in zip(normalized, expected_bbox):
        if abs(actual - expected) > 0.65:
            raise ValueError(
                "Source-line geometry does not match candidate bounding box."
            )


def build_wall_mesh(
    *,
    recognition_report: dict,
    candidate: dict,
    source_primitives: list[dict],
    calibration_record: dict,
    height_evidence: dict | None = None,
) -> dict:
    """
    Build a provisional closed wall prism from a recognized parallel-line pair.

    Coordinates are page-local millimetres:
        +X right
        +Y down
        +Z up

    A drawing-derived height evidence object is mandatory.

    This function does not create or approve a native Revit element.
    """

    if not isinstance(recognition_report, dict):
        raise ValueError("recognition_report must be an object.")

    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object.")

    if not isinstance(calibration_record, dict):
        raise ValueError("calibration_record must be an object.")

    if not isinstance(source_primitives, list):
        raise ValueError("source_primitives must be a list.")

    drawing_id = str(recognition_report.get("drawing_id", ""))

    try:
        page_number = int(candidate.get("page_number", 0))
        calibration_page = int(
            calibration_record.get("page_number", 0)
        )
    except (TypeError, ValueError):
        raise ValueError(
            "Page numbers must be valid integers."
        ) from None

    if (
        not drawing_id
        or drawing_id != str(calibration_record.get("drawing_id", ""))
    ):
        raise ValueError(
            "Recognition and calibration drawing IDs do not match."
        )

    if page_number <= 0 or page_number != calibration_page:
        raise ValueError(
            "Candidate and calibration page numbers do not match."
        )

    if calibration_record.get("status") != "calibrated":
        raise ValueError("A calibrated page is required.")

    report_hash = recognition_report.get("source_sha256")
    calibration_hash = calibration_record.get("source_sha256")

    if not report_hash or report_hash != calibration_hash:
        raise ValueError(
            "Recognition and calibration source PDF hashes do not match."
        )

    if candidate.get("element_type") not in SUPPORTED_WALL_CANDIDATE_TYPES:
        raise ValueError(
            "Only recognized wall edge-pair candidates are supported."
        )

    geometry_type = candidate.get("geometry_type")

    if geometry_type not in SUPPORTED_WALL_GEOMETRIES:
        raise ValueError(
            "Only horizontal or vertical parallel-line pairs are supported."
        )

    if candidate.get("verification_required") is not True:
        raise ValueError(
            "Candidate verification status is missing or invalid."
        )

    scale = _finite(
        calibration_record.get("millimetres_per_pdf_point"),
        "millimetres_per_pdf_point",
    )

    if scale <= 0:
        raise ValueError("Calibration scale must be positive.")

    if calibration_record.get("verification_required") is not True:
        raise ValueError(
            "Calibration must remain marked verification-required."
        )

    # Drawing-derived height evidence is mandatory.
    height, height_source_reference = _validate_height_evidence(
        height_evidence
    )

    source = candidate.get("source")

    if not isinstance(source, dict):
        raise ValueError(
            "Candidate source references are missing."
        )

    ref_a = source.get("line_a")
    ref_b = source.get("line_b")

    if not (
        isinstance(ref_a, (list, tuple))
        and len(ref_a) == 2
        and isinstance(ref_b, (list, tuple))
        and len(ref_b) == 2
    ):
        raise ValueError(
            "Candidate must contain line_a and line_b source references."
        )

    try:
        path_a, item_a = int(ref_a[0]), int(ref_a[1])
        path_b, item_b = int(ref_b[0]), int(ref_b[1])
    except (TypeError, ValueError):
        raise ValueError(
            "Source line references must contain integer indices."
        ) from None

    if (path_a, item_a) == (path_b, item_b):
        raise ValueError(
            "Wall candidate must reference two distinct source lines."
        )

    line_a = _find_source_line(
        source_primitives,
        path_a,
        item_a,
    )

    line_b = _find_source_line(
        source_primitives,
        path_b,
        item_b,
    )

    if int(line_a.get("page_number", 0)) != page_number:
        raise ValueError(
            "Source line A belongs to a different page."
        )

    if int(line_b.get("page_number", 0)) != page_number:
        raise ValueError(
            "Source line B belongs to a different page."
        )

    x_min, y_min, x_max, y_max = _wall_footprint(
        line_a,
        line_b,
        geometry_type,
    )

    expected_bbox = [
        x_min,
        y_min,
        x_max,
        y_max,
    ]

    _validate_candidate_bbox(
        candidate,
        expected_bbox,
    )

    p0 = _transform_pdf_point(
        [x_min, y_min],
        scale,
    )

    p1 = _transform_pdf_point(
        [x_max, y_max],
        scale,
    )

    x0, y0 = p0
    x1, y1 = p1

    # Bottom ring followed by top ring.
    # Units are millimetres.
    vertices = [
        [x0, y0, 0.0],
        [x1, y0, 0.0],
        [x1, y1, 0.0],
        [x0, y1, 0.0],
        [x0, y0, height],
        [x1, y0, height],
        [x1, y1, height],
        [x0, y1, height],
    ]

    faces = [
        [0, 2, 1],
        [0, 3, 2],
        [4, 5, 6],
        [4, 6, 7],
        [0, 1, 5],
        [0, 5, 4],
        [1, 2, 6],
        [1, 6, 5],
        [2, 3, 7],
        [2, 7, 6],
        [3, 0, 4],
        [3, 4, 7],
    ]

    # Report semantic wall dimensions independently of orientation.
    if geometry_type == "parallel_horizontal_lines":
        length_mm = round(x1 - x0, 3)
        thickness_mm = round(y1 - y0, 3)
    else:
        length_mm = round(y1 - y0, 3)
        thickness_mm = round(x1 - x0, 3)

    volume_mm3 = round(
        length_mm * thickness_mm * height,
        3,
    )

    if (
        length_mm <= 0
        or thickness_mm <= 0
        or volume_mm3 <= 0
    ):
        raise ValueError(
            "Generated wall dimensions or volume are invalid."
        )

    if not _mesh_is_closed(vertices, faces):
        raise ValueError(
            "Generated mesh is not a closed manifold triangle mesh."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "stage": "5.3",
        "generated_at": datetime.now(timezone.utc).isoformat(),

        "project_id": recognition_report.get("project_id"),
        "drawing_id": drawing_id,
        "page_number": page_number,

        "element_id": (
            f"{drawing_id}:p{page_number}:wall:"
            f"{path_a}-{item_a}:{path_b}-{item_b}"
        ),

        "element_type": "wall",
        "geometry_status": "provisional_mesh",
        "verification_required": True,
        "approval_status": "not_approved",

        "height_status": "drawing_derived_requires_verification",

        "dimensions_mm": {
            "length": length_mm,
            "thickness": thickness_mm,
            "height": round(height, 3),
            "volume": volume_mm3,
        },

        "coordinate_system": {
            "unit": "mm",
            "frame": "page_local",
            "origin_pdf": [0.0, 0.0],
            "x_axis": "right",
            "y_axis": "down",
            "z_axis": "up",
            "shared_building_coordinates_established": False,
        },

        "mesh": {
            "primitive": "triangular_mesh",
            "vertices": vertices,
            "faces": faces,
            "vertex_count": len(vertices),
            "face_count": len(faces),
            "closed_manifold": True,
        },

        "provenance": {
            "source_pdf_sha256": report_hash,
            "calibration_source_reference": calibration_record.get(
                "source_reference"
            ),
            "millimetres_per_pdf_point": scale,

            "candidate_type": candidate.get("element_type"),
            "candidate_geometry_type": geometry_type,
            "candidate_confidence": candidate.get("confidence"),
            "candidate_evidence": candidate.get("evidence", []),

            "source_lines": {
                "line_a": {
                    "path_index": path_a,
                    "item_index": item_a,
                },
                "line_b": {
                    "path_index": path_b,
                    "item_index": item_b,
                },
            },

            "source_bbox_pdf_points": [
                round(value, 6)
                for value in expected_bbox
            ],

            "height_evidence": {
                "height_mm": round(height, 3),
                "source_reference": height_source_reference,
                "status": "drawing_derived",
            },
        },

        "limitations": [
            "Candidate is an unverified geometric hypothesis, not a confirmed wall.",
            "Drawing-derived wall height still requires human verification.",
            "Page-local coordinates do not establish shared building coordinates.",
            "This mesh is not a native Revit wall or an approved BIM element.",
        ],
    }
