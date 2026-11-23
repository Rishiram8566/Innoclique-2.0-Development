
"""
Innoclique — Stage 5.2.11
Vertical Level Evidence Extraction (strict classification)

Extracts provisional, source-grounded vertical level annotations.
Does not approve levels, infer units/datums, calculate heights,
or generate BIM geometry.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.2.0"
STAGE = "5.2.11"

# This stage intentionally accepts decimal-form values only.
# Examples: +6.05, -7.60, 0.45, ±0.00
# Integer identifiers such as -01 are not accepted.
LEVEL_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9.,])"
    r"([+-]\s*\d{1,5}\.\d{1,4}|\d{1,5}\.\d{1,4})"
    r"(?![A-Za-z0-9.,])"
)

# Explicit level labels only. Generic floor names are deliberately excluded.
LABEL_PATTERNS = [
    ("top_roof_level", re.compile(
        r"\bT\.?\s*R\.?\s*L\.?\b|\bTOP\s+ROOF\s+(?:LVL|LEVEL)\b", re.I
    )),
    ("roof_upper_level", re.compile(
        r"\bROOF\s+FLOOR\s+(?:LVL\s*)?\(UPPER\)", re.I
    )),
    ("roof_lower_level", re.compile(
        r"\bROOF\s+FLOOR\s+(?:LVL\s*)?\(LOWER\)", re.I
    )),
    ("roof_level", re.compile(
        r"\bROOF\s+(?:FLOOR|LEVEL|LVL)\b", re.I
    )),
    ("mezzanine_level", re.compile(
        r"\bMEZZANINE\s+(?:FLOOR\s+)?(?:LEVEL|LVL)\b", re.I
    )),
    ("ground_floor_level", re.compile(
        r"\bGROUND\s+FLOOR\s+(?:LEVEL|LVL)\b", re.I
    )),
    ("basement_lower_level", re.compile(
        r"\bBASEMENT\s+FLOOR\s+LOWER\s+LEVEL\b|"
        r"\bBASEMENT\s+LOWER\s+LEVEL\b", re.I
    )),
    ("basement_upper_level", re.compile(
        r"\bBASEMENT\s+FLOOR\s+UPPER\s+LEVEL\b|"
        r"\bBASEMENT\s+UPPER\s+LEVEL\b", re.I
    )),
    ("basement_level", re.compile(
        r"\bBASEMENT\s+(?:FLOOR\s+)?(?:LEVEL|LVL)\b", re.I
    )),
    ("road_level", re.compile(
        r"\b(?:ROAD|ROADWAY)\s+(?:LEVEL|LVL)\b", re.I
    )),
    ("structural_slab_level", re.compile(
        r"\bS\.?\s*S\.?\s*L\.?\b", re.I
    )),
    ("finished_floor_level", re.compile(
        r"\bF\.?\s*F\.?\s*L\.?\b", re.I
    )),
    ("water_level", re.compile(
        r"\bWATER\s+(?:LEVEL|LVL)\b", re.I
    )),
]

# Reject contexts that are not vertical level evidence.
EXCLUDED_CONTEXT = re.compile(
    r"\b(?:"
    r"AREA|SQ\.?\s*FT|SQ\.?\s*M|SQUARE\s+FEET|"
    r"STAIR|STAIRCASE|BATTERY\s+RACK|"
    r"VOLUME|CAPACITY|U[- ]?VALUE|"
    r"W/M2|M2\.K|"
    r"DOOR\s+NO|WINDOW\s+NO|"
    r"GRID\s+NO|COLUMN\s+NO|"
    r"ROOM\s+NO|PANEL\s+NO|"
    r"ITEM\s+NO|DETAIL\s+NO|"
    r"REFERENCE\s+NO"
    r")\b",
    re.I,
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None

    try:
        coords = [float(v) for v in value[:4]]
    except (TypeError, ValueError):
        return None

    if coords[2] < coords[0] or coords[3] < coords[1]:
        return None

    return coords


def _horizontal_gap(a: list[float], b: list[float]) -> float:
    if a[2] < b[0]:
        return b[0] - a[2]
    if b[2] < a[0]:
        return a[0] - b[2]
    return 0.0


def _vertical_gap(a: list[float], b: list[float]) -> float:
    if a[3] < b[1]:
        return b[1] - a[3]
    if b[3] < a[1]:
        return a[1] - b[3]
    return 0.0


def _is_excluded_context(text: str) -> bool:
    return bool(text and EXCLUDED_CONTEXT.search(text))


def _label_type(text: str) -> str | None:
    if not text or _is_excluded_context(text):
        return None

    for semantic_type, pattern in LABEL_PATTERNS:
        if pattern.search(text):
            return semantic_type

    return None


def _numeric_values(text: str) -> list[str]:
    if not text or _is_excluded_context(text):
        return []

    return [
        match.group(1).replace(" ", "")
        for match in LEVEL_VALUE_RE.finditer(text)
    ]


def _iter_pages(metadata_index: dict[str, Any]):
    drawings = metadata_index.get("drawings", [])
    if not isinstance(drawings, list):
        return

    for drawing in drawings:
        if not isinstance(drawing, dict):
            continue

        pages = drawing.get("pages", [])
        if not isinstance(pages, list):
            continue

        for page_index, page in enumerate(pages):
            if isinstance(page, dict):
                yield drawing, page, page_index


def _iter_spans(page: dict[str, Any]):
    spans = page.get("text_spans", [])
    if not isinstance(spans, list):
        return

    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            continue

        text = _text(span.get("text"))
        if text:
            yield index, span, text


def _find_nearby_label(
    numeric_span: dict[str, Any],
    numeric_text: str,
    spans: list[tuple[int, dict[str, Any], str]],
    max_distance: float,
) -> tuple[str | None, dict[str, Any] | None]:

    # Never pair numeric values from an excluded source context.
    if _is_excluded_context(numeric_text):
        return None, None

    # Explicit label and value in the same text span.
    own_label = _label_type(numeric_text)
    if own_label:
        return own_label, numeric_span

    numeric_box = _bbox(numeric_span.get("bbox"))
    if numeric_box is None:
        return None, None

    matches = []

    for _, candidate_span, candidate_text in spans:
        # Only explicitly recognized label spans can be paired.
        label_type = _label_type(candidate_text)
        if not label_type:
            continue

        label_box = _bbox(candidate_span.get("bbox"))
        if label_box is None:
            continue

        v_gap = _vertical_gap(numeric_box, label_box)
        h_gap = _horizontal_gap(numeric_box, label_box)

        if v_gap <= max_distance and h_gap <= max_distance:
            score = v_gap + h_gap
            matches.append((score, label_type, candidate_span))

    if not matches:
        return None, None

    matches.sort(key=lambda item: item[0])

    # Reject ambiguous nearest-label matches.
    if len(matches) > 1 and abs(matches[1][0] - matches[0][0]) < 1.0:
        return None, None

    return matches[0][1], matches[0][2]


def extract_vertical_level_evidence(
    metadata_index: dict[str, Any],
    project_id: str | None = None,
    max_label_distance_pdf_points: float = 24.0,
) -> dict[str, Any]:

    if not isinstance(metadata_index, dict):
        raise TypeError("metadata_index must be a dictionary")

    if max_label_distance_pdf_points < 0:
        raise ValueError("max_label_distance_pdf_points cannot be negative")

    candidates: list[dict[str, Any]] = []
    drawing_count = 0
    page_count = 0
    skipped_numeric_count = 0

    for drawing, page, page_index in _iter_pages(metadata_index):
        page_count += 1

        drawing_id = drawing.get("drawing_id")
        filename = _text(drawing.get("filename"))
        page_number = page.get("page_number", page_index + 1)

        spans = list(_iter_spans(page))

        for span_index, span, source_text in spans:
            values = _numeric_values(source_text)

            if not values:
                continue

            for value_text in values:
                label_type, label_span = _find_nearby_label(
                    span,
                    source_text,
                    spans,
                    max_label_distance_pdf_points,
                )

                if label_type is None or label_span is None:
                    skipped_numeric_count += 1
                    continue

                source_box = _bbox(span.get("bbox"))
                label_box = _bbox(label_span.get("bbox"))

                candidate = {
                    "schema_version": SCHEMA_VERSION,
                    "stage": STAGE,
                    "project_id": project_id,
                    "drawing_id": drawing_id,
                    "filename": filename,
                    "page_number": page_number,
                    "semantic_type": label_type,
                    "value_text": value_text,
                    "unit": None,
                    "normalized_value": None,
                    "datum": None,
                    "source": {
                        "text": source_text,
                        "bbox": source_box,
                        "span_index": span.get("span_index", span_index),
                        "block_index": span.get("block_index"),
                        "line_index": span.get("line_index"),
                        "label_text": _text(label_span.get("text")),
                        "label_bbox": label_box,
                        "label_block_index": label_span.get("block_index"),
                    },
                    "evidence_status": "provisional_candidate",
                    "verification_required": True,
                    "approval_status": "unapproved",
                    "calibration_eligible": False,
                    "geometry_generation_allowed": False,
                    "classification_reason": (
                        "Explicit level label found in the same text span"
                        if label_span is span
                        else "Numeric annotation paired with an explicit nearby level label"
                    ),
                }

                candidates.append(candidate)

        drawing_count += 1

    semantic_counts: dict[str, int] = defaultdict(int)

    for candidate in candidates:
        semantic_counts[candidate["semantic_type"]] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "status": "completed",
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "metadata_index_drawing_count": drawing_count,
            "metadata_index_page_count": page_count,
            "coordinate_system": "source PDF points; x-right, y-down",
        },
        "summary": {
            "candidate_count": len(candidates),
            "skipped_unpaired_numeric_count": skipped_numeric_count,
            "verification_required": True,
            "approved_count": 0,
            "calibration_eligible_count": 0,
            "geometry_generation_allowed_count": 0,
            "semantic_type_counts": dict(sorted(semantic_counts.items())),
        },
        "candidates": candidates,
        "limitations": [
            "Extracted values are provisional source-text candidates.",
            "This stage accepts decimal-form numeric values only.",
            "Units and vertical datum are not inferred.",
            "Only explicit level labels are accepted.",
            "Spatial label pairing remains heuristic and requires review.",
            "Repeated annotations are preserved and are not independent confirmation.",
            "No floor-to-floor height or wall height is calculated.",
            "No BIM geometry is generated or approved.",
        ],
    }
