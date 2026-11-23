"""Stage 5.2.8 advanced engineering dimension evidence classifier.

This classifier enriches text-context labels while keeping every result
provisional. It does not calibrate drawings, approve dimensions, or create BIM.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

SCHEMA_VERSION = "0.2.1"
CLASSIFICATION_STAGE = "5.2.8"

# Match complete numeric measurement tokens. The dot in the negative
# lookbehind prevents extracting 50M from the tail of 1.50M.
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

DIAMETER_PREFIX_RE = re.compile(
    r"(?:Ø|⌀|\bDIA(?:METER)?\.?)\s*$",
    re.IGNORECASE,
)
ELEVATION_RE = re.compile(
    r"\b(?:FFL|SSL|TOS|TOC|BOP|TOP OF|LEVEL|LVL)\b",
    re.IGNORECASE,
)
HEIGHT_RE = re.compile(r"\b(?:HIGH|HEIGHT|HGT|PARAPET)\b", re.IGNORECASE)
DEPTH_RE = re.compile(r"\b(?:DEPTH|DEEP|TRENCH|EXCAVATION)\b", re.IGNORECASE)
WIDTH_RE = re.compile(r"\b(?:WIDTH|WIDE)\b", re.IGNORECASE)
LENGTH_RE = re.compile(r"\b(?:LENGTH|LONG)\b", re.IGNORECASE)
VOLUME_RE = re.compile(
    r"(?:\bm[³3]\b|\bm\s*\^\s*3\b|\bCUBIC\s+MET(?:ER|RE)S?\b)",
    re.IGNORECASE,
)
TANK_RE = re.compile(
    r"\b(?:WATER\s+)?TANK\b|\bCISTERN\b|\bRESERVOIR\b",
    re.IGNORECASE,
)

# Material/layer categories are evaluated before generic THK/THICK patterns.
MATERIAL_RULES = (
    (
        "waterproofing_thickness",
        re.compile(r"\b(?:WATERPROOF(?:ING)?|BITUMEN|DAMP[- ]PROOF)\b", re.I),
    ),
    (
        "plaster_thickness",
        re.compile(r"\b(?:PLASTER|CEMENT\s*/?\s*SAND|RENDER)\b", re.I),
    ),
    (
        "concrete_thickness",
        re.compile(
            r"\b(?:P\.?C\.?C\.?|R\.?C\.?C\.?|CONCRETE|SCREED)\b",
            re.I,
        ),
    ),
    (
        "ceiling_thickness",
        re.compile(r"\b(?:CEILING|GYPSUM\s+BOARD|CEILING\s+BOARD)\b", re.I),
    ),
    (
        "glass_thickness",
        re.compile(r"\b(?:GLASS|GLAZ(?:ING|ED)|AIR\s+GAP|LOW[- ]E)\b", re.I),
    ),
    (
        "wall_thickness",
        re.compile(
            r"\b(?:WALL|BLOCK\s*WALL|BLOCKWORK|MASONRY\s+SOLID\s+BLOCK|SOLID\s+BLOCK)\b",
            re.I,
        ),
    ),
    (
        "insulation_thickness",
        re.compile(r"\b(?:INSULATION|INSULATED|THERMAL)\b", re.I),
    ),
    (
        "finish_thickness",
        re.compile(
            r"\b(?:GRANITE|TILE|TILING|PORCELAIN|MARBLE|VANITY\s+TOP|FLOOR\s+FINISH)\b",
            re.I,
        ),
    ),
)

THICKNESS_RE = re.compile(r"\b(?:THICKNESS|THICK|THK)\b", re.I)
COMPONENT_RE = re.compile(
    r"\b(?:DOOR|WINDOW|FRAME|SHUTTER|OPENING|EQUIPMENT|LIFT)\b",
    re.I,
)


def _span_text(span: dict[str, Any]) -> str:
    value = span.get("text", "")
    return value if isinstance(value, str) else str(value)


def _normalise_unit(unit: str) -> str:
    key = unit.strip().lower()
    return {
        "mm": "mm",
        "cm": "cm",
        "m": "m",
        "ft": "ft",
        "feet": "ft",
        "in": "in",
        "inch": "in",
        "'": "ft",
    }.get(key, key)


def _context_window(
    text: str,
    start: int,
    end: int,
) -> tuple[str, str, str]:
    before = text[max(0, start - 90):start]
    after = text[end:min(len(text), end + 110)]
    return before, after, before + text[start:end] + after


def _classify_context(
    text: str,
    start: int,
    end: int,
    unit: str,
) -> tuple[str, str, str]:
    """Return semantic type, rationale, and context window."""
    before, after, context = _context_window(text, start, end)

    if DIAMETER_PREFIX_RE.search(before):
        return (
            "diameter",
            "diameter symbol or DIA marker immediately precedes value",
            context,
        )

    # FFL/SSL/TOS etc. identify an elevation, not a physical object height.
    if ELEVATION_RE.search(before[-45:]) or re.search(
        r"\b(?:FFL|SSL|TOS|TOC|BOP|LEVEL|LVL)\b",
        after[:45],
        re.I,
    ):
        return (
            "elevation",
            "floor/structural level marker near measurement",
            context,
        )

    # Tank volume only when explicit cubic units are present.
    if TANK_RE.search(context) and VOLUME_RE.search(context):
        return (
            "volume_candidate",
            "tank context with explicit cubic unit",
            context,
        )

    # A tank word without an explicit cubic unit is not enough to infer volume.
    if TANK_RE.search(context) and re.search(
        r"\b\d+\s*[xX×]\s*\d+",
        context,
    ):
        return (
            "dimension_unspecified",
            "tank dimensions/volume notation is ambiguous",
            context,
        )

    for category, pattern in MATERIAL_RULES:
        has_material_context = pattern.search(context)
        has_explicit_thickness_wording = THICKNESS_RE.search(context)

        # Waterproofing drawing notes commonly use "25MM WATERPROOF"
        # without spelling out THK/THICK. Keep this as a candidate only.
        # A measurement immediately followed by a material/layer description
        # is useful semantic evidence even when THK/THICK is not written.
        # Keep this rule local to the same extracted text span and provisional.
        material_follows_value = bool(pattern.search(after[:65]))
        if has_material_context and (
            has_explicit_thickness_wording
            or category == "waterproofing_thickness"
            or material_follows_value
        ):
            return (
                category,
                f"material context identified: {pattern.pattern}",
                context,
            )

    # Explicit high/height wording is stronger than generic material nouns.
    if HEIGHT_RE.search(after[:70]) or HEIGHT_RE.search(before[-55:]):
        return (
            "height",
            "explicit HIGH/HEIGHT/HGT/PARAPET context",
            context,
        )

    if DEPTH_RE.search(after[:85]) or DEPTH_RE.search(before[-65:]):
        return (
            "depth",
            "explicit depth/trench context",
            context,
        )

    if WIDTH_RE.search(after[:70]) or WIDTH_RE.search(before[-55:]):
        return (
            "width",
            "explicit WIDTH/WIDE context",
            context,
        )

    if LENGTH_RE.search(after[:70]) or LENGTH_RE.search(before[-55:]):
        return (
            "length",
            "explicit LENGTH/LONG context",
            context,
        )

    # Generic thickness is deliberately separate from structural wall thickness.
    if THICKNESS_RE.search(after[:90]) or THICKNESS_RE.search(before[-65:]):
        return (
            "component_thickness",
            "explicit THICK/THK wording without a specific material category",
            context,
        )

    if COMPONENT_RE.search(context):
        return (
            "component_size",
            "door/window/equipment context; dimension role not resolved",
            context,
        )

    return (
        "dimension_unspecified",
        "insufficient local engineering context",
        context,
    )


def _stable_evidence_family(
    drawing_id: Any,
    page_number: Any,
    semantic_type: str,
    value_text: str,
    source_text: str,
) -> str:
    """Stable within the same drawing/page/context; not a cross-drawing identity."""
    canonical = "|".join([
        str(drawing_id or ""),
        str(page_number or ""),
        semantic_type,
        re.sub(r"\s+", " ", value_text.strip()).lower(),
        re.sub(r"\s+", " ", source_text.strip()).lower(),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def classify_page_evidence(
    page: dict[str, Any],
    *,
    drawing_id: Any = None,
) -> dict[str, Any]:
    spans = page.get("text_spans") or []
    if not isinstance(spans, list):
        spans = []

    page_text = "\n".join(
        _span_text(span)
        for span in spans
        if isinstance(span, dict)
    )
    no_scale_match = DO_NOT_SCALE_RE.search(page_text)
    dimensions: list[dict[str, Any]] = []

    for span_index, span in enumerate(spans):
        if not isinstance(span, dict):
            continue

        text = _span_text(span)

        for match in DIMENSION_RE.finditer(text):
            original_value = match.group(0).strip()
            unit = match.group("unit")

            semantic, reason, context = _classify_context(
                text,
                match.start("value"),
                match.end("unit"),
                unit,
            )

            # Preserve exact source text and offsets; normalized unit is additive.
            evidence = {
                "value_text": original_value,
                "numeric_value_text": match.group("value"),
                "unit": unit,
                "normalized_unit": _normalise_unit(unit),
                "semantic_type": semantic,
                "classification_reason": reason,
                "context_window": context,
                "source": {
                    "span_index": span_index,
                    "text": text,
                    "char_start": match.start(),
                    "char_end": match.end(),
                    "bbox": span.get("bbox"),
                    "page_number": span.get(
                        "page_number",
                        page.get("page_number"),
                    ),
                    "block_index": span.get("block_index"),
                    "line_index": span.get("line_index"),
                    "span_index_original": span.get(
                        "span_index",
                        span_index,
                    ),
                },
                "confidence": (
                    "contextual_candidate"
                    if semantic != "dimension_unspecified"
                    else "unclassified_candidate"
                ),
                "verification_required": True,
                "calibration_eligible": False,
                "calibration_status": (
                    "not_eligible_without_verified_geometry_correspondence"
                ),
                "approval_status": "unapproved",
            }

            evidence["evidence_family_id"] = _stable_evidence_family(
                drawing_id,
                page.get("page_number"),
                semantic,
                original_value,
                text,
            )

            dimensions.append(evidence)

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": CLASSIFICATION_STAGE,
        "page_number": page.get("page_number"),
        "dimension_evidence": dimensions,
        "dimension_evidence_count": len(dimensions),
        "scale_restriction": {
            "present": bool(no_scale_match),
            "matched_text": (
                no_scale_match.group(0)
                if no_scale_match
                else None
            ),
            "source_text": page_text if no_scale_match else None,
            "printed_scale_usable": False if no_scale_match else None,
            "status": (
                "scale_prohibited_by_drawing_note"
                if no_scale_match
                else "no_prohibition_note_detected"
            ),
            "verification_required": True,
        },
        "limitations": [
            "Semantic types are contextual candidates, not verified engineering facts.",
            "Unit normalization is additive; original value text and unit are preserved.",
            "Repeated evidence across drawings is not treated as independent confirmation.",
            "Evidence family IDs identify matching records only within the same drawing/page context.",
            "No dimension is calibration eligible or approved by this classifier.",
            "No geometry, wall height, BIM object, or approval is generated.",
        ],
    }


def classify_drawing_evidence(
    drawing: dict[str, Any],
) -> dict[str, Any]:
    pages = drawing.get("pages") or []

    reports = [
        classify_page_evidence(
            page,
            drawing_id=drawing.get("drawing_id"),
        )
        for page in pages
        if isinstance(page, dict)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": CLASSIFICATION_STAGE,
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
                page["dimension_evidence_count"]
                for page in reports
            ),
            "pages_with_scale_restriction": sum(
                1
                for page in reports
                if page["scale_restriction"]["present"]
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
