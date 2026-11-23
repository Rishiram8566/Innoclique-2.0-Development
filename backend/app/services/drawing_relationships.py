"""Stage 4.6 cross-drawing relationship engine.

Preserves Stage 4.5 identity and drawing-register membership. Adds a separate
technical-reference layer using explicit textual callouts and short sheet-code
references (for example ARC-3001) resolved against unique uploaded sheet IDs.
Every inferred edge is review-required; schedule membership is never labelled
as a technical dependency.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

CODE_PATTERN = (r"(?<![A-Z0-9])(?:[A-Z0-9]{2,10}-){2,6}"
                r"[A-Z]{1,6}-\d{1,8}[A-Z]?(?:-\d{1,4}[A-Z]?)?(?![A-Z0-9])")
CODE_RE = re.compile(CODE_PATTERN, re.I)
SHORT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,6}-\d{3,5}[A-Z]?)(?![A-Z0-9])", re.I)
OWN_LABEL_RE = re.compile(
    r"\b(?:DRAWING\s*(?:NO\.?|NUMBER|#)|DWG\.?\s*(?:NO\.?|NUMBER|#)|"
    r"SHEET\s*(?:NO\.?|NUMBER|#))\s*[:#-]?\s*(" + CODE_PATTERN + r")", re.I)
LABEL_ONLY_RE = re.compile(
    r"\b(?:DRAWING\s*(?:NO\.?|NUMBER|#)|DWG\.?\s*(?:NO\.?|NUMBER|#)|"
    r"SHEET\s*(?:NO\.?|NUMBER|#))\b", re.I)
CALLOUT_RE = re.compile(
    r"\b(?:SEE|REFER(?:RED)?\s+TO|AS\s+PER|DETAIL\s+ON|DETAILS?\s+SHEET|"
    r"CONTINUATION\s+ON|MATCH\s+LINE\s+TO|DRAWING\s*(?:NO\.?|NUMBER)|"
    r"DWG\.?\s*(?:NO\.?|NUMBER))\s*[:#-]?\s*(" + CODE_PATTERN + r")", re.I)
SCHEDULE_HINT_RE = re.compile(
    r"\b(?:DRAWING\s+SCHEDULE|SCHEDULE\s+OF\s+DRAWINGS|LIST\s+OF\s+DRAWINGS|"
    r"DRAWING\s+REGISTER|SHEET\s+INDEX|INDEX\s+OF\s+DRAWINGS|DRAWING\s+INDEX)\b", re.I)
SCHEDULE_FILENAME_RE = re.compile(
    r"\b(?:drawing[\s_-]*schedule|schedule[\s_-]*of[\s_-]*drawings|"
    r"drawing[\s_-]*register|sheet[\s_-]*index|drawing[\s_-]*index)\b", re.I)
SCHEDULE_DOC_RE = re.compile(r"\b(?:DOOR|WINDOW|ROOM|FINISH|EQUIPMENT|IRONMONGERY)\s+SCHEDULE\b", re.I)
RANGE_RE = re.compile(r"(" + CODE_PATTERN + r")\s+TO\s+(" + CODE_PATTERN + r")", re.I)


def _codes(value: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).upper() for m in CODE_RE.finditer(value or "")))


def _filename_identity(filename: str) -> list[str]:
    return _codes((filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])


def _titleblock_identity(text: str) -> list[str]:
    text = text or ""
    masked = list(text)
    for match in CALLOUT_RE.finditer(text):
        for pos in range(match.start(), match.end()):
            masked[pos] = " "
    body = "".join(masked)
    found = [m.group(1).upper() for m in OWN_LABEL_RE.finditer(body)]
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if LABEL_ONLY_RE.search(line):
            for following in lines[i + 1:i + 4]:
                if not following.strip():
                    continue
                candidates = _codes(following)
                if len(candidates) == 1:
                    found.append(candidates[0])
                break
    return list(dict.fromkeys(found))


def _resolve_identity(filename: str, text: str) -> dict[str, Any]:
    fc, tc = _filename_identity(filename), _titleblock_identity(text)
    f = fc[0] if len(fc) == 1 else None
    t = tc[0] if len(tc) == 1 else None
    if len(fc) > 1 or len(tc) > 1:
        status, own, source, confidence = "ambiguous_evidence", None, None, 0.0
    elif f and t and f == t:
        status, own, source, confidence = "corroborated", f, "filename_and_title_block", 0.99
    elif f and t and f != t:
        status, own, source, confidence = "conflict_requires_review", None, None, 0.0
    elif f:
        status, own, source, confidence = "filename_only", f, "filename", 0.88
    elif t:
        status, own, source, confidence = "title_block_only", t, "labelled_title_block", 0.90
    else:
        status, own, source, confidence = "not_identified", None, None, 0.0
    return {"own_code": own, "identity_status": status, "identity_source": source,
            "filename_code_candidates": fc, "titleblock_code_candidates": tc,
            "identity_confidence": confidence}


def _is_schedule(filename: str, text: str) -> tuple[bool, str | None]:
    if SCHEDULE_HINT_RE.search(text or ""):
        return True, "drawing_register_heading"
    if SCHEDULE_DOC_RE.search(text or "") or re.search(r"\bschedule\b", filename or "", re.I):
        return True, "discipline_schedule_heading_or_filename"
    normalized = re.sub(r"[_-]+", " ", filename or "")
    if SCHEDULE_FILENAME_RE.search(normalized):
        return True, "drawing_register_filename"
    return False, None


def _short_suffix(code: str) -> str:
    return code.rsplit("-", 1)[-1].upper()


def _explicit_refs(body: str) -> list[str]:
    refs = [m.group(1).upper() for m in CALLOUT_RE.finditer(body or "")]
    # Expand explicit numbered drawing ranges when the prefix is identical.
    for match in RANGE_RE.finditer(body or ""):
        start, end = match.group(1).upper(), match.group(2).upper()
        left, right = start.rsplit("-", 1), end.rsplit("-", 1)
        if left[0] == right[0] and left[1].isdigit() and right[1].isdigit():
            a, b = int(left[1]), int(right[1])
            if 0 <= b - a <= 50:
                refs.extend(f"{left[0]}-{n:0{len(left[1])}d}" for n in range(a, b + 1))
    return list(dict.fromkeys(refs))


def _add_unique(target: list[dict], seen: set[tuple], item: dict, key: tuple) -> None:
    if key not in seen:
        seen.add(key)
        target.append(item)


def build_drawing_relationships(drawings: list[dict]) -> dict[str, Any]:
    nodes, texts = [], []
    code_to_ids: dict[str, list[str]] = defaultdict(list)
    suffix_to_ids: dict[str, list[str]] = defaultdict(list)
    for idx, drawing in enumerate(drawings):
        filename = str(drawing.get("filename", f"drawing_{idx+1}"))
        text = str(drawing.get("text", drawing.get("text_preview", "")) or "")
        texts.append(text)
        identity = _resolve_identity(filename, text)
        node_id = str(drawing.get("drawing_id") or drawing.get("id") or idx)
        is_schedule, schedule_evidence = _is_schedule(filename, text)
        node = {"id": node_id, "filename": filename, **identity,
                "drawing_codes": _codes(filename + "\n" + text)[:300],
                "is_drawing_schedule": is_schedule,
                "schedule_detection_evidence": schedule_evidence,
                "sheet_type": drawing.get("sheet_type"),
                "primary_discipline": drawing.get("primary_discipline")}
        nodes.append(node)
        if node["own_code"]:
            code_to_ids[node["own_code"]].append(node_id)
            suffix_to_ids[_short_suffix(node["own_code"])].append(node_id)

    relationships, technical, unresolved = [], [], []
    memberships, schedule_sheets = [], []
    edge_seen, technical_seen, unresolved_seen, membership_seen = set(), set(), set(), set()

    def resolve_full(ref: str) -> list[str]:
        return code_to_ids.get(ref.upper(), [])

    def resolve_short(ref: str) -> list[str]:
        # ARC-3001 resolves only to a unique full identity ending in -ARC-3001.
        return suffix_to_ids.get(ref.rsplit("-", 1)[-1].upper(), [])

    for node, body in zip(nodes, texts):
        own = node["own_code"]
        # Explicit full drawing-number callouts have the strongest evidence.
        for ref in _explicit_refs(body):
            if ref == own:
                continue
            targets = resolve_full(ref)
            if len(targets) == 1 and targets[0] != node["id"]:
                item = {"source_id": node["id"], "target_id": targets[0],
                        "relationship": "explicit_technical_callout", "matched_reference": ref,
                        "evidence": "explicit_callout_phrase_and_full_sheet_number",
                        "confidence": 0.97, "verification_required": True}
                _add_unique(technical, technical_seen, item,
                            (node["id"], targets[0], ref, "explicit_technical_callout"))
            else:
                _add_unique(unresolved, unresolved_seen,
                            {"source_id": node["id"], "reference": ref,
                             "source": "explicit_technical_callout",
                             "reason": "target_missing_or_identity_ambiguous"},
                            (node["id"], ref, "explicit_technical_callout"))

        # Short discipline sheet IDs are common in detail bubbles. Treat as
        # candidates, not confirmed dependencies, because PDF text loses layout.
        if not node["is_drawing_schedule"]:
            for match in SHORT_CODE_RE.finditer(body):
                ref = match.group(1).upper()
                # Ignore short token if it is part of a longer full code.
                if match.start() > 0 and body[match.start()-1] == "-":
                    continue
                targets = resolve_short(ref)
                if len(targets) == 1 and targets[0] != node["id"]:
                    item = {"source_id": node["id"], "target_id": targets[0],
                            "relationship": "short_sheet_code_candidate", "matched_reference": ref,
                            "evidence": "short_sheet_code_matches_unique_uploaded_sheet_suffix",
                            "confidence": 0.68, "verification_required": True,
                            "note": "Candidate only: confirm callout marker and spatial context on the PDF page."}
                    _add_unique(technical, technical_seen, item,
                                (node["id"], targets[0], ref, "short_sheet_code_candidate"))
                elif targets:
                    _add_unique(unresolved, unresolved_seen,
                                {"source_id": node["id"], "reference": ref,
                                 "source": "short_sheet_code_candidate",
                                 "reason": "multiple_uploaded_sheets_match_short_code"},
                                (node["id"], ref, "ambiguous_short_code"))
                else:
                    _add_unique(unresolved, unresolved_seen,
                                {"source_id": node["id"], "reference": ref,
                                 "source": "short_sheet_code_candidate",
                                 "reason": "short_code_has_no_matching_uploaded_sheet"},
                                (node["id"], ref, "short_code_missing"))

    # Keep drawing-register membership distinct from technical dependencies.
    for node, body in zip(nodes, texts):
        if not node["is_drawing_schedule"] or node["schedule_detection_evidence"] not in {
            "drawing_register_heading", "drawing_register_filename"
        }:
            continue
        schedule_sheets.append({"id": node["id"], "filename": node["filename"],
                                "own_code": node["own_code"],
                                "detection_evidence": node["schedule_detection_evidence"]})
        for ref in dict.fromkeys(_codes(body)):
            if ref == node["own_code"]:
                continue
            targets = resolve_full(ref)
            if len(targets) == 1 and targets[0] != node["id"]:
                item = {"schedule_sheet_id": node["id"], "listed_sheet_id": targets[0],
                        "listed_drawing_number": ref, "relationship": "schedule_lists_sheet",
                        "evidence": node["schedule_detection_evidence"],
                        "confidence": 0.90, "verification_required": True}
                _add_unique(memberships, membership_seen, item, (node["id"], targets[0], ref))
            elif not targets:
                _add_unique(unresolved, unresolved_seen,
                            {"source_id": node["id"], "reference": ref,
                             "source": "drawing_schedule",
                             "reason": "listed_code_has_no_matching_uploaded_sheet"},
                            (node["id"], ref, "schedule_missing"))
            else:
                _add_unique(unresolved, unresolved_seen,
                            {"source_id": node["id"], "reference": ref,
                             "source": "drawing_schedule",
                             "reason": "multiple_sheets_share_target_identity"},
                            (node["id"], ref, "schedule_ambiguous"))

    # Legacy `relationships` retains Stage 4.5 schedule edges and now includes
    # separately typed technical candidates; consumers must filter by type.
    for item in technical:
        relationships.append(dict(item))
    for item in memberships:
        relationships.append({"source_id": item["schedule_sheet_id"],
                              "target_id": item["listed_sheet_id"],
                              "relationship": "schedule_lists_sheet",
                              "matched_reference": item["listed_drawing_number"],
                              "evidence": item["evidence"], "confidence": item["confidence"],
                              "verification_required": True})

    status_counts = Counter(n["identity_status"] for n in nodes)
    source_counts = Counter(n["identity_source"] for n in nodes if n["identity_source"])
    tech_explicit = sum(x["relationship"] == "explicit_technical_callout" for x in technical)
    tech_candidates = sum(x["relationship"] == "short_sheet_code_candidate" for x in technical)
    return {
        "nodes": nodes,
        "relationships": relationships,
        "technical_relationships": technical,
        "unresolved_references": unresolved[:2000],
        "schedule_sheets": schedule_sheets,
        "schedule_memberships": memberships[:2000],
        "relationship_summary": {
            "total_relationships": len(relationships),
            "technical_relationship_count": len(technical),
            "explicit_technical_callout_count": tech_explicit,
            "short_sheet_code_candidate_count": tech_candidates,
            "schedule_membership_relationship_count": len(memberships),
            "schedule_membership_count": len(memberships),
            "unresolved_reference_count": len(unresolved),
            "schedule_sheet_count": len(schedule_sheets),
        },
        "identity_coverage": {
            "drawings_with_own_code": sum(bool(n["own_code"]) for n in nodes),
            "drawings_without_own_code": sum(not n["own_code"] for n in nodes),
            "identity_sources": dict(source_counts),
            "identity_status_counts": dict(status_counts),
            "conflict_count": status_counts.get("conflict_requires_review", 0),
            "ambiguous_count": status_counts.get("ambiguous_evidence", 0),
            "duplicate_own_code_count": sum(len(ids) > 1 for ids in code_to_ids.values()),
        },
        "note": ("Stage 4.6 separates technical-reference candidates from drawing-register membership. "
                 "Short sheet-code matches are not confirmed dependencies because extracted PDF text "
                 "may lose callout layout. All relationships require visual verification."),
    }
