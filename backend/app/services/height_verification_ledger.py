"""
Stage 5.3.5 - Human height-verification ledger.

Stage 5.3.4 produces wall-type height candidates (for example
"PARAPET 1.2M HIGH" = 1200 mm) that are deliberately NOT linked to any wall
geometry. This stage records a reviewer's explicit decision that a height
candidate applies (or does not apply) to specific, named wall geometry
candidates on one drawing page.

Only a confirmed, active verification produces builder height evidence in the
exact form required by ``bim_geometry_builder._validate_height_evidence``:

    {"height_mm": ..., "source_reference": "...", "status": "drawing_derived"}

Rules:

* Every verification names its reviewer, a written justification, and the
  Stage 5.3.4 evidence IDs it relies on.
* If none of the cited evidence is on the target drawing, a separate
  cross-drawing justification is mandatory.
* Height ranges cannot be confirmed as a single wall height.
* A wall candidate can have at most one active verification. To change a
  decision the old record is revoked (never deleted), then a new one is made.
* Verification passes the HEIGHT gate only. Stage 5.4 must still pass the
  calibration, source-line and mesh checks in the geometry builder.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import bim_geometry_builder as wall_builder
from app.services.wall_height_evidence_association import (
    OUTPUT_ARTIFACT_NAME as HEIGHT_ASSOCIATION_ARTIFACT_NAME,
    STAGE as HEIGHT_ASSOCIATION_STAGE,
    file_sha256,
)


STAGE = "5.3.5"
SCHEMA_VERSION = "0.2.0"
HEIGHT_SOURCES = {"annotation", "storey_height"}

LEDGER_ARTIFACT_NAME = "height_verifications_stage5_3_5.json"
RECOGNITION_ARTIFACT_NAME = "engineering_element_candidates_stage5_1_6.json"
GEOMETRY_VALIDATION_ARTIFACT_NAME = "wall_candidate_validation_stage5_3_project.json"
CALIBRATION_ARTIFACT_NAME = "drawing_scale_calibration_stage5_2.json"

MIN_JUSTIFICATION_LENGTH = 20
MAX_ELEMENTS_PER_VERIFICATION = 500
DECISIONS = {"confirmed", "rejected"}

LEDGER_POLICY = [
    "Each record is a human decision; nothing here is automatic.",
    "Records are never deleted. A decision is changed by revoking it and "
    "recording a new one.",
    "A confirmed record passes the wall-height gate only. Calibration, "
    "source-line and mesh checks still apply in Stage 5.4.",
    "Height ranges cannot be confirmed as a single wall height.",
    "Spatial proximity is not accepted as a justification on its own.",
]


class VerificationConflict(ValueError):
    """Raised when a request clashes with an existing active verification."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _clean_text(value: Any, name: str, *, minimum: int = 1, maximum: int = 2000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    text = " ".join(value.split())
    if len(text) < minimum:
        raise ValueError(f"{name} must be at least {minimum} characters")
    if len(text) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return text


def wall_element_id(drawing_id: str, page_number: int, candidate: dict[str, Any]) -> str:
    """Element ID in the same format as ``build_wall_mesh`` uses."""
    source = candidate.get("source")
    if not isinstance(source, dict):
        raise ValueError("Candidate source references are missing")
    refs = []
    for key in ("line_a", "line_b"):
        ref = source.get(key)
        if not isinstance(ref, (list, tuple)) or len(ref) != 2:
            raise ValueError(f"Candidate {key} reference is invalid")
        refs.append((int(ref[0]), int(ref[1])))
    (path_a, item_a), (path_b, item_b) = refs
    return f"{drawing_id}:p{int(page_number)}:wall:{path_a}-{item_a}:{path_b}-{item_b}"


def element_source_lines(element_id: str) -> tuple[str, frozenset[tuple[int, int]]]:
    """Split a wall element ID into (page prefix, {(path, item), (path, item)})."""
    prefix, marker, refs = element_id.rpartition(":wall:")
    if not marker:
        raise ValueError(f"Not a wall element ID: {element_id}")
    lines = []
    for ref in refs.split(":"):
        path, _, item = ref.partition("-")
        lines.append((int(path), int(item)))
    if len(lines) != 2:
        raise ValueError(f"Wall element ID must reference two lines: {element_id}")
    return prefix, frozenset(lines)


def _shared_line_conflicts(element_ids: list[str]) -> dict[str, list[str]]:
    """element_id -> other element_ids (same page) sharing a source line."""
    owners: dict[tuple[str, tuple[int, int]], list[str]] = {}
    for element_id in element_ids:
        prefix, lines = element_source_lines(element_id)
        for line in lines:
            owners.setdefault((prefix, line), []).append(element_id)
    result: dict[str, set[str]] = {}
    for members in owners.values():
        for element_id in members:
            others = {m for m in members if m != element_id}
            if others:
                result.setdefault(element_id, set()).update(others)
    return {k: sorted(v) for k, v in result.items()}


def _is_supported_wall(candidate: Any) -> bool:
    return (
        isinstance(candidate, dict)
        and candidate.get("element_type") in wall_builder.SUPPORTED_WALL_CANDIDATE_TYPES
        and candidate.get("geometry_type") in wall_builder.SUPPORTED_WALL_GEOMETRIES
        and candidate.get("verification_required") is True
    )


def _find_recognition_page(
    recognition_report: dict[str, Any], drawing_id: str, page_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    drawings = [
        d for d in recognition_report.get("drawings", [])
        if isinstance(d, dict) and d.get("drawing_id") == drawing_id
    ]
    if len(drawings) != 1:
        raise LookupError("Drawing is missing or duplicated in the recognition report")
    pages = [
        p for p in drawings[0].get("pages", [])
        if isinstance(p, dict) and p.get("page_number") == page_number
    ]
    if len(pages) != 1:
        raise LookupError(f"Recognition page {page_number} not found")
    return drawings[0], pages[0]


def _validation_index(validation_report: dict[str, Any] | None) -> dict[tuple, str]:
    """(drawing_id, page, line_a, line_b) -> geometry_status."""
    index: dict[tuple, str] = {}
    if not isinstance(validation_report, dict):
        return index
    for result in validation_report.get("results", []):
        if not isinstance(result, dict):
            continue
        refs = result.get("source_references") or {}
        try:
            key = (
                result.get("drawing_id"),
                int(result.get("page_number")),
                tuple(int(v) for v in refs.get("line_a")),
                tuple(int(v) for v in refs.get("line_b")),
            )
        except (TypeError, ValueError):
            continue
        index[key] = str(result.get("geometry_status") or "unknown")
    return index


def _validation_status(index: dict[tuple, str], drawing_id: str, page: int, candidate: dict) -> str:
    if not index:
        return "validation_report_not_available"
    source = candidate.get("source") or {}
    try:
        key = (
            drawing_id, int(page),
            tuple(int(v) for v in source.get("line_a")),
            tuple(int(v) for v in source.get("line_b")),
        )
    except (TypeError, ValueError):
        return "not_validated"
    return index.get(key, "not_validated")


def _page_calibration(
    calibration_report: dict[str, Any] | None, drawing_id: str, page_number: int,
) -> dict[str, Any]:
    record = None
    if isinstance(calibration_report, dict):
        record = (
            calibration_report.get("calibrations", {})
            .get(drawing_id, {})
            .get(str(page_number))
        )
    if not isinstance(record, dict) or record.get("status") != "calibrated":
        return {"status": "not_calibrated", "millimetres_per_pdf_point": None}
    return {
        "status": "calibrated",
        "millimetres_per_pdf_point": record.get("millimetres_per_pdf_point"),
        "verification_required": record.get("verification_required"),
        "method": record.get("method"),
        "source_reference": record.get("source_reference"),
    }


def _footprint_dimensions(candidate: dict[str, Any]) -> tuple[float, float] | None:
    bbox = candidate.get("bbox_pdf_points")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    width, height = abs(x1 - x0), abs(y1 - y0)
    if candidate.get("geometry_type") == "parallel_vertical_lines":
        return height, width
    return width, height


# ---------------------------------------------------------------------------
# Ledger state
# ---------------------------------------------------------------------------

def empty_ledger(project_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "stage": STAGE,
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "height_verification_ledger",
        "project_id": project_id,
        "created_at": now,
        "updated_at": now,
        "policy": LEDGER_POLICY,
        "verifications": [],
        "plan_placements": [],
    }


def _check_ledger(ledger: Any, project_id: str) -> list[dict[str, Any]]:
    if not isinstance(ledger, dict) or ledger.get("stage") != STAGE:
        raise ValueError("Invalid Stage 5.3.5 ledger")
    if ledger.get("project_id") != project_id:
        raise ValueError("Ledger project ownership mismatch")
    records = ledger.get("verifications")
    if not isinstance(records, list):
        raise ValueError("Ledger verifications must be a list")
    if not isinstance(ledger.setdefault("plan_placements", []), list):
        raise ValueError("Ledger plan_placements must be a list")
    return records


def active_verifications_by_element(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in ledger.get("verifications", []):
        if not isinstance(record, dict) or record.get("status") != "active":
            continue
        for element_id in record.get("element_ids", []):
            result[element_id] = record
    return result


def resolve_builder_height_evidence(
    ledger: dict[str, Any], element_id: str,
) -> dict[str, Any] | None:
    """
    Return builder height evidence for one wall element, or None.

    Only an active, confirmed verification yields evidence. Stage 5.4 calls
    this; it never falls back to an unverified height.
    """
    record = active_verifications_by_element(ledger).get(element_id)
    if not record or record.get("decision") != "confirmed":
        return None
    candidate = record.get("height_candidate") or {}
    if candidate.get("source") == "storey_height":
        # A storey height is only valid while its plan placement is unchanged.
        placement = placement_by_id(ledger, candidate.get("placement_id"))
        if (
            not placement
            or placement.get("status") != "active"
            or placement.get("storey_height_mm") != candidate.get("height_mm")
        ):
            return None
    evidence = record.get("builder_height_evidence")
    return dict(evidence) if isinstance(evidence, dict) else None


# ---------------------------------------------------------------------------
# Reviewer view of one page
# ---------------------------------------------------------------------------

def _height_candidate_views(
    height_report: dict[str, Any] | None, drawing_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(height_report, dict):
        return []
    occurrences = {
        item.get("evidence_id"): item
        for item in height_report.get("component_heights", [])
        if isinstance(item, dict)
    }
    views = []
    for candidate in height_report.get("wall_type_height_candidates", []):
        if not isinstance(candidate, dict):
            continue
        evidence = []
        for evidence_id in candidate.get("source_evidence_ids", []):
            item = occurrences.get(evidence_id) or {}
            reference = item.get("source_reference") or {}
            evidence.append({
                "evidence_id": evidence_id,
                "drawing_id": item.get("drawing_id"),
                "drawing_title": item.get("drawing_title"),
                "page_number": item.get("page_number"),
                "text": item.get("text"),
                "bbox_pdf_points": reference.get("bbox_pdf_points"),
                "on_this_drawing": item.get("drawing_id") == drawing_id,
            })
        views.append({
            "candidate_id": candidate.get("candidate_id"),
            "component_words": candidate.get("component_words", []),
            "height_mm": candidate.get("height_mm"),
            "height_range_mm": candidate.get("height_range_mm"),
            "printed_measurements": candidate.get("printed_measurements", []),
            "occurrence_count": candidate.get("occurrence_count"),
            "occurrences_on_this_drawing": sum(1 for e in evidence if e["on_this_drawing"]),
            "confirmable": candidate.get("height_mm") is not None,
            "evidence": evidence,
        })
    return views


def list_page_wall_candidates(
    *,
    recognition_report: dict[str, Any],
    drawing_id: str,
    page_number: int,
    ledger: dict[str, Any],
    height_report: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    calibration_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a reviewer needs to decide on one drawing page."""

    project_id = recognition_report.get("project_id")
    _check_ledger(ledger, project_id)
    drawing, page = _find_recognition_page(recognition_report, drawing_id, page_number)

    calibration = _page_calibration(calibration_report, drawing_id, page_number)
    scale = calibration.get("millimetres_per_pdf_point")
    validation = _validation_index(validation_report)
    active = active_verifications_by_element(ledger)

    candidates = []
    for index, candidate in enumerate(page.get("candidates", [])):
        if not _is_supported_wall(candidate):
            continue
        try:
            element_id = wall_element_id(drawing_id, page_number, candidate)
        except (TypeError, ValueError):
            continue
        dims = _footprint_dimensions(candidate)
        record = active.get(element_id)
        candidates.append({
            "element_id": element_id,
            "candidate_index": index,
            "element_type": candidate.get("element_type"),
            "geometry_type": candidate.get("geometry_type"),
            "bbox_pdf_points": candidate.get("bbox_pdf_points"),
            "length_pdf_points": round(dims[0], 3) if dims else None,
            "thickness_pdf_points": round(dims[1], 3) if dims else None,
            "length_mm_provisional": (
                round(dims[0] * scale, 1) if dims and scale else None
            ),
            "thickness_mm_provisional": (
                round(dims[1] * scale, 1) if dims and scale else None
            ),
            "confidence": candidate.get("confidence"),
            "confidence_is_calibrated_probability": False,
            "geometry_validation_status": _validation_status(
                validation, drawing_id, page_number, candidate
            ),
            "height_verification": (
                {
                    "status": record["decision"],
                    "verification_id": record["verification_id"],
                    "height_mm": (record.get("builder_height_evidence") or {}).get("height_mm"),
                }
                if record else {"status": "unverified"}
            ),
        })

    placement = find_active_placement(ledger, drawing_id, page_number)
    shared = _shared_line_conflicts([c["element_id"] for c in candidates])
    for candidate in candidates:
        candidate["shares_source_line_with"] = shared.get(candidate["element_id"], [])

    confirmed = sum(1 for c in candidates if c["height_verification"]["status"] == "confirmed")
    rejected = sum(1 for c in candidates if c["height_verification"]["status"] == "rejected")

    return {
        "stage": STAGE,
        "project_id": project_id,
        "drawing_id": drawing_id,
        "drawing_title": drawing.get("filename"),
        "page_number": page_number,
        "page_size_pdf_points": page.get("page_size_pdf_points"),
        "source_pdf_sha256": drawing.get("source_sha256"),
        "calibration": calibration,
        "height_report_available": isinstance(height_report, dict),
        "height_candidates": _height_candidate_views(height_report, drawing_id),
        "summary": {
            "wall_candidates": len(candidates),
            "height_confirmed": confirmed,
            "height_rejected": rejected,
            "unverified": len(candidates) - confirmed - rejected,
        },
        "candidates": candidates,
        "placement": placement,
        "storey_height_option": (
            {
                "available": True,
                "placement_id": placement["placement_id"],
                "height_mm": placement["storey_height_mm"],
                "base_level_key": placement["base_level"]["level_key"],
                "top_level_key": placement["top_level"]["level_key"],
                "evidence": placement.get("level_evidence", []),
            }
            if placement and placement.get("storey_height_mm") is not None
            else {
                "available": False,
                "reason": (
                    "Set this page's base and top levels (calibration section) to confirm full-height walls."
                    if not placement else "This page's placement has no top level."
                ),
            }
        ),
        "verifications_on_page": [
            record for record in ledger.get("verifications", [])
            if record.get("drawing_id") == drawing_id
            and record.get("page_number") == page_number
        ],
        "limitations": [
            "Lengths and thicknesses in mm use the page calibration, which "
            "itself requires verification.",
            "Geometry validation does not prove a candidate is a physical wall.",
            "Confirming a height here does not generate geometry.",
        ],
    }


# ---------------------------------------------------------------------------
# Create / revoke
# ---------------------------------------------------------------------------

def _evidence_view(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": reference.get("evidence_id"),
        "drawing_id": reference.get("drawing_id"),
        "drawing_title": reference.get("drawing_title"),
        "page_number": reference.get("page_number"),
        "text": reference.get("text"),
        "span_index": reference.get("span_index"),
        "bbox_pdf_points": reference.get("bbox_pdf_points"),
        "source_sha256": reference.get("source_sha256"),
    }


def _annotation_height(
    height_report: dict[str, Any], request: dict[str, Any], decision: str,
    drawing_id: str, page_number: int,
) -> dict[str, Any]:
    """Height from a Stage 5.3.4 wall-type annotation (e.g. PARAPET 1.2M HIGH)."""
    candidate_id = request.get("height_candidate_id")
    matches = [
        c for c in height_report.get("wall_type_height_candidates", [])
        if isinstance(c, dict) and c.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("Height candidate not found in the Stage 5.3.4 report")
    height_candidate = matches[0]
    height_mm = height_candidate.get("height_mm")
    if decision == "confirmed":
        if height_mm is None:
            raise ValueError("A height range cannot be confirmed as a single wall height")
        if not isinstance(height_mm, (int, float)) or height_mm <= 0:
            raise ValueError("Height candidate value is invalid")

    cited_ids = request.get("cited_evidence_ids")
    if not isinstance(cited_ids, list) or not cited_ids:
        raise ValueError("At least one cited evidence ID is required")
    if len(set(cited_ids)) != len(cited_ids):
        raise ValueError("Cited evidence IDs must be unique")
    allowed = set(height_candidate.get("source_evidence_ids", []))
    unknown = [e for e in cited_ids if e not in allowed]
    if unknown:
        raise ValueError(f"Cited evidence does not belong to this height candidate: {unknown[:5]}")

    occurrences = {
        item.get("evidence_id"): item
        for item in height_report.get("component_heights", [])
        if isinstance(item, dict)
    }
    cited = []
    for evidence_id in cited_ids:
        item = occurrences.get(evidence_id)
        if not item:
            raise ValueError(f"Cited evidence {evidence_id} is missing from the report")
        cited.append(_evidence_view({
            **(item.get("source_reference") or {}),
            "evidence_id": evidence_id,
            "drawing_id": item.get("drawing_id"),
            "drawing_title": item.get("drawing_title"),
            "page_number": item.get("page_number"),
            "text": item.get("text"),
        }))

    return {
        "height_mm": height_mm,
        "cited": cited,
        "same_drawing": any(
            c["drawing_id"] == drawing_id and c["page_number"] == page_number for c in cited
        ),
        "component": " ".join(w.lower() for w in height_candidate.get("component_words", [])) or "wall",
        "candidate": {
            "source": "annotation",
            "candidate_id": candidate_id,
            "component_words": height_candidate.get("component_words", []),
            "height_mm": height_mm,
            "height_range_mm": height_candidate.get("height_range_mm"),
            "printed_measurements": height_candidate.get("printed_measurements", []),
        },
    }


def _storey_height(ledger: dict[str, Any], drawing_id: str, page_number: int) -> dict[str, Any]:
    """Full storey height (top level - base level) from this page's active placement."""
    placement = find_active_placement(ledger, drawing_id, page_number)
    if placement is None:
        raise ValueError(
            "This page has no plan placement. Set its base and top levels in the "
            "calibration section before confirming full-height walls."
        )
    if placement.get("storey_height_mm") is None:
        raise ValueError(
            "This page's placement has no top level, so its storey height is unknown."
        )
    base, top = placement["base_level"], placement["top_level"]
    return {
        "height_mm": placement["storey_height_mm"],
        "cited": list(placement.get("level_evidence", [])),
        "same_drawing": bool(placement.get("levels_on_this_drawing")),
        "component": "full-height wall",
        "candidate": {
            "source": "storey_height",
            "candidate_id": None,
            "placement_id": placement["placement_id"],
            "component_words": ["FULL-HEIGHT WALL"],
            "height_mm": placement["storey_height_mm"],
            "height_range_mm": None,
            "base_level_key": base["level_key"],
            "top_level_key": top["level_key"],
            "printed_measurements": [f"{base['level_key']} to {top['level_key']}"],
        },
    }


def build_verification_record(
    *,
    ledger: dict[str, Any],
    recognition_report: dict[str, Any],
    height_report: dict[str, Any],
    height_report_sha256: str | None,
    request: dict[str, Any],
    validation_report: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate a reviewer request and return a new ledger record."""

    project_id = recognition_report.get("project_id")
    if not project_id:
        raise ValueError("Recognition report project_id is missing")
    records = _check_ledger(ledger, project_id)

    if not isinstance(height_report, dict) or height_report.get("stage") != HEIGHT_ASSOCIATION_STAGE:
        raise ValueError("A Stage 5.3.4 height association report is required")
    if height_report.get("project_id") != project_id:
        raise ValueError("Height report project ownership mismatch")

    if not isinstance(request, dict):
        raise ValueError("Request must be an object")

    decision = request.get("decision")
    if decision not in DECISIONS:
        raise ValueError("decision must be 'confirmed' or 'rejected'")

    reviewer = _clean_text(request.get("reviewer"), "reviewer", minimum=2, maximum=120)
    justification = _clean_text(
        request.get("justification"), "justification", minimum=MIN_JUSTIFICATION_LENGTH
    )

    drawing_id = request.get("drawing_id")
    if not isinstance(drawing_id, str) or not drawing_id:
        raise ValueError("drawing_id is required")
    try:
        page_number = int(request.get("page_number"))
    except (TypeError, ValueError):
        raise ValueError("page_number must be an integer") from None
    if page_number < 1:
        raise ValueError("page_number must be positive")

    try:
        drawing, page = _find_recognition_page(recognition_report, drawing_id, page_number)
    except LookupError as exc:
        raise ValueError(str(exc)) from None

    height_source = request.get("height_source") or "annotation"
    if height_source not in HEIGHT_SOURCES:
        raise ValueError("height_source must be 'annotation' or 'storey_height'")

    if height_source == "annotation":
        height = _annotation_height(height_report, request, decision, drawing_id, page_number)
    else:
        height = _storey_height(ledger, drawing_id, page_number)

    height_mm = height["height_mm"]
    cited = height["cited"]
    cited_ids = [c["evidence_id"] for c in cited]
    same_drawing = height["same_drawing"]

    cross_justification = request.get("cross_drawing_justification")
    if not same_drawing and height_source == "annotation":
        cross_justification = _clean_text(
            cross_justification, "cross_drawing_justification",
            minimum=MIN_JUSTIFICATION_LENGTH,
        )
    elif cross_justification is not None:
        cross_justification = _clean_text(
            cross_justification, "cross_drawing_justification", minimum=0
        ) or None

    # ---- wall elements -----------------------------------------------------
    element_ids = request.get("element_ids")
    if not isinstance(element_ids, list) or not element_ids:
        raise ValueError("At least one wall element_id is required")
    if len(element_ids) > MAX_ELEMENTS_PER_VERIFICATION:
        raise ValueError(f"At most {MAX_ELEMENTS_PER_VERIFICATION} elements per verification")
    if len(set(element_ids)) != len(element_ids):
        raise ValueError("element_ids must be unique")

    page_walls: dict[str, dict[str, Any]] = {}
    for candidate in page.get("candidates", []):
        if not _is_supported_wall(candidate):
            continue
        try:
            page_walls[wall_element_id(drawing_id, page_number, candidate)] = candidate
        except (TypeError, ValueError):
            continue

    missing = [e for e in element_ids if e not in page_walls]
    if missing:
        raise ValueError(
            f"Not a supported wall candidate on this page: {missing[:5]}"
        )

    validation = _validation_index(validation_report)
    validation_statuses = {
        element_id: _validation_status(validation, drawing_id, page_number, page_walls[element_id])
        for element_id in element_ids
    }
    if decision == "confirmed":
        invalid = [
            e for e, status in validation_statuses.items()
            if status not in ("geometry_validated", "validation_report_not_available")
        ]
        if invalid:
            raise ValueError(
                f"Wall candidates without validated geometry cannot be confirmed: {invalid[:5]}"
            )

    active = active_verifications_by_element(ledger)
    clashes = sorted({active[e]["verification_id"] for e in element_ids if e in active})
    if clashes:
        raise VerificationConflict(
            "Some wall candidates already have an active verification. "
            f"Revoke it first: {clashes}"
        )

    if decision == "confirmed":
        # Two confirmed walls built on the same source line would be stacked,
        # overlapping solids for what is most likely one physical wall.
        within = _shared_line_conflicts(list(element_ids))
        if within:
            first = sorted(within)[0]
            raise ValueError(
                "Selected wall candidates share a source line and cannot all be "
                f"confirmed together, e.g. {first} with {within[first][:3]}"
            )
        confirmed_elsewhere = [
            e for e, r in active.items() if r.get("decision") == "confirmed"
        ]
        combined = _shared_line_conflicts(confirmed_elsewhere + list(element_ids))
        overlapping = sorted(
            e for e in element_ids
            if any(o in confirmed_elsewhere for o in combined.get(e, []))
        )
        if overlapping:
            raise VerificationConflict(
                "Wall candidates share a source line with an already confirmed "
                f"wall; revoke that verification first: {overlapping[:5]}"
            )

    # ---- record --------------------------------------------------------------
    created_at = now or _now()
    height_candidate = height["candidate"]
    component = height["component"]
    verification_id = _stable_id([
        project_id, drawing_id, page_number, sorted(element_ids),
        height_candidate.get("candidate_id") or height_candidate.get("placement_id"),
        decision, created_at, len(records),
    ])
    printed = ", ".join(height_candidate.get("printed_measurements", [])) or str(height_mm)

    builder_height_evidence = None
    if decision == "confirmed":
        builder_height_evidence = {
            "height_mm": float(height_mm),
            "source_reference": (
                f"Stage 5.3.5 verification {verification_id}: {reviewer} confirmed "
                f"'{printed}' ({component}) for {len(element_ids)} wall candidate(s) on "
                f"{drawing.get('filename') or drawing_id} p{page_number}; "
                f"cited evidence {', '.join(cited_ids[:5])}"
                + (" ..." if len(cited_ids) > 5 else "")
            )[:500],
            "status": "drawing_derived",
        }

    return {
        "verification_id": verification_id,
        "stage": STAGE,
        "created_at": created_at,
        "status": "active",
        "decision": decision,
        "reviewer": reviewer,
        "justification": justification,
        "cross_drawing_justification": cross_justification,
        "project_id": project_id,
        "drawing_id": drawing_id,
        "drawing_title": drawing.get("filename"),
        "page_number": page_number,
        "source_pdf_sha256": drawing.get("source_sha256"),
        "element_ids": list(element_ids),
        "element_geometry_validation": validation_statuses,
        "height_candidate": height_candidate,
        "height_report": {
            "stage": HEIGHT_ASSOCIATION_STAGE,
            "artifact_sha256": height_report_sha256,
            "generated_at": height_report.get("generated_at"),
        },
        "cited_evidence": cited,
        "source_on_same_drawing": same_drawing,
        "semantic_confirmation": (
            {"component": component, "basis": "reviewer_confirmed"}
            if decision == "confirmed" else None
        ),
        "builder_height_evidence": builder_height_evidence,
        "gates": {
            "height": "passed" if decision == "confirmed" else "blocked_by_reviewer",
            "calibration": "checked_in_stage_5_4",
            "source_lines_and_mesh": "checked_in_stage_5_4",
        },
        "revocation": None,
    }


def apply_verification(ledger: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    ledger.setdefault("verifications", []).append(record)
    ledger["updated_at"] = record["created_at"]
    return ledger


def revoke_verification(
    ledger: dict[str, Any],
    verification_id: str,
    *,
    reviewer: Any,
    reason: Any,
    now: str | None = None,
) -> dict[str, Any]:
    """Mark an active record revoked. The record itself is kept."""
    reviewer = _clean_text(reviewer, "reviewer", minimum=2, maximum=120)
    reason = _clean_text(reason, "reason", minimum=MIN_JUSTIFICATION_LENGTH)
    matches = [
        r for r in ledger.get("verifications", [])
        if isinstance(r, dict) and r.get("verification_id") == verification_id
    ]
    if len(matches) != 1:
        raise LookupError("Verification not found")
    record = matches[0]
    if record.get("status") != "active":
        raise VerificationConflict("Verification is not active")
    revoked_at = now or _now()
    record["status"] = "revoked"
    record["revocation"] = {"reviewer": reviewer, "reason": reason, "revoked_at": revoked_at}
    ledger["updated_at"] = revoked_at
    return record


# ---------------------------------------------------------------------------
# Project artifact I/O
# ---------------------------------------------------------------------------

def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def read_optional_artifact(folder: Path, name: str) -> dict[str, Any] | None:
    path = Path(folder) / "artifacts" / name
    return _read_json_object(path) if path.is_file() else None


def read_required_artifact(folder: Path, name: str) -> dict[str, Any]:
    path = Path(folder) / "artifacts" / name
    if not path.is_file():
        raise FileNotFoundError(f"Required artifact not found: {name}")
    return _read_json_object(path)


def height_report_sha256(folder: Path) -> str | None:
    path = Path(folder) / "artifacts" / HEIGHT_ASSOCIATION_ARTIFACT_NAME
    return file_sha256(path) if path.is_file() else None


def load_ledger(folder: Path, project_id: str) -> dict[str, Any]:
    path = Path(folder) / "artifacts" / LEDGER_ARTIFACT_NAME
    if not path.is_file():
        return empty_ledger(project_id)
    ledger = _read_json_object(path)
    _check_ledger(ledger, project_id)
    return ledger


def save_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent))
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
    return path


def save_ledger(folder: Path, ledger: dict[str, Any]) -> Path:
    return save_json_atomic(Path(folder) / "artifacts" / LEDGER_ARTIFACT_NAME, ledger)


# ---------------------------------------------------------------------------
# Mesh serving gate
# ---------------------------------------------------------------------------

def _height_values(element: dict[str, Any]) -> list[float]:
    values = []
    dimensions = element.get("dimensions_mm")
    if isinstance(dimensions, dict) and isinstance(dimensions.get("height"), (int, float)):
        values.append(float(dimensions["height"]))
    provenance = element.get("provenance")
    evidence = provenance.get("height_evidence") if isinstance(provenance, dict) else None
    if isinstance(evidence, dict) and isinstance(evidence.get("height_mm"), (int, float)):
        values.append(float(evidence["height_mm"]))
    return values


def screen_mesh_elements(
    elements: list[dict[str, Any]], ledger: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split mesh elements into (served, withheld).

    An element is served only when its height is drawing-derived AND an
    active, reviewer-confirmed Stage 5.3.5 verification exists for that exact
    element with the same height. Anything else, including meshes built with
    assumed or test heights, is withheld with the reasons.
    """
    served, withheld = [], []
    for element in elements:
        element_id = str(element.get("element_id") or "")
        reasons = []

        height_status = str(element.get("height_status") or "")
        if "assumed" in height_status.lower() or "test" in height_status.lower():
            reasons.append(f"Height status is '{height_status}', not drawing-derived.")

        provenance = element.get("provenance")
        evidence = provenance.get("height_evidence") if isinstance(provenance, dict) else None
        if not isinstance(evidence, dict) or evidence.get("status") != "drawing_derived":
            reasons.append("Mesh provenance has no drawing-derived height evidence.")

        verified = resolve_builder_height_evidence(ledger, element_id) if element_id else None
        if verified is None:
            reasons.append("No active reviewer-confirmed height verification for this element.")
        else:
            heights = _height_values(element)
            if not heights or any(abs(h - float(verified["height_mm"])) > 1e-6 for h in heights):
                reasons.append(
                    f"Mesh height {heights} differs from the verified height {verified['height_mm']}."
                )

        if reasons:
            withheld.append({"element_id": element_id or None, "reasons": reasons})
        else:
            served.append(element)
    return served, withheld


# ---------------------------------------------------------------------------
# Plan placements (Stage 5.4): which level a plan sits at, its storey height,
# and the shared reference point used to stack plans into one building.
# ---------------------------------------------------------------------------

LEVEL_EVIDENCE_CAP = 10


def placement_by_id(ledger: dict[str, Any], placement_id: Any) -> dict[str, Any] | None:
    for placement in ledger.get("plan_placements", []):
        if isinstance(placement, dict) and placement.get("placement_id") == placement_id:
            return placement
    return None


def find_active_placement(ledger: dict[str, Any], drawing_id: str, page_number: int) -> dict[str, Any] | None:
    for placement in ledger.get("plan_placements", []):
        if (
            isinstance(placement, dict)
            and placement.get("status") == "active"
            and placement.get("drawing_id") == drawing_id
            and placement.get("page_number") == page_number
        ):
            return placement
    return None


def _page_size_pdf(page: dict[str, Any]) -> tuple[float, float] | None:
    value = page.get("page_size_pdf_points")
    try:
        if isinstance(value, dict):
            return float(value["width"]), float(value["height"])
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]), float(value[1])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _level_register(height_report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(height_report, dict):
        return {}
    return {
        level["level_key"]: level
        for level in height_report.get("level_register", [])
        if isinstance(level, dict) and level.get("level_key")
    }


def _occurrences_on_page(level: dict[str, Any], drawing_id: str, page_number: int) -> int:
    for drawing in level.get("drawings", []):
        pages = drawing.get("page_numbers") or [page_number]
        if drawing.get("drawing_id") == drawing_id and page_number in pages:
            return int(drawing.get("occurrence_count") or 0)
    return 0


def _level_evidence(level: dict[str, Any], drawing_id: str, page_number: int) -> list[dict[str, Any]]:
    references = [r for r in level.get("source_references", []) if isinstance(r, dict)]
    local = [r for r in references if r.get("drawing_id") == drawing_id and r.get("page_number") == page_number]
    chosen = (local or references)[:LEVEL_EVIDENCE_CAP]
    return [_evidence_view(r) for r in chosen]


def _level_summary(level: dict[str, Any], drawing_id: str, page_number: int) -> dict[str, Any]:
    return {
        "level_key": level["level_key"],
        "label": level.get("label"),
        "standard_meaning": level.get("standard_meaning"),
        "elevation_mm": level.get("elevation_mm"),
        "printed_forms": level.get("printed_forms", []),
        "occurrence_count": level.get("occurrence_count"),
        "occurrences_on_this_drawing": _occurrences_on_page(level, drawing_id, page_number),
    }


def placement_options(
    *, height_report: dict[str, Any] | None, ledger: dict[str, Any],
    drawing_id: str, page_number: int,
) -> dict[str, Any]:
    """Levels a reviewer can choose from, with a suggestion that is never auto-applied."""
    register = _level_register(height_report)
    levels = sorted(
        (_level_summary(level, drawing_id, page_number) for level in register.values()),
        key=lambda item: (item["label"] or "", item["elevation_mm"] or 0),
    )

    local = [level for level in levels if level["occurrences_on_this_drawing"] > 0]
    preference = {"SSL": 0, "FFL": 1}
    suggested_base = min(
        local,
        key=lambda l: (preference.get(l["label"], 2), -l["occurrences_on_this_drawing"], l["elevation_mm"]),
        default=None,
    )
    suggested_top = None
    if suggested_base:
        higher = [
            l for l in levels
            if l["label"] == suggested_base["label"] and l["elevation_mm"] > suggested_base["elevation_mm"]
        ]
        suggested_top = min(higher, key=lambda l: l["elevation_mm"], default=None)

    return {
        "height_report_available": bool(register),
        "levels": levels,
        "suggested_base_level_key": suggested_base["level_key"] if suggested_base else None,
        "suggested_top_level_key": suggested_top["level_key"] if suggested_top else None,
        "suggestion_basis": (
            "Most-annotated slab level on this drawing, and the next level of the same type above it. "
            "A suggestion only; the reviewer must confirm."
        ),
        "active_placement": find_active_placement(ledger, drawing_id, page_number),
        "placements_on_page": [
            p for p in ledger.get("plan_placements", [])
            if p.get("drawing_id") == drawing_id and p.get("page_number") == page_number
        ],
    }


def build_placement_record(
    *,
    ledger: dict[str, Any],
    recognition_report: dict[str, Any],
    height_report: dict[str, Any],
    request: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Validate a reviewer's plan placement and return a new ledger record."""
    project_id = recognition_report.get("project_id")
    _check_ledger(ledger, project_id)
    if not isinstance(height_report, dict) or height_report.get("project_id") != project_id:
        raise ValueError("A Stage 5.3.4 height association report for this project is required")
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")

    reviewer = _clean_text(request.get("reviewer"), "reviewer", minimum=2, maximum=120)
    justification = _clean_text(request.get("justification"), "justification", minimum=MIN_JUSTIFICATION_LENGTH)
    reference_label = _clean_text(request.get("reference_label"), "reference_label", minimum=2, maximum=120)

    drawing_id = request.get("drawing_id")
    try:
        page_number = int(request.get("page_number"))
    except (TypeError, ValueError):
        raise ValueError("page_number must be an integer") from None
    try:
        drawing, page = _find_recognition_page(recognition_report, drawing_id, page_number)
    except LookupError as exc:
        raise ValueError(str(exc)) from None

    point = request.get("reference_point_pdf")
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("reference_point_pdf must be [x, y]")
    try:
        rx, ry = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        raise ValueError("reference_point_pdf must contain numbers") from None
    size = _page_size_pdf(page)
    if size is None or not (0 <= rx <= size[0] and 0 <= ry <= size[1]):
        raise ValueError("Reference point lies outside the page")

    register = _level_register(height_report)
    base = register.get(request.get("base_level_key"))
    if base is None:
        raise ValueError("Base level not found in the Stage 5.3.4 level register")
    top = None
    if request.get("top_level_key"):
        top = register.get(request.get("top_level_key"))
        if top is None:
            raise ValueError("Top level not found in the Stage 5.3.4 level register")
        if top.get("label") != base.get("label"):
            raise ValueError(
                "Base and top levels must be the same type (e.g. SSL to SSL) so the "
                "storey height does not mix slab and finish levels"
            )
        if float(top["elevation_mm"]) <= float(base["elevation_mm"]):
            raise ValueError("Top level must be above the base level")

    base_local = _occurrences_on_page(base, drawing_id, page_number) > 0
    cross_justification = request.get("cross_drawing_justification")
    if not base_local:
        cross_justification = _clean_text(
            cross_justification, "cross_drawing_justification", minimum=MIN_JUSTIFICATION_LENGTH,
        )
    elif cross_justification is not None:
        cross_justification = _clean_text(cross_justification, "cross_drawing_justification", minimum=0) or None

    existing = find_active_placement(ledger, drawing_id, page_number)
    if existing:
        raise VerificationConflict(
            f"This page already has an active placement ({existing['placement_id']}); revoke it first."
        )

    created_at = now or _now()
    placement_id = _stable_id([
        project_id, drawing_id, page_number, base["level_key"], top and top["level_key"],
        rx, ry, created_at, len(ledger.get("plan_placements", [])),
    ])
    evidence = _level_evidence(base, drawing_id, page_number)
    if top:
        evidence += _level_evidence(top, drawing_id, page_number)
    snapped = request.get("reference_snapped_line")

    return {
        "placement_id": placement_id,
        "stage": "5.4",
        "created_at": created_at,
        "status": "active",
        "reviewer": reviewer,
        "justification": justification,
        "cross_drawing_justification": cross_justification,
        "project_id": project_id,
        "drawing_id": drawing_id,
        "drawing_title": drawing.get("filename"),
        "page_number": page_number,
        "source_pdf_sha256": drawing.get("source_sha256"),
        "base_level": _level_summary(base, drawing_id, page_number),
        "top_level": _level_summary(top, drawing_id, page_number) if top else None,
        "storey_height_mm": (
            round(float(top["elevation_mm"]) - float(base["elevation_mm"]), 3) if top else None
        ),
        "reference_point": {
            "point_pdf": [rx, ry],
            "label": reference_label,
            "snapped_line": snapped if isinstance(snapped, dict) else None,
        },
        "level_evidence": evidence,
        "levels_on_this_drawing": base_local,
        "alignment": "translation_only_plans_assumed_to_share_orientation",
        "revocation": None,
    }


def apply_placement(ledger: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    ledger.setdefault("plan_placements", []).append(record)
    ledger["updated_at"] = record["created_at"]
    return ledger


def revoke_placement(
    ledger: dict[str, Any], placement_id: str, *, reviewer: Any, reason: Any, now: str | None = None,
) -> dict[str, Any]:
    """Mark an active placement revoked. Storey-height walls that depend on it stop being served."""
    reviewer = _clean_text(reviewer, "reviewer", minimum=2, maximum=120)
    reason = _clean_text(reason, "reason", minimum=MIN_JUSTIFICATION_LENGTH)
    placement = placement_by_id(ledger, placement_id)
    if placement is None:
        raise LookupError("Placement not found")
    if placement.get("status") != "active":
        raise VerificationConflict("Placement is not active")
    revoked_at = now or _now()
    placement["status"] = "revoked"
    placement["revocation"] = {"reviewer": reviewer, "reason": reason, "revoked_at": revoked_at}
    ledger["updated_at"] = revoked_at
    return placement
