"""
Stage 5.3.4 - Wall-height evidence association.

Reads a Stage 5.3.3 source-evidence report and produces a component-aware
height-evidence association report:

* level register            parsed level annotations (FFL, SSL, RL ...) with
                            every source occurrence preserved;
* adjacent level intervals  differences between adjacent annotated levels of
                            the same type (storey bounds, never wall heights);
* abbreviation context      text in which the drawings themselves place a
                            level abbreviation in brackets after a
                            description, e.g. "TOP OF PARAPET (T.R.L)";
* component heights         "<value><unit> HIGH" and "HEIGHT <a> TO <b> <unit>"
                            annotations, classified ONLY by the component
                            that the annotation's own text names;
* wall-type height candidates
                            the subset whose own text names exactly one
                            wall-type component (wall, parapet, partition ...).

Rules enforced here:

* Spatial proximity is never used as proof of association.
* A height whose text names no component, or a non-wall component, is never
  treated as a wall height.
* Drawing-title context is recorded as a hint only and never assigns a
  component.
* No level, interval or height is approved, and geometry generation is never
  allowed by this stage. Every output requires human verification.
* Nothing is hardcoded to a particular project, drawing set or naming scheme.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


STAGE = "5.3.4"
SCHEMA_VERSION = "0.1.0"
SOURCE_STAGE = "5.3.3"

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


# ---------------------------------------------------------------------------
# Level annotations
# ---------------------------------------------------------------------------

_LEVEL_LABEL = (
    r"F\s*\.?\s*F\s*\.?\s*L"
    r"|S\s*\.?\s*S\s*\.?\s*L"
    r"|T\s*\.?\s*R\s*\.?\s*L"
    r"|T\s*\.?\s*O\s*\.?\s*S"
    r"|T\s*\.?\s*O\s*\.?\s*C"
    r"|R\s*\.?\s*L"
)

# A level label followed by a signed or unsigned value, e.g.
# "F.F.L = +0.55", "S.S.L = - 7.60", "FFL +6.15 m", "T.R.L +28.30".
LEVEL_VALUE_PATTERN = re.compile(
    rf"(?<![A-Z])(?P<label>{_LEVEL_LABEL})\.?"
    r"\s*(?:=|:)?\s*"
    r"(?P<sign>[+\-−–])?\s*"
    r"(?P<value>\d+(?:\.\d+)?)"
    r"(?P<dangling>\.(?!\d))?"
    r"(?:\s*(?P<unit>mm|m)\b)?",
    re.IGNORECASE,
)

# "ALL LEVELS ARE IN METRES", "LEVELS IN MM", "ALL LEVELS SHOWN IN METERS"
LEVEL_UNIT_NOTE_PATTERN = re.compile(
    r"\bLEVELS?\s+(?:ARE\s+)?(?:SHOWN\s+|GIVEN\s+|EXPRESSED\s+)?IN\s+"
    r"(?P<unit>METRES|METERS|METRE|METER|MILLIMETRES|MILLIMETERS|MM|M)\b",
    re.IGNORECASE,
)

LEVEL_LABEL_ONLY_PATTERN = re.compile(
    rf"(?<![A-Z])(?:{_LEVEL_LABEL})(?![A-Z])",
    re.IGNORECASE,
)

# Meanings are recorded only for widely standardised abbreviations.
# Anything else stays unexpanded until a drawing or a reviewer defines it.
LEVEL_ABBREVIATIONS = {
    "FFL": {
        "standard_meaning": "finished floor level",
        "meaning_basis": "standard_abbreviation",
    },
    "SSL": {
        "standard_meaning": "structural slab level",
        "meaning_basis": "standard_abbreviation",
    },
    "RL": {
        "standard_meaning": "reduced level",
        "meaning_basis": "standard_abbreviation",
    },
    "TOC": {
        "standard_meaning": "top of concrete",
        "meaning_basis": "standard_abbreviation",
    },
    "TOS": {
        "standard_meaning": None,
        "meaning_basis": "ambiguous_top_of_slab_or_top_of_steel",
    },
    "TRL": {
        "standard_meaning": None,
        "meaning_basis": "not_a_standard_abbreviation",
    },
}

# "<DESCRIPTION> (<A.B.C>)" - the drawing places an abbreviation in brackets.
ABBREVIATION_CONTEXT_PATTERN = re.compile(
    r"^\s*(?P<description>[A-Z][A-Z0-9 /&\-]{2,}?)\s*"
    r"\(\s*(?P<abbreviation>[A-Z](?:\s*\.?\s*[A-Z]){1,5}\s*\.?)\s*\)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Component height annotations
# ---------------------------------------------------------------------------

# "PARAPET 1.2M HIGH", "600mm HIGH", "(... & 26.5m HIGH)", "1200mm HT."
HEIGHT_SUFFIX_PATTERN = re.compile(
    r"(?<![\d.])(?P<first>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m)\.?\s*"
    r"(?:HIGH|HEIGHT|HT\.?)(?![A-Z])",
    re.IGNORECASE,
)

# "HEIGHT 865 TO 965 MM", "WALL HEIGHT = 3000mm", "HEIGHT: 2.4 m"
HEIGHT_PREFIX_PATTERN = re.compile(
    r"(?<![A-Z])(?:HEIGHT|HT\.?)\s*(?:=|:|OF)?\s*"
    r"(?P<first>\d+(?:\.\d+)?)\s*"
    r"(?:(?:TO|-|–|—)\s*(?P<second>\d+(?:\.\d+)?)\s*)?"
    r"(?P<unit>mm|cm|m)(?![A-Z])",
    re.IGNORECASE,
)

# "4500  DOOR OPENING HEIGHT (DH)" - schedules often omit the unit.
HEIGHT_LABEL_WITHOUT_UNIT_PATTERN = re.compile(
    r"^\s*(?P<first>\d+(?:\.\d+)?)\s+"
    r"(?P<label>(?:[A-Z]+\s+){0,4}HEIGHT)(?![A-Z])",
    re.IGNORECASE,
)

# Generic multi-discipline vocabulary. A text naming components from more
# than one class is "ambiguous" and is never treated as a wall height.
COMPONENT_CLASSES: dict[str, tuple[str, ...]] = {
    "wall": (
        "WALL", "DRYWALL", "PARAPET", "PARTITION", "BLOCKWORK", "MASONRY",
    ),
    "railing": (
        "HANDRAIL", "GUARDRAIL", "BALUSTRADE", "RAILING", "RAIL",
    ),
    "door": ("DOOR",),
    "window": ("WINDOW", "GLAZING", "SILL", "LOUVRE", "LOUVER"),
    "finish": (
        "SKIRTING", "TILE", "BACKSPLASH", "MARBLE", "PORCELAIN",
        "GRANITE", "EPOXY", "COATING", "PLASTER", "CLADDING",
    ),
    "ceiling": ("CEILING", "SOFFIT"),
    "stair": ("STAIR", "STAIRCASE", "RISER", "TREAD", "LANDING"),
    "kerb": ("KERB", "CURB", "UPSTAND"),
    "fence": ("FENCE", "GATE"),
    "equipment": ("TANK", "PUMP", "CHILLER", "EQUIPMENT", "PLINTH"),
    "clearance": ("HEADROOM", "CLEARANCE", "CLEAR HEIGHT"),
    "trench": ("TRENCH", "PIT", "MANHOLE"),
}

_COMPONENT_PATTERNS = {
    component_class: re.compile(
        r"(?<![A-Z])(?:"
        + "|".join(re.escape(word).replace(r"\ ", r"\s+") for word in words)
        + r")S?(?![A-Z])",
        re.IGNORECASE,
    )
    for component_class, words in COMPONENT_CLASSES.items()
}

# Drawing-title context. Recorded as a hint only; never assigns a component.
SHEET_CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "stair_or_railing_detail": (
        "STAIR", "STAIRCASE", "RAILING", "HANDRAIL", "BALUSTRADE",
    ),
    "door_schedule": ("DOOR SCHEDULE",),
    "window_schedule": ("WINDOW SCHEDULE",),
    "wall_section": ("WALL SECTION",),
    "section": ("SECTION",),
    "elevation": ("ELEVATION",),
    "plan": ("PLAN",),
    "detail": ("DETAIL", "DETAILS"),
    "landscape": ("LANDSCAPE", "HARDSCAPE"),
    "schedule": ("SCHEDULE",),
}

NON_WALL_SHEET_CONTEXTS = {
    "stair_or_railing_detail",
    "door_schedule",
    "window_schedule",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(payload: Any) -> str:
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _normalise_label(label: str) -> str:
    return re.sub(r"[^A-Z]", "", label.upper())


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _sheet_context(title: str | None) -> list[str]:
    if not title:
        return []
    upper = title.upper()
    return sorted(
        context
        for context, keywords in SHEET_CONTEXT_KEYWORDS.items()
        if any(
            re.search(rf"(?<![A-Z]){re.escape(k)}(?![A-Z])", upper)
            for k in keywords
        )
    )


def _components_named(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for component_class, pattern in _COMPONENT_PATTERNS.items():
        words = sorted({
            re.sub(r"\s+", " ", match.group(0).upper())
            for match in pattern.finditer(text)
        })
        if words:
            found[component_class] = words
    # "CLEAR HEIGHT" contains no wall/door word; nothing else to reconcile.
    return found


def _source_reference(record: dict[str, Any]) -> dict[str, Any]:
    reference = record.get("source_reference")
    reference = dict(reference) if isinstance(reference, dict) else {}
    reference.setdefault("drawing_id", record.get("drawing_id"))
    reference.setdefault("page_number", record.get("page_number"))
    reference.setdefault("text", record.get("text"))
    reference["evidence_id"] = record.get("evidence_id")
    return reference


# ---------------------------------------------------------------------------
# Parsers (pure functions, unit-tested individually)
# ---------------------------------------------------------------------------

def parse_level_annotations(text: str) -> list[dict[str, Any]]:
    """
    Parse every "<level label> <value>" occurrence in one text span.

    The printed unit is preserved. When no unit is printed, ``unit`` is None
    and the caller decides whether a project-level convention resolves it.
    Truncated values such as "F.F.L= +6." are returned with
    ``value_status = "incomplete_text"`` and no numeric value.
    """

    results = []
    for match in LEVEL_VALUE_PATTERN.finditer(text or ""):
        label = _normalise_label(match.group("label"))
        sign_text = match.group("sign") or ""
        negative = sign_text in {"-", "−", "–"}
        unit = (match.group("unit") or "").lower() or None

        if match.group("dangling"):
            results.append({
                "label": label,
                "printed": match.group(0).strip(),
                "value_status": "incomplete_text",
                "value": None,
                "sign_printed": bool(sign_text),
                "unit": unit,
            })
            continue

        value = float(match.group("value"))
        if negative:
            value = -value

        results.append({
            "label": label,
            "printed": match.group(0).strip(),
            "value_status": "parsed",
            "value": value,
            "sign_printed": bool(sign_text),
            "unit": unit,
        })

    return results


def parse_abbreviation_context(text: str) -> dict[str, str] | None:
    """Return {description, abbreviation} for "DESCRIPTION (A.B.C)" text."""

    match = ABBREVIATION_CONTEXT_PATTERN.match(text or "")
    if not match:
        return None
    description = re.sub(r"\s+", " ", match.group("description")).strip()
    return {
        "description": description.upper(),
        "abbreviation": _normalise_label(match.group("abbreviation")),
        "printed_abbreviation": match.group("abbreviation").strip(),
    }


def parse_component_heights(text: str) -> list[dict[str, Any]]:
    """
    Parse explicit height statements from one text span.

    Only the text itself is used. The component is taken from words in the
    same text span; nearby geometry or text is never consulted.
    """

    text = text or ""
    measurements: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and start < span[1] for start, end in occupied)

    for match in HEIGHT_PREFIX_PATTERN.finditer(text):
        first = float(match.group("first"))
        second = match.group("second")
        unit = match.group("unit").lower()
        values = [first] if second is None else [first, float(second)]
        measurements.append({
            "printed": match.group(0).strip(),
            "values": values,
            "unit": unit,
            "is_range": second is not None,
            "pattern": "height_prefix",
        })
        occupied.append(match.span())

    for match in HEIGHT_SUFFIX_PATTERN.finditer(text):
        if overlaps(match.span()):
            continue
        measurements.append({
            "printed": match.group(0).strip(),
            "values": [float(match.group("first"))],
            "unit": match.group("unit").lower(),
            "is_range": False,
            "pattern": "value_then_high",
        })
        occupied.append(match.span())

    if not measurements:
        match = HEIGHT_LABEL_WITHOUT_UNIT_PATTERN.match(text)
        if match:
            measurements.append({
                "printed": match.group(0).strip(),
                "values": [float(match.group("first"))],
                "unit": None,
                "is_range": False,
                "pattern": "value_then_height_label_no_unit",
            })

    if not measurements:
        return []

    components = _components_named(text)
    if not components:
        component_class = "unspecified"
    elif len(components) == 1:
        component_class = next(iter(components))
    else:
        component_class = "ambiguous"

    results = []
    for measurement in measurements:
        unit = measurement["unit"]
        factor = UNIT_TO_MM.get(unit) if unit else None
        values_mm = (
            [_round(v * factor) for v in measurement["values"]]
            if factor else None
        )
        results.append({
            **measurement,
            "values_mm": values_mm,
            "unit_status": "printed" if unit else "unit_not_stated",
            "component_class": component_class,
            "components_named": components,
        })
    return results


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _validate_source_report(report: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(report, dict):
        raise ValueError("Stage 5.3.3 report must be an object")

    if str(report.get("stage")) != SOURCE_STAGE:
        raise ValueError(
            f"Expected a Stage {SOURCE_STAGE} evidence report, "
            f"got stage {report.get('stage')!r}"
        )

    project_id = report.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("Stage 5.3.3 report project_id is missing")

    evidence = report.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("Stage 5.3.3 report evidence must be a list")

    return project_id, evidence


def parse_level_unit_note(text: str) -> str | None:
    """Unit stated by a drawing note such as "ALL LEVELS ARE IN METRES", else None."""
    match = LEVEL_UNIT_NOTE_PATTERN.search(text or "")
    if not match:
        return None
    word = match.group("unit").lower()
    return "mm" if word.startswith("mil") or word == "mm" else "m"


def _resolve_level_unit(
    parsed_levels: list[tuple[dict[str, Any], dict[str, Any]]],
    unit_notes: list[tuple[dict[str, Any], str]] | None = None,
) -> dict[str, Any]:
    """
    Resolve the unit for level values printed without one.

    A unit is adopted only when the project's own level annotations print
    exactly one explicit unit, or - when they print none - when the drawings'
    notes state exactly one level unit ("ALL LEVELS ARE IN METRES"). A note
    that disagrees with the annotations leaves the unit unresolved.
    """

    explicit = defaultdict(list)
    for record, level in parsed_levels:
        if level["value_status"] == "parsed" and level["unit"]:
            explicit[level["unit"]].append(record.get("evidence_id"))
    noted = defaultdict(list)
    for record, unit in unit_notes or []:
        noted[unit].append(record.get("evidence_id"))

    if len(noted) > 1 or (noted and explicit and set(noted) != set(explicit)):
        return {
            "status": "unresolved",
            "unit": None,
            "explicit_units_found": {k: len(v) for k, v in explicit.items()},
            "noted_units_found": {k: len(v) for k, v in noted.items()},
            "note": "The drawings' notes and level annotations state different level units; not converted.",
        }

    if not explicit and len(noted) == 1:
        unit, evidence_ids = next(iter(noted.items()))
        return {
            "status": "resolved_from_drawing_note",
            "unit": unit,
            "explicit_occurrence_count": 0,
            "basis_evidence_ids": sorted(set(evidence_ids))[:25],
            "note": (
                "Level values printed without a unit adopt the unit stated by the "
                "drawings' own note (e.g. 'ALL LEVELS ARE IN METRES'). Requires verification."
            ),
        }

    if len(explicit) == 1:
        unit, evidence_ids = next(iter(explicit.items()))
        return {
            "status": "resolved_from_project_annotations",
            "unit": unit,
            "explicit_occurrence_count": len(evidence_ids),
            "basis_evidence_ids": sorted(set(evidence_ids))[:25],
            "note": (
                "Level values printed without a unit adopt the single unit "
                "printed on this project's own level annotations. "
                "Requires verification."
            ),
        }

    return {
        "status": "unresolved",
        "unit": None,
        "explicit_units_found": {k: len(v) for k, v in explicit.items()},
        "note": (
            "No single explicit level unit is printed in this project; "
            "unit-less level values are not converted."
        ),
    }


def build_height_association_report(
    source_report: dict[str, Any],
    *,
    drawing_titles: dict[str, str] | None = None,
    source_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """
    Build the Stage 5.3.4 association report from a Stage 5.3.3 report.

    ``drawing_titles`` maps drawing_id -> human title/filename and is used
    only for display and sheet-context hints.
    """

    project_id, evidence = _validate_source_report(source_report)
    titles = drawing_titles or {}

    scanned = 0
    skipped_foreign = 0
    level_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    labels_without_value = 0
    abbreviation_hits: list[tuple[dict[str, Any], dict[str, str]]] = []
    height_hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unit_notes: list[tuple[dict[str, Any], str]] = []

    for record in evidence:
        if not isinstance(record, dict):
            continue
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if record.get("project_id") not in (None, project_id):
            skipped_foreign += 1
            continue

        scanned += 1

        noted_unit = parse_level_unit_note(text)
        if noted_unit:
            unit_notes.append((record, noted_unit))

        levels = parse_level_annotations(text)
        if levels:
            for level in levels:
                level_records.append((record, level))
        elif LEVEL_LABEL_ONLY_PATTERN.search(text):
            labels_without_value += 1

        abbreviation = parse_abbreviation_context(text)
        if abbreviation and abbreviation["abbreviation"] in LEVEL_ABBREVIATIONS:
            abbreviation_hits.append((record, abbreviation))

        for height in parse_component_heights(text):
            height_hits.append((record, height))

    # ---- levels ---------------------------------------------------------
    level_unit = _resolve_level_unit(level_records, unit_notes)

    register: dict[tuple[str, float], dict[str, Any]] = {}
    incomplete_levels = []
    unresolved_unit_levels = []

    for record, level in level_records:
        reference = _source_reference(record)

        if level["value_status"] != "parsed":
            incomplete_levels.append({
                "label": level["label"],
                "printed": level["printed"],
                "reason": "value_truncated_in_source_text",
                "source_reference": reference,
            })
            continue

        unit = level["unit"] or level_unit["unit"]
        if not unit:
            unresolved_unit_levels.append({
                "label": level["label"],
                "printed": level["printed"],
                "value": level["value"],
                "reason": "unit_not_stated_and_not_resolved",
                "source_reference": reference,
            })
            continue

        elevation_mm = _round(level["value"] * UNIT_TO_MM[unit])
        key = (level["label"], elevation_mm)
        entry = register.get(key)
        if entry is None:
            abbreviation = LEVEL_ABBREVIATIONS.get(level["label"], {})
            entry = register[key] = {
                "level_key": f"{level['label']}@{elevation_mm:+.0f}mm",
                "label": level["label"],
                "standard_meaning": abbreviation.get("standard_meaning"),
                "meaning_basis": abbreviation.get("meaning_basis"),
                "elevation_mm": elevation_mm,
                "printed_forms": set(),
                "unit_basis": set(),
                "drawings": defaultdict(lambda: {"pages": set(), "count": 0}),
                "source_references": [],
            }
        entry["printed_forms"].add(level["printed"])
        entry["unit_basis"].add(
            "printed" if level["unit"] else level_unit["status"]
        )
        drawing = entry["drawings"][reference.get("drawing_id")]
        drawing["pages"].add(reference.get("page_number"))
        drawing["count"] += 1
        entry["source_references"].append(reference)

    level_register = []
    for (_, _), entry in sorted(
        register.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        drawings = [
            {
                "drawing_id": drawing_id,
                "drawing_title": titles.get(drawing_id),
                "page_numbers": sorted(p for p in data["pages"] if p is not None),
                "occurrence_count": data["count"],
            }
            for drawing_id, data in sorted(entry["drawings"].items())
        ]
        level_register.append({
            "level_key": entry["level_key"],
            "label": entry["label"],
            "standard_meaning": entry["standard_meaning"],
            "meaning_basis": entry["meaning_basis"],
            "elevation_mm": entry["elevation_mm"],
            "printed_forms": sorted(entry["printed_forms"]),
            "unit_basis": sorted(entry["unit_basis"]),
            "occurrence_count": len(entry["source_references"]),
            "drawing_count": len(drawings),
            "drawings": drawings,
            "source_references": entry["source_references"],
            "verification_required": True,
            "approval_status": "unapproved",
        })

    # ---- adjacent level intervals ----------------------------------------
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for level in level_register:
        by_label[level["label"]].append(level)

    level_intervals = []
    for label, levels in sorted(by_label.items()):
        ordered = sorted(levels, key=lambda item: item["elevation_mm"])
        for lower, upper in zip(ordered, ordered[1:]):
            interval = _round(upper["elevation_mm"] - lower["elevation_mm"])
            level_intervals.append({
                "interval_id": _stable_id([
                    project_id, lower["level_key"], upper["level_key"]
                ]),
                "label": label,
                "lower_level_key": lower["level_key"],
                "upper_level_key": upper["level_key"],
                "lower_elevation_mm": lower["elevation_mm"],
                "upper_elevation_mm": upper["elevation_mm"],
                "interval_mm": interval,
                "lower_occurrence_count": lower["occurrence_count"],
                "upper_occurrence_count": upper["occurrence_count"],
                "interpretation": "difference_between_adjacent_annotated_levels",
                "is_wall_height": False,
                "is_storey_height": "not_established",
                "verification_required": True,
                "approval_status": "unapproved",
                "limitations": [
                    "Adjacent annotated levels may belong to different areas "
                    "of the same storey, not to consecutive storeys.",
                    "A level interval is not a wall height: walls may be "
                    "partial height, and slab and finish build-up are included.",
                ],
            })

    # ---- abbreviation context --------------------------------------------
    abbreviation_groups: dict[tuple[str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for record, hit in abbreviation_hits:
        abbreviation_groups[(hit["abbreviation"], hit["description"])].append(
            _source_reference(record)
        )

    abbreviation_context = [
        {
            "abbreviation": abbreviation,
            "description_text": description,
            "occurrence_count": len(references),
            "drawing_count": len({r.get("drawing_id") for r in references}),
            "interpretation": (
                "Description and bracketed abbreviation appear in one text "
                "span. It may be a definition or a label; it is not treated "
                "as the abbreviation's meaning without verification."
            ),
            "source_references": references,
            "verification_required": True,
        }
        for (abbreviation, description), references in sorted(
            abbreviation_groups.items()
        )
    ]

    # ---- component heights -----------------------------------------------
    component_heights = []
    for record, height in height_hits:
        reference = _source_reference(record)
        drawing_id = reference.get("drawing_id")
        sheet_context = _sheet_context(titles.get(drawing_id))
        component_class = height["component_class"]

        reasons = []
        wall_type = component_class == "wall"
        if not wall_type:
            if component_class == "unspecified":
                reasons.append(
                    "Annotation text does not name the component the "
                    "height belongs to."
                )
            elif component_class == "ambiguous":
                reasons.append(
                    "Annotation text names components of more than one class."
                )
            else:
                reasons.append(
                    f"Annotation text names a {component_class} component, "
                    "not a wall-type component."
                )
        if height["values_mm"] is None:
            reasons.append("Unit is not stated in the annotation text.")

        warnings = []
        conflicting = sorted(set(sheet_context) & NON_WALL_SHEET_CONTEXTS)
        if conflicting:
            warnings.append(
                "Sheet title context ("
                + ", ".join(conflicting)
                + ") indicates a non-wall detail or schedule; this height "
                "must not be used as a wall height."
            )

        component_heights.append({
            "evidence_id": record.get("evidence_id"),
            "drawing_id": drawing_id,
            "drawing_title": titles.get(drawing_id),
            "page_number": record.get("page_number"),
            "text": record.get("text"),
            "printed_measurement": height["printed"],
            "values": height["values"],
            "unit": height["unit"],
            "unit_status": height["unit_status"],
            "values_mm": height["values_mm"],
            "is_range": height["is_range"],
            "component_class": component_class,
            "components_named": height["components_named"],
            "association_basis": "component_named_in_same_text_span",
            "spatial_proximity_used_as_proof": False,
            "sheet_context_hints": sheet_context,
            "wall_height_eligible": (
                wall_type and height["values_mm"] is not None and not conflicting
            ),
            "not_wall_height_reasons": reasons,
            "warnings": warnings,
            "source_reference": reference,
            "verification_required": True,
            "approval_status": "unapproved",
            "geometry_generation_allowed": False,
        })

    # ---- wall-type height candidates --------------------------------------
    candidate_groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for item in component_heights:
        if not item["wall_height_eligible"]:
            continue
        named = tuple(item["components_named"].get("wall", []))
        candidate_groups[(named, tuple(item["values_mm"]))].append(item)

    wall_height_candidates = []
    for (named, values_mm), items in sorted(candidate_groups.items()):
        drawings: dict[str, dict[str, Any]] = {}
        for item in items:
            entry = drawings.setdefault(item["drawing_id"], {
                "drawing_id": item["drawing_id"],
                "drawing_title": item["drawing_title"],
                "page_numbers": set(),
                "occurrence_count": 0,
            })
            entry["page_numbers"].add(item["page_number"])
            entry["occurrence_count"] += 1

        is_range = len(values_mm) > 1
        wall_height_candidates.append({
            "candidate_id": _stable_id([project_id, list(named), list(values_mm)]),
            "component_words": list(named),
            "height_mm": None if is_range else values_mm[0],
            "height_range_mm": list(values_mm) if is_range else None,
            "printed_measurements": sorted({i["printed_measurement"] for i in items}),
            "occurrence_count": len(items),
            "drawing_count": len(drawings),
            "drawings": [
                {**d, "page_numbers": sorted(d["page_numbers"])}
                for d in sorted(drawings.values(), key=lambda d: d["drawing_id"])
            ],
            "association_basis": "component_named_in_annotation_text",
            "geometry_association": None,
            "geometry_association_status": (
                "not_associated: no wall geometry candidate is linked; "
                "spatial proximity was not used"
            ),
            "source_evidence_ids": [i["evidence_id"] for i in items],
            "builder_height_evidence": None,
            "verification_required": True,
            "approval_status": "unapproved",
            "geometry_generation_allowed": False,
            "next_step": (
                "A reviewer must confirm which specific wall geometry "
                "candidates this component height applies to before any "
                "builder height evidence is issued."
            ),
        })

    # ---- per-drawing level summary ----------------------------------------
    drawing_levels: dict[str, dict[str, int]] = defaultdict(dict)
    for level in level_register:
        for drawing in level["drawings"]:
            drawing_levels[drawing["drawing_id"]][level["level_key"]] = (
                drawing["occurrence_count"]
            )

    drawing_level_summary = [
        {
            "drawing_id": drawing_id,
            "drawing_title": titles.get(drawing_id),
            "annotated_levels": [
                {"level_key": key, "occurrence_count": count}
                for key, count in sorted(levels.items(), key=lambda kv: -kv[1])
            ],
            "relationship_status": "annotated_on_drawing_not_assigned",
            "verification_required": True,
        }
        for drawing_id, levels in sorted(drawing_levels.items())
    ]

    # ---- summary ------------------------------------------------------------
    class_counts: dict[str, int] = defaultdict(int)
    for item in component_heights:
        class_counts[item["component_class"]] += 1

    summary = {
        "evidence_records_scanned": scanned,
        "evidence_records_skipped_other_project": skipped_foreign,
        "level_annotations_parsed": sum(
            level["occurrence_count"] for level in level_register
        ),
        "level_annotations_incomplete": len(incomplete_levels),
        "level_annotations_unit_unresolved": len(unresolved_unit_levels),
        "level_labels_without_value": labels_without_value,
        "distinct_levels": len(level_register),
        "adjacent_level_intervals": len(level_intervals),
        "abbreviation_context_entries": len(abbreviation_context),
        "component_height_annotations": len(component_heights),
        "component_height_class_counts": dict(sorted(class_counts.items())),
        "wall_height_eligible_annotations": sum(
            1 for item in component_heights if item["wall_height_eligible"]
        ),
        "wall_type_height_candidates": len(wall_height_candidates),
        "geometry_associations": 0,
        "approved_count": 0,
        "geometry_generation_allowed_count": 0,
    }

    return {
        "stage": STAGE,
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "wall_height_evidence_association",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "status": "completed_with_verification_required",
        "source": {
            "stage": SOURCE_STAGE,
            "schema_version": source_report.get("schema_version"),
            "status": source_report.get("status"),
            "artifact_sha256": source_artifact_sha256,
        },
        "summary": summary,
        "level_unit_convention": level_unit,
        "level_register": level_register,
        "adjacent_level_intervals": level_intervals,
        "abbreviation_context": abbreviation_context,
        "drawing_level_summary": drawing_level_summary,
        "component_heights": component_heights,
        "wall_type_height_candidates": wall_height_candidates,
        "incomplete_level_annotations": incomplete_levels,
        "unit_unresolved_level_annotations": unresolved_unit_levels,
        "limitations": [
            "Nothing in this report is approved; every record requires "
            "human verification.",
            "Spatial proximity is never used to associate a height or level "
            "with geometry.",
            "Component classes come only from words in the same text span; "
            "multi-line annotations split across spans may lose their "
            "component word and are then left unspecified.",
            "Sheet-title context is a hint and never assigns a component.",
            "Adjacent level intervals are not wall or storey heights.",
            "Wall-type height candidates are not linked to any wall geometry "
            "candidate; no builder height evidence is issued at this stage.",
            "Level values printed without a unit use a project unit only "
            "when the project's own annotations print exactly one unit.",
        ],
    }


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Project artifact I/O (shared by the CLI runner and the API route)
# ---------------------------------------------------------------------------

SOURCE_ARTIFACT_NAME = "geometry_source_evidence_stage5_3_3_v2.json"
OUTPUT_ARTIFACT_NAME = "wall_height_association_stage5_3_4.json"


def _load_json_object(path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _write_json_atomic(path, payload: dict[str, Any]) -> None:
    import os
    import tempfile
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def generate_project_height_association(
    project_folder,
    *,
    overwrite: bool = False,
    source_artifact_name: str = SOURCE_ARTIFACT_NAME,
) -> tuple[dict[str, Any], Any]:
    """
    Build and save the Stage 5.3.4 report for one project folder.

    The output is fully derived from the Stage 5.3.3 artifact and contains
    no human decisions, so ``overwrite=True`` only replaces derived data.
    Returns (report, output_path).
    """

    from pathlib import Path

    folder = Path(project_folder)
    artifacts = folder / "artifacts"
    source_path = artifacts / source_artifact_name
    output_path = artifacts / OUTPUT_ARTIFACT_NAME

    if not source_path.is_file():
        raise FileNotFoundError(
            f"Stage 5.3.3 evidence artifact not found: {source_path.name}"
        )
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Stage 5.3.4 artifact already exists: {output_path.name}"
        )

    source_report = _load_json_object(source_path)

    titles: dict[str, str] = {}
    project_file = folder / "project.json"
    if project_file.is_file():
        project = _load_json_object(project_file)
        if project.get("id") and project.get("id") != source_report.get("project_id"):
            raise ValueError("Project folder and Stage 5.3.3 report project IDs differ")
        for drawing in project.get("drawings", []):
            if isinstance(drawing, dict) and drawing.get("id"):
                titles[str(drawing["id"])] = str(drawing.get("filename") or "")

    report = build_height_association_report(
        source_report,
        drawing_titles=titles,
        source_artifact_sha256=file_sha256(source_path),
    )
    report["source"]["artifact_name"] = source_path.name
    _write_json_atomic(output_path, report)
    return report, output_path
