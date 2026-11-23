
"""
Innoclique — Stage 5.2.12
Evidence Deduplication and Relationship Tracking.

Preserves all source candidates while identifying exact and
possible duplicate evidence across and within drawings.

Source character offsets distinguish multiple measurements
inside the same text span.

This stage does not approve evidence, calibrate levels,
calculate heights, or generate BIM geometry.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.1.1"
STAGE = "5.2.12"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(
        _canonical(value).encode("utf-8")
    ).hexdigest()


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _normalized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None

    try:
        coords = [round(float(item), 3) for item in value[:4]]
    except (TypeError, ValueError):
        return None

    if coords[2] < coords[0] or coords[3] < coords[1]:
        return None

    return coords


def _source_character_range(
    source: dict[str, Any],
) -> tuple[int, int] | None:
    """
    Return the exact source-text character range.

    Invalid or incomplete ranges remain None rather than
    being guessed or reconstructed.
    """
    start = source.get("char_start")
    end = source.get("char_end")

    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        return None

    return start, end


def _candidate_identity(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    source = candidate.get("source") or {}
    character_range = _source_character_range(source)

    return {
        "project_id": candidate.get("project_id"),
        "drawing_id": candidate.get("drawing_id"),
        "page_number": candidate.get("page_number"),
        "semantic_type": candidate.get("semantic_type"),
        "value_text": candidate.get("value_text"),
        "source_text": source.get("text"),
        "bbox": _normalized_bbox(source.get("bbox")),
        "span_index": source.get("span_index"),
        "char_start": (
            character_range[0] if character_range else None
        ),
        "char_end": (
            character_range[1] if character_range else None
        ),
    }


def _duplicate_key(candidate: dict[str, Any]) -> tuple | None:
    """
    Exact-match key.

    Character offsets distinguish separate measurements
    within the same text span.

    Matching offsets across drawings can still identify
    possible repeated annotations.

    Candidates without a valid bounding box are not grouped.
    """
    source = candidate.get("source") or {}
    bbox = _normalized_bbox(source.get("bbox"))

    if bbox is None:
        return None

    character_range = _source_character_range(source)

    return (
        str(candidate.get("semantic_type") or ""),
        _normalize_text(candidate.get("value_text")),
        _normalize_text(source.get("text")),
        tuple(bbox),
        character_range,
    )


def deduplicate_evidence(
    report: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise TypeError("report must be a dictionary")

    if not isinstance(report.get("candidates"), list):
        raise ValueError(
            "report must contain a candidates list"
        )

    original_candidates = report["candidates"]

    groups: dict[tuple, list[int]] = defaultdict(list)
    enriched: list[dict[str, Any]] = []

    for index, candidate in enumerate(original_candidates):
        if not isinstance(candidate, dict):
            raise ValueError(
                f"Candidate at index {index} must be a dictionary"
            )

        identity = _candidate_identity(candidate)
        evidence_id = "evi_" + _hash(identity)[:24]

        item = dict(candidate)
        item["evidence_id"] = evidence_id
        item["evidence_relationship"] = "unclassified"
        item["duplicate_group_id"] = None
        item["duplicate_scope"] = None
        item["independent_evidence"] = None

        enriched.append(item)

        key = _duplicate_key(candidate)
        if key is not None:
            groups[key].append(index)

    exact_group_count = 0
    cross_drawing_group_count = 0
    within_drawing_group_count = 0
    ungrouped_count = 0

    for key, indexes in groups.items():
        if len(indexes) < 2:
            continue

        exact_group_count += 1

        drawing_ids = {
            str(enriched[index].get("drawing_id") or "")
            for index in indexes
        }

        filenames = {
            str(enriched[index].get("filename") or "")
            for index in indexes
        }

        if all(
            enriched[index].get("drawing_id") is not None
            for index in indexes
        ):
            cross_drawing = len(drawing_ids) > 1
        else:
            cross_drawing = len(filenames) > 1

        scope = (
            "cross_drawing"
            if cross_drawing
            else "within_drawing"
        )

        group_id = "dup_" + _hash({
            "stage": STAGE,
            "duplicate_key": list(key),
        })[:24]

        if cross_drawing:
            cross_drawing_group_count += 1
        else:
            within_drawing_group_count += 1

        for index in indexes:
            enriched[index]["evidence_relationship"] = (
                "possible_duplicate_evidence"
            )
            enriched[index]["duplicate_group_id"] = group_id
            enriched[index]["duplicate_scope"] = scope
            enriched[index]["independent_evidence"] = False

    for item in enriched:
        if item["duplicate_group_id"] is None:
            item["evidence_relationship"] = "unmatched_evidence"
            item["independent_evidence"] = None
            ungrouped_count += 1

        # Preserve original safety restrictions.
        item["approval_status"] = "unapproved"
        item["verification_required"] = True
        item["calibration_eligible"] = False
        item["geometry_generation_allowed"] = False

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": report.get("project_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": {
            "stage": report.get("stage"),
            "schema_version": report.get("schema_version"),
            "candidate_count": len(original_candidates),
            "source_generated_at": report.get("generated_at"),
        },
        "summary": {
            "input_candidate_count": len(original_candidates),
            "output_candidate_count": len(enriched),
            "exact_duplicate_group_count": exact_group_count,
            "possible_cross_drawing_group_count": (
                cross_drawing_group_count
            ),
            "possible_within_drawing_group_count": (
                within_drawing_group_count
            ),
            "ungrouped_candidate_count": ungrouped_count,
            "approved_count": 0,
            "calibration_eligible_count": 0,
            "geometry_generation_allowed_count": 0,
        },
        "candidates": enriched,
        "limitations": [
            "All source candidates are preserved.",
            "Source character offsets distinguish measurements within a text span.",
            "Exact matching does not prove duplicated drawing content.",
            "Possible duplicates are not independent confirmation.",
            "Ungrouped candidates are not automatically independent.",
            "Candidates without valid bounding boxes are not grouped.",
            "No evidence is automatically approved.",
            "No calibration or geometry is generated.",
            "No height calculation is performed.",
        ],
    }
