
"""Stage 5.2.18: Evidence-to-geometry association gap analysis.

Diagnostic only. Does not create associations, approve evidence,
or generate BIM geometry.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.services.evidence_geometry_association import (
    SEMANTIC_COMPATIBILITY,
    STAGE as ASSOCIATION_STAGE,
)

STAGE = "5.2.18"
SCHEMA_VERSION = "0.1.0"


def analyze_association_gaps(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Input must be a JSON object.")

    if data.get("stage") != ASSOCIATION_STAGE:
        raise ValueError("Expected Stage 5.2.17 association report.")

    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("Invalid project_id.")

    geometry = data.get("geometry_candidates")
    evidence = data.get("evidence_associations")

    if not isinstance(geometry, list) or not isinstance(evidence, list):
        raise ValueError("Geometry and evidence records must be lists.")

    geometry_by_page = defaultdict(list)

    for item in geometry:
        if not isinstance(item, dict):
            raise ValueError("Invalid geometry candidate.")

        drawing_id = item.get("drawing_id")
        page_number = item.get("page_number")

        if not isinstance(drawing_id, str) or not drawing_id.strip():
            raise ValueError("Geometry candidate has invalid drawing_id.")

        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError("Geometry candidate has invalid page_number.")

        geometry_by_page[(drawing_id, page_number)].append(item)

    seen_ids = set()
    records = []
    group_counts = Counter()
    drawing_counts = defaultdict(Counter)

    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("Invalid evidence association.")

        evidence_id = item.get("evidence_id")
        drawing_id = item.get("drawing_id")
        page_number = item.get("page_number")

        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("Evidence has invalid evidence_id.")

        if evidence_id in seen_ids:
            raise ValueError(f"Duplicate evidence_id: {evidence_id}")
        seen_ids.add(evidence_id)

        if not isinstance(drawing_id, str) or not drawing_id.strip():
            raise ValueError(f"Invalid drawing_id for {evidence_id}")

        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError(f"Invalid page_number for {evidence_id}")

        page_candidates = geometry_by_page.get(
            (drawing_id, page_number), []
        )

        semantic_type = item.get("semantic_type")
        compatible = [
            candidate
            for candidate in page_candidates
            if semantic_type in SEMANTIC_COMPATIBILITY.get(
                candidate.get("element_type"), set()
            )
        ]

        incompatible_ids = item.get(
            "explicit_incompatible_candidate_ids", []
        )

        if not isinstance(incompatible_ids, list):
            raise ValueError("Invalid incompatible candidate ID list.")

        if incompatible_ids:
            gap_status = "explicit_reference_incompatible"
        elif not page_candidates:
            gap_status = "no_geometry_on_page"
        elif not compatible:
            gap_status = "no_semantically_compatible_geometry"
        else:
            gap_status = "compatible_geometry_missing_explicit_reference"

        record = {
            "evidence_id": evidence_id,
            "drawing_id": drawing_id,
            "page_number": page_number,
            "semantic_type": semantic_type,
            "value_text": item.get("value_text"),
            "source": item.get("evidence_source"),
            "same_page_geometry_count": len(page_candidates),
            "compatible_geometry_candidate_ids": [
                candidate["candidate_id"]
                for candidate in compatible
            ],
            "gap_status": gap_status,
            "verification_required": True,
            "approval_status": "unapproved",
            "geometry_generation_allowed": False,
        }

        records.append(record)
        group_counts[gap_status] += 1
        drawing_counts[drawing_id][gap_status] += 1

    records.sort(key=lambda x: (x["drawing_id"], x["page_number"], x["evidence_id"]))

    page_groups = defaultdict(list)
    for record in records:
        key = (record["drawing_id"], record["page_number"])
        page_groups[key].append(record)

    page_reports = []
    for (drawing_id, page_number), items in sorted(page_groups.items()):
        page_reports.append({
            "drawing_id": drawing_id,
            "page_number": page_number,
            "evidence_count": len(items),
            "geometry_candidate_count": len(
                geometry_by_page.get((drawing_id, page_number), [])
            ),
            "gap_status_counts": dict(Counter(
                item["gap_status"] for item in items
            )),
            "verification_required": True,
        })

    summary = {
        "evidence_count": len(records),
        "geometry_candidate_count": len(geometry),
        "drawings_with_geometry": len({
            item["drawing_id"] for item in geometry
        }),
        "drawings_with_evidence": len({
            item["drawing_id"] for item in records
        }),
        "evidence_pages_with_geometry": sum(
            1 for key in page_groups if key in geometry_by_page
        ),
        "evidence_pages_without_geometry": sum(
            1 for key in page_groups if key not in geometry_by_page
        ),
        "gap_status_counts": dict(sorted(group_counts.items())),
        "approved_count": 0,
        "geometry_generation_allowed_count": 0,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stage": ASSOCIATION_STAGE,
        "summary": summary,
        "page_reports": page_reports,
        "drawing_gap_counts": {
            drawing_id: dict(sorted(counts.items()))
            for drawing_id, counts in sorted(drawing_counts.items())
        },
        "evidence_gaps": records,
        "policy": (
            "Diagnostic gap analysis only. Same-page geometry and semantic "
            "compatibility identify review opportunities, not associations. "
            "No spatial proximity or inferred relationship establishes proof. "
            "All records require engineering verification. No approvals or "
            "BIM geometry generation are permitted."
        ),
    }
