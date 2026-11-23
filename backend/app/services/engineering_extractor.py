"""Stage 4.4 engineering candidate extraction with stricter context checks."""
import re
from pathlib import Path

DIM_RE = re.compile(
    r"(?<![A-Z0-9])(?:Ø\s*)?[-+]?\d{1,5}(?:,\d{3})*(?:\.\d+)?\s*(?:mm|cm|m|ft|feet|inches|inch|in)\b", re.I
)
ELEV_RE = re.compile(
    r"\b(?:F\.?\s*F\.?\s*L|S\.?\s*S\.?\s*L|T\.?\s*O\.?\s*S|T\.?\s*O\.?\s*C|B\.?\s*O\.?\s*P|"
    r"FINISHED FLOOR LEVEL|STRUCTURAL SLAB LEVEL|TOP OF(?: STEEL| CONCRETE)?)\s*[:=]?\s*[-+]?\s*\d+(?:\.\d+)?\s*(?:mm|m)?", re.I
)
LEVEL_RE = re.compile(
    r"\b(?:B\d{1,2}|BASEMENT(?:\s+\d+)?|GROUND(?:\s+FLOOR)?|MEZZANINE|PODIUM|"
    r"ROOF(?:\s+FLOOR)?|LEVEL\s*[-+]?\d+(?:\.\d+)?|FIRST FLOOR|SECOND FLOOR|"
    r"THIRD FLOOR|UPPER FLOOR|LOWER FLOOR)\b", re.I
)
# Candidate grid labels are accepted only when supported by a nearby grid cue
# or a clear axis-pair notation (e.g. A/1, 1/A, A-1).
GRID_TOKEN_RE = re.compile(r"\b[A-Z]{1,2}\s*[-/]?\s*\d{1,3}\b", re.I)
GRID_CONTEXT_RE = re.compile(r"\b(?:GRID|GRIDLINE|GRID LINE|AXIS|SETTING[- ]OUT)\b", re.I)
GRID_PAIR_RE = re.compile(r"\b(?:[A-Z]{1,2}\s*/\s*\d{1,3}|\d{1,3}\s*/\s*[A-Z]{1,2})\b", re.I)
TAG_RE = re.compile(
    r"\b(?:AHU|FAHU|FCU|CHWP|CWP|PCHWP|EF|SF|FAN|PUMP|FACP|MDB|SMDB|LVMDB|MCC|"
    r"UPS|VFD|ATS|RMU|GEN|DG|XFMR|TRANSFORMER|VALVE|METER|FIRE PUMP|"
    r"SPRINKLER|HYDRANT|EXHAUST FAN|SUPPLY FAN)"
    r"[-\s]?(?:NO\.?\s*)?\d{1,4}[A-Z]?(?:[-/]\d{1,3}[A-Z]?)?\b", re.I
)
ROOM_RE = re.compile(
    r"\b(?:FIRE PUMP ROOM|PUMP ROOM|ELECTRICAL ROOM|TRANSFORMER ROOM|MECHANICAL ROOM|"
    r"PLANT ROOM|CHILLER HALL|CONTROL ROOM|SERVER ROOM|GENERATOR ROOM|BATTERY ROOM|"
    r"TELECOM ROOM|LIFT LOBBY|STAIRCASE|MEETING ROOM|STORE ROOM|WORKSHOP|TOILET|"
    r"PANTRY|KITCHEN|BEDROOM|LIVING ROOM|OFFICE)\b", re.I
)
DRAWING_CODE_RE = re.compile(r"\b[A-Z0-9]{2,10}(?:-[A-Z0-9]{1,10}){1,6}-[A-Z]{1,6}-\d{2,8}[A-Z]?\b", re.I)

def _unique(pattern, text, limit=250):
    found, seen = [], set()
    for match in pattern.finditer(text or ""):
        value = re.sub(r"\s+", " ", match.group(0)).strip()
        key = value.upper()
        if key not in seen:
            seen.add(key)
            found.append(value)
        if len(found) >= limit:
            break
    return found

def _grid_candidates(text):
    accepted = {}
    lines = (text or "").splitlines()
    for line_no, line in enumerate(lines):
        upper = line.upper()
        context = bool(GRID_CONTEXT_RE.search(upper))
        pairs = [m.group(0).strip() for m in GRID_PAIR_RE.finditer(line)]
        if context or pairs:
            for match in GRID_TOKEN_RE.finditer(line):
                value = match.group(0).strip()
                # Avoid obvious equipment / discipline abbreviations.
                if re.match(r"^(?:AH|FA|FC|CH|EF|SF|DB|MD|SM|MV|LV|RF|DX|CP|ET|JP|TX|TR)\b", value, re.I):
                    continue
                accepted.setdefault(value.upper(), {"value": value, "line": line_no, "evidence": "grid_context" if context else "axis_pair"})
            for value in pairs:
                accepted.setdefault(value.upper(), {"value": value, "line": line_no, "evidence": "axis_pair"})
    return list(accepted.values())[:300]

def extract_engineering_candidates(filename: str, text: str) -> dict:
    body = text or ""
    upper = body.upper()
    dimensions = _unique(DIM_RE, body, 500)
    dim_items = []
    for value in dimensions:
        pos = upper.find(value.upper())
        prefix = upper[max(0, pos-45):pos]
        context = upper[max(0, pos-120):pos+len(value)+120]
        if re.search(r"(?:F\.?\s*F\.?\s*L|S\.?\s*S\.?\s*L|T\.?\s*O\.?\s*S|T\.?\s*O\.?\s*C|B\.?\s*O\.?\s*P)\s*[:=]?\s*$", prefix):
            continue
        strong = bool(re.search(
            r"\b(?:DIMENSION|DIM|SIZE|DIAMETER|WIDTH|HEIGHT|LENGTH|SPACING|CLEAR|DISTANCE|OFFSET|"
            r"CENTRE[- ]?TO[- ]?CENTRE|C/C|THK|THICKNESS)\b", context
        ))
        dim_items.append({
            "value": value,
            "confidence": "medium" if strong else "low",
            "evidence": "nearby_dimension_context" if strong else "unit_pattern_only",
        })
    elevations = _unique(ELEV_RE, body, 250)
    levels = _unique(LEVEL_RE, body, 150)
    grid_items = _grid_candidates(body)
    tags = _unique(TAG_RE, body, 300)
    rooms = _unique(ROOM_RE, body, 200)
    drawing_codes = _unique(DRAWING_CODE_RE, body, 300)
    return {
        "source_filename": Path(filename).name,
        "dimension_candidates": [item["value"] for item in dim_items],
        "dimension_evidence": dim_items,
        "level_candidates": levels,
        "level_elevation_candidates": elevations,
        "grid_reference_candidates": [item["value"] for item in grid_items],
        "grid_reference_evidence": grid_items,
        "equipment_tag_candidates": tags,
        "room_name_candidates": rooms,
        "drawing_number_candidates": drawing_codes,
        "verification_required": True,
        "extraction_warnings": [
            "Text extraction cannot establish geometric location or intended meaning.",
            "Dimensions without nearby dimension terminology remain low confidence.",
            "Grid labels require grid/axis context or explicit axis-pair notation.",
            "All extracted values require confirmation against the drawing graphics.",
        ],
    }
