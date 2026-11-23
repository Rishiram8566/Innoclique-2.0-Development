
"""Stage 5.2.17: Evidence-to-geometry association.

Conservative, source-traceable association analysis.

Only explicit candidate-to-evidence ID references can produce a direct
association. Spatial proximity and filename similarity are not proof.
No BIM geometry is generated or approved by this service.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


STAGE = "5.2.17"
SCHEMA_VERSION = "0.1.0"
RECOGNITION_STAGE = "5.1.6"
EVIDENCE_STAGE = "5.2.12"


# These are explicit semantic compatibility rules, not fuzzy text matches.
# Extend only when a corresponding evidence type is actually supported.
SEMANTIC_COMPATIBILITY = {
    "wall_geometry_supported_candidate": {
        "wall_thickness_dimension",
        "wall_height_dimension",
        "wall_length_dimension",
    },
    "stair_boundary_candidate": {
        "stair_rise_dimension",
        "stair_run_dimension",
        "stair_height_dimension",
    },
    "equipment_outline_candidate": {
        "equipment_width_dimension",
        "equipment_height_dimension",
        "equipment_length_dimension",
    },
}


def stable_id(value: Any) -> str:
    """Create a deterministic identifier from JSON-compatible data."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_evidence_ids(candidate: dict[str, Any]) -> set[str]:
    """Read only explicit evidence-ID reference fields."""
    result: set[str] = set()

    for field in ("evidence_ids", "supported_evidence_ids"):
        values = candidate.get(field)

        if isinstance(values, list):
            result.update(
                value.strip()
                for value in values
                if _valid_identifier(value)
            )

    return result


def _semantic_compatibility(
    element_type: Any,
    semantic_type: Any,
) -> bool:
    """Return true only for an explicitly configured semantic pairing."""
    if not isinstance(element_type, str):
        return False

    if not isinstance(semantic_type, str):
        return False

    allowed = SEMANTIC_COMPATIBILITY.get(element_type, set())
    return semantic_type in allowed


def _extract_geometry_candidates(
    recognition_data: dict[str, Any],
) -> list[dict[str, Any]]:
    drawings = recognition_data.get("drawings")

    if not isinstance(drawings, list):
        raise ValueError("Recognition drawings must be a list.")

    extracted = []

    for drawing in drawings:
        if not isinstance(drawing, dict):
            raise ValueError("Every recognition drawing must be an object.")

        drawing_id = drawing.get("drawing_id")

        if not _valid_identifier(drawing_id):
            raise ValueError("Recognition drawing has an invalid drawing_id.")

        pages = drawing.get("pages")

        if not isinstance(pages, list):
            raise ValueError("Recognition drawing pages must be a list.")

        for page in pages:
            if not isinstance(page, dict):
                raise ValueError("Every recognition page must be an object.")

            page_number = page.get("page_number")

            if not isinstance(page_number, int) or page_number < 1:
                raise ValueError("Recognition page has an invalid page_number.")

            candidates = page.get("candidates")

            if not isinstance(candidates, list):
                raise ValueError("Page candidates must be a list.")

            for candidate_index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    raise ValueError("Every geometry candidate must be an object.")

                identity = {
                    "drawing_id": drawing_id,
                    "page_number": page_number,
                    "candidate_index": candidate_index,
                    "candidate": candidate,
                }

                extracted.append({
                    "candidate_id": stable_id(identity),
                    "drawing_id": drawing_id,
                    "page_number": page_number,
                    "candidate_index": candidate_index,
                    "element_type": candidate.get("element_type"),
                    "geometry_type": candidate.get("geometry_type"),
                    "bbox_pdf_points": candidate.get("bbox_pdf_points"),
                    "source": candidate.get("source"),
                    "explicit_evidence_ids": sorted(
                        _candidate_evidence_ids(candidate)
                    ),
                })

    return extracted


def analyze_evidence_geometry_associations(
    recognition_data: dict[str, Any],
    evidence_data: dict[str, Any],
) -> dict[str, Any]:
    """Associate evidence with geometry using explicit references only."""

    if not isinstance(recognition_data, dict):
        raise ValueError("Recognition input must be a JSON object.")

    if not isinstance(evidence_data, dict):
        raise ValueError("Evidence input must be a JSON object.")

    if recognition_data.get("stage") != RECOGNITION_STAGE:
        raise ValueError("Expected Stage 5.1.6 recognition input.")

    if evidence_data.get("stage") != EVIDENCE_STAGE:
        raise ValueError("Expected Stage 5.2.12 evidence input.")

    recognition_project = recognition_data.get("project_id")
    evidence_project = evidence_data.get("project_id")

    if not _valid_identifier(recognition_project):
        raise ValueError("Recognition input has an invalid project_id.")

    if recognition_project != evidence_project:
        raise ValueError("Recognition and evidence project IDs do not match.")

    project_id = recognition_project

    evidence_candidates = evidence_data.get("candidates")

    if not isinstance(evidence_candidates, list):
        raise ValueError("Evidence candidates must be a list.")

    geometry_candidates = _extract_geometry_candidates(recognition_data)

    evidence_ids_seen: set[str] = set()
    evidence_results = []

    direct_association_count = 0
    ambiguous_association_count = 0
    unmatched_evidence_count = 0
    incompatible_reference_count = 0

    for evidence_index, evidence in enumerate(evidence_candidates):
        if not isinstance(evidence, dict):
            raise ValueError("Every evidence candidate must be an object.")

        evidence_id = evidence.get("evidence_id")

        if not _valid_identifier(evidence_id):
            raise ValueError(
                f"Evidence candidate at index {evidence_index} "
                "has an invalid evidence_id."
            )

        if evidence_id in evidence_ids_seen:
            raise ValueError(f"Duplicate evidence_id detected: {evidence_id}")

        evidence_ids_seen.add(evidence_id)

        if evidence.get("project_id") != project_id:
            raise ValueError(
                f"Evidence project ID mismatch for {evidence_id}."
            )

        drawing_id = evidence.get("drawing_id")
        page_number = evidence.get("page_number")
        semantic_type = evidence.get("semantic_type")

        if not _valid_identifier(drawing_id):
            raise ValueError(f"Invalid drawing_id for evidence {evidence_id}.")

        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError(f"Invalid page_number for evidence {evidence_id}.")

        # A candidate must explicitly reference this evidence ID.
        explicit_matches = [
            candidate
            for candidate in geometry_candidates
            if evidence_id in candidate["explicit_evidence_ids"]
            and candidate["drawing_id"] == drawing_id
            and candidate["page_number"] == page_number
        ]

        compatible_matches = [
            candidate
            for candidate in explicit_matches
            if _semantic_compatibility(
                candidate["element_type"],
                semantic_type,
            )
        ]

        incompatible_matches = [
            candidate
            for candidate in explicit_matches
            if candidate not in compatible_matches
        ]

        if len(compatible_matches) > 1:
            association_status = "ambiguous"
            reason = "Multiple geometry candidates explicitly reference this evidence."
            ambiguous_association_count += 1
            associated_ids = [
                item["candidate_id"] for item in compatible_matches
            ]

        elif len(compatible_matches) == 1:
            association_status = "direct_association"
            reason = (
                "One geometry candidate explicitly references this evidence "
                "and its element type is semantically compatible."
            )
            direct_association_count += 1
            associated_ids = [
                compatible_matches[0]["candidate_id"]
            ]

        elif incompatible_matches:
            association_status = "unmatched"
            reason = (
                "Explicit candidate references exist, but their element "
                "types are not semantically compatible with this evidence."
            )
            incompatible_reference_count += len(incompatible_matches)
            unmatched_evidence_count += 1
            associated_ids = []

        else:
            association_status = "unmatched"
            reason = (
                "No same-drawing, same-page geometry candidate contains "
                "an explicit reference to this evidence ID."
            )
            unmatched_evidence_count += 1
            associated_ids = []

        evidence_results.append({
            "association_id": stable_id({
                "project_id": project_id,
                "evidence_id": evidence_id,
                "candidate_ids": associated_ids,
                "association_status": association_status,
            }),
            "evidence_id": evidence_id,
            "drawing_id": drawing_id,
            "page_number": page_number,
            "semantic_type": semantic_type,
            "value_text": evidence.get("value_text"),
            "evidence_source": evidence.get("source"),
            "association_status": association_status,
            "associated_candidate_ids": associated_ids,
            "explicit_incompatible_candidate_ids": [
                item["candidate_id"] for item in incompatible_matches
            ],
            "reason": reason,
            "spatial_proximity_used_as_proof": False,
            "verification_required": True,
            "approval_status": "unapproved",
            "geometry_generation_allowed": False,
        })

    evidence_results.sort(key=lambda item: item["evidence_id"])

    summary = {
        "recognition_drawing_count": len(
            recognition_data.get("drawings", [])
        ),
        "geometry_candidate_count": len(geometry_candidates),
        "evidence_candidate_count": len(evidence_candidates),
        "direct_association_count": direct_association_count,
        "ambiguous_association_count": ambiguous_association_count,
        "unmatched_evidence_count": unmatched_evidence_count,
        "incompatible_explicit_reference_count": incompatible_reference_count,
        "approved_association_count": 0,
        "geometry_generation_allowed_count": 0,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stages": {
            "recognition": RECOGNITION_STAGE,
            "evidence": EVIDENCE_STAGE,
        },
        "summary": summary,
        "geometry_candidates": geometry_candidates,
        "evidence_associations": evidence_results,
        "policy": (
            "Associations require explicit evidence-ID references, matching "
            "drawing and page identifiers, and configured semantic "
            "compatibility. Spatial proximity, filename similarity, and "
            "shared project membership do not establish an association. "
            "Every association remains unapproved and requires engineering "
            "verification. This stage does not generate BIM geometry."
        ),
    }
