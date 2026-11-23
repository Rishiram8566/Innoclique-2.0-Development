
"""Stage 5.2.13: Read-only cross-drawing geometry relationships.

Exact coordinate matches are recorded as possible repeated geometry.
They are not proof of physical continuity or confirmed BIM elements.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


SCHEMA_VERSION = "0.1.0"
STAGE = "5.2.13"

WALL_TYPE = "wall_geometry_supported_candidate"


def _stable_id(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _rounded_bbox(candidate: dict[str, Any]) -> tuple | None:
    bbox = candidate.get("bbox_pdf_points")

    if not isinstance(bbox, list) or len(bbox) != 4:
        return None

    try:
        values = tuple(round(float(v), 2) for v in bbox)
    except (TypeError, ValueError):
        return None

    if not all(map(lambda v: abs(v) < float("inf"), values)):
        return None

    x1, y1, x2, y2 = values

    if x2 <= x1 or y2 <= y1:
        return None

    return values


def _page_signature(
    drawing: dict[str, Any],
    page: dict[str, Any],
) -> dict[str, Any] | None:
    coordinate_system = drawing.get("coordinate_system")

    page_size = page.get("page_size_pdf_points")

    if not isinstance(coordinate_system, dict):
        return None

    if not isinstance(page_size, dict):
        return None

    width = page_size.get("width")
    height = page_size.get("height")

    if not isinstance(width, (int, float)):
        return None

    if not isinstance(height, (int, float)):
        return None

    if width <= 0 or height <= 0:
        return None

    required_fields = ("unit", "origin", "x_axis", "y_axis")

    if not all(coordinate_system.get(k) for k in required_fields):
        return None

    return {
        "unit": coordinate_system["unit"],
        "origin": coordinate_system["origin"],
        "x_axis": coordinate_system["x_axis"],
        "y_axis": coordinate_system["y_axis"],
        "page_width": round(float(width), 2),
        "page_height": round(float(height), 2),
        "page_number": page.get("page_number"),
    }


def _candidate_reference(
    drawing: dict[str, Any],
    page: dict[str, Any],
    candidate: dict[str, Any],
    candidate_index: int,
) -> dict[str, Any]:
    drawing_id = drawing.get("drawing_id")
    page_number = page.get("page_number")

    identity = {
        "drawing_id": drawing_id,
        "page_number": page_number,
        "candidate_index": candidate_index,
        "element_type": candidate.get("element_type"),
        "bbox_pdf_points": candidate.get("bbox_pdf_points"),
        "source": candidate.get("source"),
    }

    support = candidate.get("geometric_support") or {}

    return {
        "candidate_id": _stable_id(identity),
        "drawing_id": drawing_id,
        "filename": drawing.get("filename"),
        "page_number": page_number,
        "candidate_index": candidate_index,
        "element_type": candidate.get("element_type"),
        "geometry_type": candidate.get("geometry_type"),
        "bbox_pdf_points": candidate.get("bbox_pdf_points"),
        "estimated_thickness_pdf_points": candidate.get(
            "estimated_thickness_pdf_points"
        ),
        "overlap_length_pdf_points": candidate.get(
            "overlap_length_pdf_points"
        ),
        "overlap_ratio": candidate.get("overlap_ratio"),
        "line_length_ratio": candidate.get("line_length_ratio"),
        "connected_endpoints_total": support.get(
            "connected_endpoints_total"
        ),
        "confidence": candidate.get("confidence"),
        "verification_required": candidate.get(
            "verification_required", True
        ),
        "source": candidate.get("source"),
    }


def _compare_measurements(
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    fields = (
        "estimated_thickness_pdf_points",
        "overlap_length_pdf_points",
        "overlap_ratio",
        "line_length_ratio",
        "connected_endpoints_total",
        "geometry_type",
    )

    comparisons = {}

    for field in fields:
        values = [member.get(field) for member in members]

        present = [value for value in values if value is not None]

        comparisons[field] = {
            "values": values,
            "all_present": len(present) == len(values),
            "all_equal": (
                len(present) == len(values)
                and len(set(present)) == 1
            ),
        }

    return comparisons


def analyze_cross_drawing_geometry(
    recognition_data: dict[str, Any],
) -> dict[str, Any]:
    """Analyze exact wall-geometry matches without modifying source data."""

    if not isinstance(recognition_data, dict):
        raise ValueError("Recognition input must be a JSON object.")

    if recognition_data.get("stage") != "5.1.6":
        raise ValueError("Expected a Stage 5.1.6 recognition artifact.")

    project_id = recognition_data.get("project_id")

    if not project_id:
        raise ValueError("Recognition artifact has no project_id.")

    drawings = recognition_data.get("drawings")

    if not isinstance(drawings, list):
        raise ValueError("Recognition artifact drawings must be a list.")

    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)

    drawing_count = 0
    compatible_page_count = 0
    wall_candidate_count = 0
    invalid_bbox_count = 0
    incompatible_page_count = 0

    for drawing in drawings:
        if not isinstance(drawing, dict):
            continue

        drawing_id = drawing.get("drawing_id")

        if not drawing_id:
            continue

        drawing_count += 1

        for page in drawing.get("pages", []):
            if not isinstance(page, dict):
                continue

            signature = _page_signature(drawing, page)

            if signature is None:
                incompatible_page_count += 1
                continue

            compatible_page_count += 1

            for index, candidate in enumerate(
                page.get("candidates", [])
            ):
                if not isinstance(candidate, dict):
                    continue

                if candidate.get("element_type") != WALL_TYPE:
                    continue

                wall_candidate_count += 1

                bbox = _rounded_bbox(candidate)

                if bbox is None:
                    invalid_bbox_count += 1
                    continue

                # The complete page signature prevents comparisons
                # across incompatible page coordinate systems.
                group_key = (
                    tuple(sorted(signature.items())),
                    bbox,
                )

                groups[group_key].append(
                    _candidate_reference(
                        drawing,
                        page,
                        candidate,
                        index,
                    )
                )

    relationships = []

    for group_key, members in sorted(
        groups.items(),
        key=lambda item: str(item[0]),
    ):
        distinct_drawings = sorted({
            member["drawing_id"]
            for member in members
        })

        # Only cross-drawing relationships are reported.
        if len(distinct_drawings) < 2:
            continue

        page_signature, bbox = group_key

        comparisons = _compare_measurements(members)

        relationship_identity = {
            "project_id": project_id,
            "page_signature": page_signature,
            "bbox_pdf_points": bbox,
            "member_ids": sorted(
                member["candidate_id"] for member in members
            ),
        }

        relationships.append({
            "relationship_id": _stable_id(relationship_identity),
            "relationship_type": "possible_repeated_geometry",
            "match_method": "exact_bbox_match",
            "match_scope": "cross_drawing",
            "bbox_pdf_points": list(bbox),
            "page_coordinate_signature": dict(page_signature),
            "drawing_ids": distinct_drawings,
            "member_count": len(members),
            "members": members,
            "measurement_comparisons": comparisons,
            "all_measurements_match": all(
                result["all_equal"]
                for result in comparisons.values()
                if result["all_present"]
            ),
            "verification_required": True,
            "approval_status": "unapproved",
            "independent_confirmation": False,
            "physical_continuity_confirmed": False,
            "geometry_generation_allowed": False,
        })

    relationships.sort(
        key=lambda item: item["relationship_id"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stage": "5.1.6",
        "summary": {
            "drawing_count": drawing_count,
            "compatible_page_count": compatible_page_count,
            "incompatible_page_count": incompatible_page_count,
            "wall_candidate_count": wall_candidate_count,
            "invalid_bbox_count": invalid_bbox_count,
            "exact_cross_drawing_relationship_count": len(
                relationships
            ),
            "relationship_member_count": sum(
                item["member_count"] for item in relationships
            ),
            "approved_relationship_count": 0,
            "independent_confirmation_count": 0,
            "geometry_generation_allowed_count": 0,
        },
        "relationships": relationships,
        "policy": (
            "Exact coordinate matches are possible repeated geometry. "
            "They do not establish physical continuity, independent "
            "confirmation, or approved BIM elements."
        ),
    }
