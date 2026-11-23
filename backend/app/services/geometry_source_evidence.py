
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any


STAGE = "5.3.3"
SCHEMA_VERSION = "0.2.1"

LEVEL_PATTERN = re.compile(
    r"\b(?:"
    r"F\s*\.?\s*F\s*\.?\s*L|"
    r"S\s*\.?\s*S\s*\.?\s*L|"
    r"T\s*\.?\s*O\s*\.?\s*S|"
    r"T\s*\.?\s*O\s*\.?\s*C|"
    r"R\s*\.?\s*L"
    r")\b",
    re.IGNORECASE,
)

NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")

UNIT_PATTERN = re.compile(
    r"[-+]?\d+(?:\.\d+)?\s*(?:mm|cm|m)\b",
    re.IGNORECASE,
)

MEASUREMENT_PATTERN = re.compile(
    r"(?P<first>[-+]?\d+(?:\.\d+)?)"
    r"\s*(?:(?:TO|–|—)\s*"
    r"(?P<second>[-+]?\d+(?:\.\d+)?)\s*)?"
    r"(?P<unit>mm|cm|m)\b",
    re.IGNORECASE,
)

WALL_HEIGHT_PATTERN = re.compile(
    r"\b(?:WALL\s+HEIGHT|WALL\s+HT\.?)\b",
    re.IGNORECASE,
)

HEIGHT_PATTERN = re.compile(
    r"\b(?:"
    r"CLEAR\s+HEIGHT|"
    r"CEILING\s+HEIGHT|"
    r"FLOOR\s*[- ]?TO\s*[- ]?FLOOR|"
    r"HEIGHT|HT\.?"
    r")\b",
    re.IGNORECASE,
)

RAILING_PATTERN = re.compile(
    r"\b(?:HANDRAIL|GUARDRAIL|BALUSTRADE)\b",
    re.IGNORECASE,
)

THICKNESS_PATTERN = re.compile(
    r"\b(?:THICKNESS|THK\.?|THICK)\b",
    re.IGNORECASE,
)

STAIR_PATTERN = re.compile(
    r"\b(?:STAIR|STAIRCASE|STAIR\s+CASE|RISER|TREAD|GOING)\b",
    re.IGNORECASE,
)

MATERIAL_PATTERN = re.compile(
    r"\b(?:"
    r"REINFORCED\s+CONCRETE|CONCRETE|"
    r"HOLLOW\s+BLOCK|SOLID\s+BLOCK|"
    r"BLOCK\s+WALL|FIRE\s+RATED|"
    r"THERMAL\s+INSULATED|STEEL|"
    r"MASONRY|GYPSUM"
    r")\b",
    re.IGNORECASE,
)


def _stable_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()[:20]


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False

    try:
        x1, y1, x2, y2 = map(float, value)
    except (TypeError, ValueError):
        return False

    return (
        all(math.isfinite(v) for v in (x1, y1, x2, y2))
        and x2 >= x1
        and y2 >= y1
    )


def _bbox_distance(a: list, b: list) -> float:
    ax1, ay1, ax2, ay2 = map(float, a)
    bx1, by1, bx2, by2 = map(float, b)

    dx = max(ax1 - bx2, bx1 - ax2, 0.0)
    dy = max(ay1 - by2, by1 - ay2, 0.0)

    return math.hypot(dx, dy)


def _classify_text(text: str) -> list[str]:
    categories = []

    if LEVEL_PATTERN.search(text):
        categories.append("level_annotation")

    if WALL_HEIGHT_PATTERN.search(text):
        categories.append("wall_height_annotation")

    elif (
        RAILING_PATTERN.search(text)
        and HEIGHT_PATTERN.search(text)
    ):
        categories.append("railing_height_annotation")

    elif HEIGHT_PATTERN.search(text):
        categories.append("component_height_annotation")

    if THICKNESS_PATTERN.search(text):
        categories.append("thickness_annotation")

    elif (
        not WALL_HEIGHT_PATTERN.search(text)
        and UNIT_PATTERN.search(text)
        and re.search(r"\b(?:WALL|BLOCK)\b", text, re.IGNORECASE)
    ):
        categories.append("possible_wall_thickness")

    if STAIR_PATTERN.search(text):
        categories.append("stair_annotation")

    if MATERIAL_PATTERN.search(text):
        categories.append("material_specification")

    if not categories and UNIT_PATTERN.search(text):
        categories.append("dimension_annotation")

    return categories or ["unclassified_text"]


def _extract_numeric_value(text: str) -> dict[str, Any] | None:
    match = MEASUREMENT_PATTERN.search(text)

    if match:
        first_value = float(match.group("first"))
        second_text = match.group("second")
        unit = match.group("unit").lower()

        if second_text is not None:
            second_value = float(second_text)

            return {
                "values": [first_value, second_value],
                "unit": unit,
                "is_range": True,
            }

        return {
            "values": [first_value],
            "unit": unit,
            "is_range": False,
        }

    # Door schedules sometimes label the opening height with a number
    # but omit the unit from the extracted text. Preserve the number
    # while leaving the unit unspecified; never assume millimetres.
    door_height_match = re.search(
        r"^\s*(?P<value>[-+]?\d+(?:\.\d+)?)"
        r"\s+DOOR\s+OPENING\s+HEIGHT"
        r"(?:\s*\(\s*DH\s*\))?\s*$",
        text,
        re.IGNORECASE,
    )

    if door_height_match:
        return {
            "values": [float(door_height_match.group("value"))],
            "unit": None,
            "is_range": False,
        }

    return None


def _page_map(drawings: list[dict]) -> dict:
    result = {}

    for drawing in drawings:
        if not isinstance(drawing, dict):
            continue

        drawing_id = drawing.get("drawing_id")

        if not drawing_id:
            continue

        if drawing_id in result:
            raise ValueError(f"Duplicate drawing ID: {drawing_id}")

        pages = {}

        for page in drawing.get("pages", []):
            if not isinstance(page, dict):
                continue

            number = page.get("page_number")

            if number is not None:
                if number in pages:
                    raise ValueError(
                        f"Duplicate page number {number} "
                        f"for drawing {drawing_id}"
                    )

                pages[number] = page

        result[drawing_id] = {
            "drawing": drawing,
            "pages": pages,
        }

    return result


def _make_spatial_suggestions(
    evidence_bbox: list,
    candidates: list[dict],
    max_distance: float,
) -> list[dict]:
    suggestions = []

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue

        candidate_bbox = candidate.get("bbox_pdf_points")

        if not _valid_bbox(candidate_bbox):
            continue

        distance = _bbox_distance(
            evidence_bbox,
            candidate_bbox,
        )

        if distance <= max_distance:
            suggestions.append({
                "candidate_index": index,
                "element_type": candidate.get("element_type"),
                "candidate_bbox_pdf_points": candidate_bbox,
                "distance_pdf_points": round(distance, 3),
                "relationship_status": (
                    "proximity_only_not_association"
                ),
                "verification_required": True,
            })

    return sorted(
        suggestions,
        key=lambda item: item["distance_pdf_points"],
    )


def build_geometry_source_evidence_report(
    recognition_data: dict[str, Any],
    geometry_data: dict[str, Any],
    *,
    max_spatial_distance_pdf_points: float = 24.0,
) -> dict[str, Any]:

    if not isinstance(recognition_data, dict):
        raise ValueError("Recognition input must be an object")

    if not isinstance(geometry_data, dict):
        raise ValueError("Geometry input must be an object")

    if (
        not math.isfinite(max_spatial_distance_pdf_points)
        or max_spatial_distance_pdf_points < 0
    ):
        raise ValueError("Invalid spatial distance threshold")

    project_id = recognition_data.get("project_id")

    if not project_id:
        raise ValueError("Recognition project_id is missing")

    geometry_project_id = geometry_data.get("project_id")

    if geometry_project_id and geometry_project_id != project_id:
        raise ValueError("Project ID mismatch")

    recognition_drawings = recognition_data.get("drawings", [])
    geometry_drawings = geometry_data.get("drawings", [])

    if not isinstance(recognition_drawings, list):
        raise ValueError("Recognition drawings must be a list")

    if not isinstance(geometry_drawings, list):
        raise ValueError("Geometry drawings must be a list")

    recognition_map = _page_map(recognition_drawings)
    geometry_map = _page_map(geometry_drawings)

    evidence_records = []
    warnings = []

    hash_mismatch_count = 0
    processed_pages = 0
    skipped_pages = 0

    for drawing_id, recognition_entry in recognition_map.items():
        geometry_entry = geometry_map.get(drawing_id)

        if geometry_entry is None:
            warnings.append({
                "drawing_id": drawing_id,
                "reason": "geometry_drawing_not_found",
            })
            continue

        recognition_drawing = recognition_entry["drawing"]
        geometry_drawing = geometry_entry["drawing"]

        recognition_hash = recognition_drawing.get("source_sha256")
        geometry_hash = geometry_drawing.get("source_sha256")

        if (
            recognition_hash
            and geometry_hash
            and recognition_hash != geometry_hash
        ):
            hash_mismatch_count += 1

            warnings.append({
                "drawing_id": drawing_id,
                "reason": "source_hash_mismatch",
            })
            continue

        for page_number, recognition_page in (
            recognition_entry["pages"].items()
        ):
            geometry_page = geometry_entry["pages"].get(page_number)

            if geometry_page is None:
                skipped_pages += 1
                continue

            recognition_size = recognition_page.get(
                "page_size_pdf_points"
            )

            geometry_size = geometry_page.get(
                "page_size_pdf_points"
            )

            if (
                recognition_size
                and geometry_size
                and recognition_size != geometry_size
            ):
                warnings.append({
                    "drawing_id": drawing_id,
                    "page_number": page_number,
                    "reason": "page_size_mismatch",
                })

                skipped_pages += 1
                continue

            spans = geometry_page.get("text_spans", [])
            candidates = recognition_page.get("candidates", [])

            if not isinstance(spans, list):
                skipped_pages += 1
                continue

            if not isinstance(candidates, list):
                candidates = []

            processed_pages += 1

            for span_index, span in enumerate(spans):
                if not isinstance(span, dict):
                    continue

                text = str(
                    span.get("text")
                    or span.get("content")
                    or ""
                ).strip()

                bbox = (
                    span.get("bbox")
                    or span.get("bbox_pdf_points")
                )

                if not text or not _valid_bbox(bbox):
                    continue

                categories = _classify_text(text)
                numeric_value = _extract_numeric_value(text)

                source_reference = {
                    "drawing_id": drawing_id,
                    "page_number": page_number,
                    "span_index": span_index,
                    "text": text,
                    "bbox_pdf_points": bbox,
                    "source_sha256": recognition_hash,
                }

                evidence_id = _stable_id({
                    "project_id": project_id,
                    **source_reference,
                })

                suggestions = _make_spatial_suggestions(
                    bbox,
                    candidates,
                    max_spatial_distance_pdf_points,
                )

                is_wall_height = (
                    "wall_height_annotation" in categories
                )

                evidence_records.append({
                    "evidence_id": evidence_id,
                    "project_id": project_id,
                    "drawing_id": drawing_id,
                    "page_number": page_number,
                    "categories": categories,
                    "text": text,
                    "numeric_value": numeric_value,
                    "source_reference": source_reference,
                    "spatial_suggestions": suggestions,
                    "height_evidence_candidate": (
                        is_wall_height and numeric_value is not None
                    ),
                    "builder_height_evidence": None,
                    "spatial_proximity_used_as_proof": False,
                    "verification_required": True,
                    "approval_status": "unapproved",
                    "geometry_generation_allowed": False,
                })

    category_counts = Counter(
        category
        for record in evidence_records
        for category in record["categories"]
    )

    return {
        "stage": STAGE,
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "status": "completed_with_verification_required",
        "summary": {
            "recognition_drawings": len(recognition_map),
            "geometry_drawings": len(geometry_map),
            "processed_pages": processed_pages,
            "skipped_pages": skipped_pages,
            "evidence_count": len(evidence_records),
            "category_counts": dict(category_counts),
            "height_evidence_candidate_count": sum(
                record["height_evidence_candidate"]
                for record in evidence_records
            ),
            "spatial_suggestion_count": sum(
                len(record["spatial_suggestions"])
                for record in evidence_records
            ),
            "source_hash_mismatches": hash_mismatch_count,
            "approved_count": 0,
            "geometry_generation_allowed_count": 0,
        },
        "evidence": evidence_records,
        "warnings": warnings,
        "limitations": [
            "Text classification is heuristic and requires review.",
            "Spatial proximity is not proof of element association.",
            "Floor-level annotations do not establish wall height.",
            "Wall thickness does not establish wall height.",
            "Generic component heights are not wall-height evidence.",
            "Railing heights are not wall-height evidence.",
            "Height ranges preserve both numeric endpoints.",
            "No height evidence is automatically approved.",
            "No BIM geometry is generated by this service.",
            "All evidence remains provisional and unapproved.",
        ],
    }
