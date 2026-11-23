
"""Stage 5.2.14: Source-line and candidate relationship analysis.

Read-only analysis. Candidate relationships are not confirmed BIM objects.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


STAGE = "5.2.14"
SCHEMA_VERSION = "0.1.0"
WALL_TYPE = "wall_geometry_supported_candidate"


def stable_id(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def bbox_values(candidate: dict) -> tuple | None:
    bbox = candidate.get("bbox_pdf_points")

    if not isinstance(bbox, list) or len(bbox) != 4:
        return None

    try:
        values = tuple(float(v) for v in bbox)
    except (TypeError, ValueError):
        return None

    x1, y1, x2, y2 = values

    if x2 <= x1 or y2 <= y1:
        return None

    return values


def source_lines(candidate: dict) -> set[tuple]:
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


def intersection_area(a: tuple, b: tuple) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

    return round(width * height, 4)


def candidate_reference(
    drawing: dict,
    page: dict,
    candidate: dict,
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
        "candidate_id": stable_id(identity),
        "drawing_id": drawing.get("drawing_id"),
        "filename": drawing.get("filename"),
        "page_number": page.get("page_number"),
        "candidate_index": index,
        "bbox_pdf_points": candidate.get("bbox_pdf_points"),
        "source": candidate.get("source"),
        "source_lines": [
            list(line)
            for line in sorted(
                source_lines(candidate),
                key=str,
            )
        ],
        "geometry_type": candidate.get("geometry_type"),
        "estimated_thickness_pdf_points": candidate.get(
            "estimated_thickness_pdf_points"
        ),
        "overlap_length_pdf_points": candidate.get(
            "overlap_length_pdf_points"
        ),
        "confidence": candidate.get("confidence"),
        "verification_required": candidate.get(
            "verification_required", True
        ),
    }


def connected_components(
    candidate_count: int,
    adjacency: dict[int, set[int]],
) -> list[list[int]]:
    visited = set()
    components = []

    for start in range(candidate_count):
        if start in visited:
            continue

        stack = [start]
        visited.add(start)
        component = []

        while stack:
            current = stack.pop()
            component.append(current)

            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)

        if len(component) > 1:
            components.append(sorted(component))

    return components


def analyze_source_geometry(
    recognition_data: dict,
) -> dict:
    if not isinstance(recognition_data, dict):
        raise ValueError("Input must be a JSON object.")

    if recognition_data.get("stage") != "5.1.6":
        raise ValueError("Expected Stage 5.1.6 input.")

    project_id = recognition_data.get("project_id")

    if not project_id:
        raise ValueError("Missing project_id.")

    pair_relationships = []
    candidate_groups = []

    total_walls = 0
    invalid_bbox_count = 0
    reused_source_line_count = 0
    overlapping_pair_count = 0
    shared_line_pair_count = 0

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

            total_walls += len(candidates)

            references = []
            boxes = []
            lines = []

            for index, candidate in enumerate(candidates):
                bbox = bbox_values(candidate)

                if bbox is None:
                    invalid_bbox_count += 1

                boxes.append(bbox)
                lines.append(source_lines(candidate))

                references.append(
                    candidate_reference(
                        drawing,
                        page,
                        candidate,
                        index,
                    )
                )

            line_usage = defaultdict(list)

            for index, candidate_lines in enumerate(lines):
                for line in candidate_lines:
                    line_usage[line].append(index)

            reused_source_line_count += sum(
                len(indexes) > 1
                for indexes in line_usage.values()
            )

            adjacency = defaultdict(set)

            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    shared = sorted(
                        lines[i] & lines[j],
                        key=str,
                    )

                    area = (
                        intersection_area(boxes[i], boxes[j])
                        if boxes[i] is not None
                        and boxes[j] is not None
                        else 0.0
                    )

                    if not shared and area <= 0:
                        continue

                    if shared:
                        shared_line_pair_count += 1
                        adjacency[i].add(j)
                        adjacency[j].add(i)

                    if area > 0:
                        overlapping_pair_count += 1

                    relation_type = (
                        "shared_source_lines_and_bbox_overlap"
                        if shared and area > 0
                        else "shared_source_lines"
                        if shared
                        else "bbox_overlap"
                    )

                    pair_identity = {
                        "drawing_id": drawing.get("drawing_id"),
                        "page_number": page.get("page_number"),
                        "candidate_ids": sorted([
                            references[i]["candidate_id"],
                            references[j]["candidate_id"],
                        ]),
                    }

                    pair_relationships.append({
                        "relationship_id": stable_id(pair_identity),
                        "relationship_type": relation_type,
                        "drawing_id": drawing.get("drawing_id"),
                        "page_number": page.get("page_number"),
                        "members": [
                            references[i],
                            references[j],
                        ],
                        "shared_source_lines": [
                            list(line) for line in shared
                        ],
                        "bbox_intersection_area_pdf_points2": area,
                        "verification_required": True,
                        "approval_status": "unapproved",
                        "physical_identity_confirmed": False,
                        "geometry_generation_allowed": False,
                    })

            for component in connected_components(
                len(candidates),
                adjacency,
            ):
                members = [
                    references[index]
                    for index in component
                ]

                group_identity = {
                    "drawing_id": drawing.get("drawing_id"),
                    "page_number": page.get("page_number"),
                    "candidate_ids": sorted(
                        member["candidate_id"]
                        for member in members
                    ),
                }

                candidate_groups.append({
                    "group_id": stable_id(group_identity),
                    "group_type": "shared_source_line_component",
                    "drawing_id": drawing.get("drawing_id"),
                    "page_number": page.get("page_number"),
                    "member_count": len(members),
                    "members": members,
                    "verification_required": True,
                    "approval_status": "unapproved",
                    "independent_candidates_confirmed": False,
                    "geometry_generation_allowed": False,
                })

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "input_stage": "5.1.6",
        "summary": {
            "wall_candidate_count": total_walls,
            "invalid_bbox_count": invalid_bbox_count,
            "reused_source_line_count": reused_source_line_count,
            "shared_source_line_pair_count": shared_line_pair_count,
            "overlapping_candidate_pair_count": overlapping_pair_count,
            "source_line_component_count": len(candidate_groups),
            "candidate_pair_relationship_count": len(pair_relationships),
            "approved_relationship_count": 0,
            "geometry_generation_allowed_count": 0,
        },
        "candidate_groups": candidate_groups,
        "pair_relationships": pair_relationships,
        "policy": (
            "Shared source lines and overlapping bounding boxes describe "
            "candidate relationships only. They do not establish unique "
            "physical walls or approved BIM geometry."
        ),
    }
