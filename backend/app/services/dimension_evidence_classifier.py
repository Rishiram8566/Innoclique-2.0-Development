"""Stage 5.2.4: classify drawing dimension evidence without promoting it to BIM facts.

This module is deliberately read-only: it consumes Stage 5.0 page metadata and
returns a separate evidence report. It does not alter Stage 5.0 artifacts,
calibrate drawings, or create geometry.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "0.1.0"

# Full numeric token. The left boundary prevents matching the tail of 1.50M.
DIMENSION_RE = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(?P<value>(?:\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.\d{1,3})?)"
    r"\s*(?P<unit>mm|cm|m|ft|feet|in|inch|')\b",
    re.IGNORECASE,
)

DO_NOT_SCALE_RE = re.compile(
    r"\b(?:DO\s+NOT\s+SCALE(?:\s+FROM)?|NOT\s+TO\s+SCALE|"
    r"DON['’]?T\s+SCALE|NOT\s+SCALE\s+DRAWING)\b",
    re.IGNORECASE,
)

_CONTEXT_PATTERNS = (
    ("diameter", re.compile(r"(?:Ø|⌀|\bDIA(?:METER)?\.?)", re.IGNORECASE), "prefix"),
    ("height", re.compile(r"\b(?:HIGH|HEIGHT|HGT)\b", re.IGNORECASE), "either"),
    ("depth", re.compile(r"\b(?:DEPTH|DEEP)\b", re.IGNORECASE), "either"),
    ("wall_thickness", re.compile(
        r"\b(?:WALL|BLOCK\s*WALL|THICKNESS|THK|INSULATED)\b", re.IGNORECASE
    ), "after"),
    ("thickness", re.compile(r"\b(?:THICKNESS|THK)\b", re.IGNORECASE), "either"),
    ("length", re.compile(r"\b(?:LENGTH|LONG)\b", re.IGNORECASE), "either"),
    ("width", re.compile(r"\b(?:WIDTH|WIDE)\b", re.IGNORECASE), "either"),
)


def _span_text(span: dict[str, Any]) -> str:
    value = span.get("text", "")
    return value if isinstance(value, str) else str(value)


def _classify_context(text: str, start: int, end: int) -> tuple[str, str | None]:
    """Classify only from nearby wording; return generic when context is weak."""
    before = text[max(0, start - 48):start]
    after = text[end:min(len(text), end + 72)]

    # Ø/DIA is meaningful only immediately before the numeric token.
    if re.search(r"(?:Ø|⌀|\bDIA(?:METER)?\.?)\s*$", before, re.IGNORECASE):
        return "diameter", "diameter marker immediately precedes measurement"

    # A nearby suffix such as HIGH is stronger than generic nearby nouns.
    for kind, pattern, direction in _CONTEXT_PATTERNS[1:]:
        if direction in ("after", "either") and pattern.search(after):
            return kind, f"matching context follows measurement: {pattern.pattern}"
        if direction == "either" and pattern.search(before):
            return kind, f"matching context precedes measurement: {pattern.pattern}"

    # Wall descriptors after a value commonly indicate a wall build-up/thickness.
    if re.search(r"\b(?:THERMAL\s+)?INSULATED\b.*\bWALL\b|\bBLOCK\s*WALL\b|\bWALL\b",
                 after, re.IGNORECASE):
        return "wall_thickness", "wall descriptor follows measurement"

    return "dimension_unspecified", None


def classify_page_evidence(page: dict[str, Any]) -> dict[str, Any]:
    """Return classified dimension and scale-warning evidence for one page."""
    spans = page.get("text_spans") or []
    if not isinstance(spans, list):
        spans = []

    page_text = "\n".join(_span_text(span) for span in spans if isinstance(span, dict))
    no_scale_match = DO_NOT_SCALE_RE.search(page_text)
    dimensions: list[dict[str, Any]] = []

    for span_index, span in enumerate(spans):
        if not isinstance(span, dict):
            continue
        text = _span_text(span)
        for match in DIMENSION_RE.finditer(text):
            kind, reason = _classify_context(text, match.start("value"), match.end("unit"))
            dimensions.append({
                "value_text": match.group(0).strip(),
                "numeric_value_text": match.group("value"),
                "unit": match.group("unit"),
                "semantic_type": kind,
                "classification_reason": reason,
                "source": {
                    "span_index": span_index,
                    "text": text,
                    "char_start": match.start(),
                    "char_end": match.end(),
                    "bbox": span.get("bbox"),
                    "page_number": span.get("page_number", page.get("page_number")),
                    "block_index": span.get("block_index"),
                    "line_index": span.get("line_index"),
                    "span_index": span.get("span_index", span_index),
                },
                "confidence": "contextual_candidate" if reason else "unclassified_candidate",
                "verification_required": True,
                "calibration_eligible": False,
                "calibration_status": "not_eligible_without_verified_geometry_correspondence",
                "approval_status": "unapproved",
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "5.2.4",
        "page_number": page.get("page_number"),
        "dimension_evidence": dimensions,
        "dimension_evidence_count": len(dimensions),
        "scale_restriction": {
            "present": bool(no_scale_match),
            "matched_text": no_scale_match.group(0) if no_scale_match else None,
            "source_text": page_text if no_scale_match else None,
            "printed_scale_usable": False if no_scale_match else None,
            "status": "scale_prohibited_by_drawing_note" if no_scale_match else "no_prohibition_note_detected",
            "verification_required": True,
        },
        "limitations": [
            "Semantic types are text-context candidates, not verified engineering facts.",
            "Dimension evidence is not calibration evidence by itself.",
            "No-scale wording blocks use of printed scale as calibration evidence on this page.",
            "Absence of a no-scale note does not prove that a printed scale is accurate or usable.",
            "No geometry, wall height, BIM object, or approval is generated by this stage.",
        ],
    }


def classify_drawing_evidence(drawing: dict[str, Any]) -> dict[str, Any]:
    """Classify all pages in a Stage 5.0 drawing record."""
    pages = drawing.get("pages") or []
    reports = [
        classify_page_evidence(page)
        for page in pages
        if isinstance(page, dict)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "5.2.4",
        "drawing_id": drawing.get("drawing_id"),
        "filename": drawing.get("filename"),
        "source_sha256": (
            drawing.get("source_sha256")
            or (drawing.get("source") or {}).get("sha256")
        ),
        "status": "completed",
        "summary": {
            "page_count": len(reports),
            "dimension_evidence_count": sum(
                page["dimension_evidence_count"] for page in reports
            ),
            "pages_with_scale_restriction": sum(
                1 for page in reports if page["scale_restriction"]["present"]
            ),
            "calibration_eligible_count": 0,
            "verification_required": True,
        },
        "pages": reports,
        "limitations": [
            "This report is derived from native PDF text spans only.",
            "All extracted dimensions remain provisional and require review.",
            "This stage does not modify the source drawing or Stage 5.0 artifact.",
        ],
    }
