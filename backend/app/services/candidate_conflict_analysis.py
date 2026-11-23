\
"""Stage 5.2.15: Candidate conflict and ambiguity analysis.

Read-only analysis of competing geometric hypotheses.

Bounding-box overlap is not proof of physical connectivity.
No candidates are merged, deleted, approved, or converted to BIM geometry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


STAGE = "5.2.15"
SCHEMA_VERSION = "0.1.1"
WALL_TYPE = "wall_geometry_supported_candidate"


def _stable_id(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _bbox(candidate: dict) -> tuple | None:
    raw = candidate.get("bbox_pdf_points")

    if not isinstance(raw, list) or len(raw) != 4:
        return None

    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2


def _source_lines(candidate: dict) -> set[tuple]:
    source = candidate.get("source")

    if not isinstance(source, dict):
        return set()

    result = set()

    for key in ("line_a", "line_b"):
        value = source.get(key)

        if (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(v, (int, str)) for v in value)
        ):
            result.add(tuple(value))

    return result


def _intersection(a: tuple, b: tuple) -> dict:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

    area = width * height

    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - area

    return {
        "area": round(area, 4),
        "iou": round(area / union, 6) if union > 0 else 0.0,
        "overlap_of_smaller": (
            round(area / min(area_a, area_b), 6)
            if min(area_a, area_b) > 0
            else 0.0
        ),
        "touches": (
            width == 0 and height >= 0
        ) or (
            height == 0 and width >= 0
        ),
    }


def _candidate_record(
    candidate: dict,
    drawing: dict,
    page: dict,
    index: int,
) -> dict:
    identity = {
        "drawing_id": drawing.get("drawing_id"),
        "page_number": page.get("page_number"),
        "candidate_index": index,
        "source": candidate.get("source"),
        "bbox": candidate.get("bbox_pdf_points"),
    }

    return {
        "candidate_id": _stable_id(identity),
        "drawing_id": drawing.get("drawing_id"),
        "filename": drawing.get("filename"),
        "page_number": page.get("page_number"),
        "candidate_index": index,
        "bbox_pdf_points": candidate.get("bbox_pdf_points"),
        "source": candidate.get("source"),
        "source_lines": [
            list(line)
            for line in sorted(_source_lines(candidate), key=str)
        ],
        "geometry_type": candidate.get("geometry_type"),
        "thickness_pdf_points": candidate.get(
            "estimated_thickness_pdf_points"
        ),
        "length_pdf_points": candidate.get(
            "overlap_length_pdf_points"
        ),
        "confidence": candidate.get("confidence"),
        "verification_required": candidate.get(
            "verification_required", True
        ),
    }


def _classify_pair(
    shared_lines: set,
    intersection: dict,
    geometry_a: str | None,
    geometry_b: str | None,
) -> list[str]:
    findings = []

    if shared_lines:
        findings.append("shared_source_geometry")

    if intersection["area"] > 0:
        findings.append("bbox_overlap")

    is_orthogonal = (
        geometry_a
        and geometry_b
        and geometry_a != geometry_b
        and "horizontal" in geometry_a
        and "vertical" in geometry_b
    ) or (
        geometry_a
        and geometry_b
        and geometry_a != geometry_b
        and "vertical" in geometry_a
        and "horizontal" in geometry_b
    )

    if is_orthogonal and intersection["area"] > 0:
        findings.append("orthogonal_bbox_overlap_unverified")

    if (
        intersection["overlap_of_smaller"] >= 0.8
        and intersection["area"] > 0
    ):
        findings.append("substantial_bbox_containment_or_overlap")

    return findings


def analyze_candidate_conflicts(recognition_data: dict) -> dict:
    if not isinstance(recognition_data, dict):
        raise ValueError("Input must be a JSON object.")

    if recognition_data.get("stage") != "5.1.6":
        raise ValueError("Expected Stage 5.1.6 input.")

    project_id = recognition_data.get("project_id")

    if not project_id:
        raise ValueError("Missing project_id.")

    conflicts = []

    candidate_count = 0
    invalid_bbox_count = 0
    shared_source_count = 0
    bbox_overlap_count = 0
    orthogonal_overlap_count = 0
    substantial_overlap_count = 0

    for drawing in recognition_data.get("drawings", []):
        if not isinstance(drawing, dict):
            continue

        for page in drawing.get("pages", []):
            if not isinstance(page, dict):
                continue

            candidates = [
                c for c in page.get("candidates", [])
                if isinstance(c, dict)
                and c.get("element_type") == WALL_TYPE
            ]

            candidate_count += len(candidates)

            records = []
            boxes = []
            lines = []

            for index, candidate in enumerate(candidates):
                box = _bbox(candidate)

                if box is None:
                    invalid_bbox_count += 1

                boxes.append(box)
                lines.append(_source_lines(candidate))

                records.append(
                    _candidate_record(
                        candidate,
                        drawing,
                        page,
                        index,
                    )
                )

            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    shared = lines[i] & lines[j]

                    if boxes[i] is None or boxes[j] is None:
                        continue

                    intersection = _intersection(
                        boxes[i],
                        boxes[j],
                    )

                    findings = _classify_pair(
                        shared,
                        intersection,
                        candidates[i].get("geometry_type"),
                        candidates[j].get("geometry_type"),
                    )

                    if not findings:
                        continue

                    if shared:
                        shared_source_count += 1

                    if intersection["area"] > 0:
                        bbox_overlap_count += 1

                    if "orthogonal_bbox_overlap_unverified" in findings:
                        orthogonal_overlap_count += 1

                    if (
                        "substantial_bbox_containment_or_overlap"
                        in findings
                    ):
                        substantial_overlap_count += 1

                    identity = {
                        "drawing_id": drawing.get("drawing_id"),
                        "page_number": page.get("page_number"),
                        "candidate_ids": sorted([
                            records[i]["candidate_id"],
                            records[j]["candidate_id"],
                        ]),
                    }

                    conflicts.append({
                        "conflict_id": _stable_id(identity),
                        "findings": findings,
                        "drawing_id": drawing.get("drawing_id"),
                        "page_number": page.get("page_number"),
                        "members": [
                            records[i],
                            records[j],
                        ],
                        "shared_source_lines": [
                            list(line)
                            for line in sorted(shared, key=str)
                        ],
                        "bbox_intersection_area_pdf_points2": (
                            intersection["area"]
                        ),
                        "bbox_iou": intersection["iou"],
                        "overlap_of_smaller_bbox": (
                            intersection["overlap_of_smaller"]
                        ),
                        "orthogonal_bbox_overlap_unverified": (
                            "orthogonal_bbox_overlap_unverified"
                            in findings
                        ),
                        "source_line_intersection_evidence_available": False,
                        "physical_intersection_confirmed": False,
                        "verification_required": True,
                        "approval_status": "unapproved",
                        "physical_identity_confirmed": False,
                        "geometry_generation_allowed": False,
                    })

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stage": "5.1.6",
        "summary": {
            "wall_candidate_count": candidate_count,
            "invalid_bbox_count": invalid_bbox_count,
            "conflict_pair_count": len(conflicts),
            "shared_source_pair_count": shared_source_count,
            "bbox_overlap_pair_count": bbox_overlap_count,
            "orthogonal_bbox_overlap_unverified_count": (
                orthogonal_overlap_count
            ),
            "possible_geometric_junction_count": 0,
            "substantial_bbox_overlap_count": (
                substantial_overlap_count
            ),
            "approved_conflict_count": 0,
            "geometry_generation_allowed_count": 0,
        },
        "conflicts": conflicts,
        "policy": (
            "Bounding-box overlap is not proof of physical intersection. "
            "Orthogonal bbox overlap is reported as an unverified spatial "
            "relationship only. Actual source-line intersection evidence "
            "is not available in this analysis. All findings require "
            "verification and remain unapproved."
        ),
    }
