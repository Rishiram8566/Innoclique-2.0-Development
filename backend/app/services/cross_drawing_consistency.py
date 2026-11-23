
"""Stage 5.2.16: Cross-drawing candidate consistency analysis.

Read-only analysis of Stage 5.2.13 relationships.
Does not confirm physical identity or generate BIM geometry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


STAGE = "5.2.16"
SCHEMA_VERSION = "0.1.0"
INPUT_STAGE = "5.2.13"


def stable_id(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def classify_measurements(
    comparisons: dict[str, Any],
) -> dict[str, Any]:
    """Classify measurement evidence without assuming physical identity."""

    if not isinstance(comparisons, dict) or not comparisons:
        return {
            "consistency_status": "incomplete",
            "matching_fields": [],
            "differing_fields": [],
            "missing_fields": [],
        }

    matching_fields = []
    differing_fields = []
    missing_fields = []

    for field, comparison in sorted(comparisons.items()):
        if not isinstance(comparison, dict):
            missing_fields.append(field)
            continue

        all_present = comparison.get("all_present")
        all_equal = comparison.get("all_equal")

        if all_present is True and all_equal is True:
            matching_fields.append(field)

        elif all_present is True and all_equal is False:
            differing_fields.append(field)

        else:
            missing_fields.append(field)

    if differing_fields:
        status = "inconsistent"
    elif missing_fields:
        status = "incomplete"
    else:
        status = "consistent"

    return {
        "consistency_status": status,
        "matching_fields": matching_fields,
        "differing_fields": differing_fields,
        "missing_fields": missing_fields,
    }


def analyze_cross_drawing_consistency(
    relationship_data: dict[str, Any],
) -> dict[str, Any]:
    """Analyze consistency of existing cross-drawing relationships."""

    if not isinstance(relationship_data, dict):
        raise ValueError("Input must be a JSON object.")

    if relationship_data.get("stage") != INPUT_STAGE:
        raise ValueError("Expected a Stage 5.2.13 input artifact.")

    project_id = relationship_data.get("project_id")

    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("Missing or invalid project_id.")

    relationships = relationship_data.get("relationships")

    if not isinstance(relationships, list):
        raise ValueError("Input relationships must be a list.")

    results = []

    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise ValueError("Every relationship must be an object.")

        relationship_id = relationship.get("relationship_id")

        if not isinstance(relationship_id, str) or not relationship_id:
            raise ValueError("Relationship is missing relationship_id.")

        comparisons = relationship.get("measurement_comparisons")

        if not isinstance(comparisons, dict):
            comparisons = {}

        classification = classify_measurements(comparisons)

        result_identity = {
            "project_id": project_id,
            "relationship_id": relationship_id,
            "consistency_status": classification["consistency_status"],
        }

        results.append({
            "consistency_id": stable_id(result_identity),
            "relationship_id": relationship_id,
            "relationship_type": relationship.get("relationship_type"),
            "match_method": relationship.get("match_method"),
            "match_scope": relationship.get("match_scope"),
            "drawing_ids": relationship.get("drawing_ids", []),
            "member_count": relationship.get("member_count", 0),
            "members": relationship.get("members", []),
            "bbox_pdf_points": relationship.get("bbox_pdf_points"),
            "page_coordinate_signature": relationship.get(
                "page_coordinate_signature"
            ),
            "measurement_comparisons": comparisons,
            **classification,
            "verification_required": True,
            "approval_status": "unapproved",
            "physical_identity_confirmed": False,
            "independent_confirmation": False,
            "physical_continuity_confirmed": False,
            "geometry_generation_allowed": False,
        })

    results.sort(key=lambda item: item["consistency_id"])

    summary = {
        "input_relationship_count": len(relationships),
        "consistent_relationship_count": sum(
            item["consistency_status"] == "consistent"
            for item in results
        ),
        "inconsistent_relationship_count": sum(
            item["consistency_status"] == "inconsistent"
            for item in results
        ),
        "incomplete_relationship_count": sum(
            item["consistency_status"] == "incomplete"
            for item in results
        ),
        "relationships_with_differences": sum(
            bool(item["differing_fields"])
            for item in results
        ),
        "relationships_with_missing_evidence": sum(
            bool(item["missing_fields"])
            for item in results
        ),
        "approved_relationship_count": 0,
        "physical_identity_confirmed_count": 0,
        "geometry_generation_allowed_count": 0,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stage": INPUT_STAGE,
        "summary": summary,
        "consistency_results": results,
        "policy": (
            "Consistency classifications describe agreement or "
            "disagreement among recorded measurements only. They do not "
            "establish physical identity, physical continuity, independent "
            "confirmation, real-world dimensions, or approved BIM geometry. "
            "PDF-point measurements must not be treated as real-world "
            "measurements while drawing scale remains unresolved."
        ),
    }
