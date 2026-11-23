"""Stage 4.4 project-agnostic sheet and discipline classification."""
import re
from pathlib import Path

SHEET_RULES = {
    "cover_or_index": [r"\bcover(?: sheet| page)?\b", r"\bdrawing index\b", r"\bsheet list\b", r"\bcontents\b"],
    "site_plan": [r"\bsite plan\b", r"\bsite layout\b", r"\bsetting[- ]?out plan\b", r"\bgrading plan\b", r"\bkey plan\b"],
    "floor_plan": [r"\bfloor plan\b", r"\blevel plan\b", r"\bGA plan\b", r"\bground floor plan\b", r"\bbasement plan\b"],
    "reflected_ceiling_plan": [r"\breflected ceiling\b", r"\bRCP\b", r"\bceiling layout\b"],
    "roof_plan": [r"\broof plan\b", r"\broof layout\b"],
    "elevation": [r"\belevation(?:s)?\b", r"\bnorth elevation\b", r"\bsouth elevation\b", r"\beast elevation\b", r"\bwest elevation\b"],
    "section": [r"\bsection(?:s)?\b", r"\bcross[- ]section\b", r"\blongitudinal section\b"],
    "schedule": [r"\bschedule\b", r"\bdoor list\b", r"\bwindow list\b", r"\bequipment list\b", r"\bfixture schedule\b"],
    "detail": [r"\btypical detail\b", r"\bconnection detail\b", r"\bdetails?\b", r"\bnode detail\b"],
    "perspective_or_render": [r"\bperspective\b", r"\brender(?:ing)?\b", r"\b3D view\b"],
    "calculation_or_report": [r"\bcalculation\b", r"\bdesign report\b", r"\bquantity take[- ]?off\b"],
    "landscape_plan": [r"\blandscape\b", r"\bplanting plan\b", r"\birrigation plan\b"],
    "demolition_plan": [r"\bdemolition\b", r"\bremoval plan\b"],
    "utility_plan": [r"\butility layout\b", r"\bservices layout\b", r"\butilities plan\b"],
    "p_and_id": [r"\bP\s*&\s*ID\b", r"\bpiping and instrumentation diagram\b"],
    "profile_or_long_section": [r"\blongitudinal profile\b", r"\broad profile\b", r"\butility profile\b"],
    "foundation_plan": [r"\bfoundation plan\b", r"\bfooting layout\b", r"\bpile layout\b"],
}

DISCIPLINE_RULES = {
    "architecture": [r"\bdoor schedule\b", r"\bwindow schedule\b", r"\broom finish\b", r"\bwall finish\b",
        r"\bpartition layout\b", r"\barchitectural floor plan\b", r"\broom layout\b", r"\bspace plan\b",
        r"\bwall layout\b", r"\bjoinery\b", r"\bceiling layout\b"],
    "structural": [r"\breinforcement\b", r"\brebar\b", r"\bbar bending\b", r"\bcolumn layout\b",
        r"\bbeam layout\b", r"\bfooting layout\b", r"\bfoundation plan\b", r"\bstructural framing\b",
        r"\bformwork\b", r"\bslab reinforcement\b", r"\bstructural general arrangement\b",
        r"\bprecast\b", r"\bsteel connection\b", r"\bstructural steel\b"],
    "electrical": [r"\bsingle[- ]line diagram\b", r"\bSLD\b", r"\bswitchgear\b", r"\bcable tray\b",
        r"\blighting layout\b", r"\bpower layout\b", r"\bload schedule\b", r"\bdistribution board\b",
        r"\bcable schedule\b", r"\bearthing\b", r"\blighting protection\b", r"\bcontainment layout\b"],
    "mechanical_hvac": [r"\bHVAC\b", r"\bair handling unit\b", r"\bAHU\b", r"\bduct layout\b",
        r"\bventilation layout\b", r"\bcooling tower\b", r"\bchilled water\b", r"\bmechanical layout\b",
        r"\bair conditioning\b", r"\bexhaust fan\b", r"\bchiller\b"],
    "plumbing_drainage": [r"\bplumbing layout\b", r"\bwater supply layout\b", r"\bdrainage layout\b",
        r"\bsanitary layout\b", r"\bstorm water\b", r"\bsewer network\b", r"\bsoil and waste\b",
        r"\bdomestic water\b", r"\bpotable water\b", r"\brainwater\b", r"\bwater services\b"],
    "fire_protection": [r"\bfire alarm layout\b", r"\bfire fighting layout\b", r"\bsprinkler layout\b",
        r"\bfire hydrant\b", r"\bfire protection layout\b", r"\bfire detection\b",
        r"\bfire suppression\b", r"\bfire hose reel\b", r"\bfire services\b"],
    "civil_site": [r"\broad alignment\b", r"\broad profile\b", r"\bgrading plan\b", r"\bearthworks\b",
        r"\bkerb layout\b", r"\bpavement layout\b", r"\btopographic survey\b", r"\bcontour plan\b",
        r"\bsite development\b", r"\broadworks\b", r"\broad drainage\b", r"\bcut and fill\b"],
    "landscape": [r"\bplanting plan\b", r"\blandscape layout\b", r"\birrigation layout\b",
        r"\bsoftscape\b", r"\bhardscape\b", r"\bplant schedule\b"],
    "process": [r"\bprocess flow diagram\b", r"\bP\s*&\s*ID\b", r"\bprocess equipment\b",
        r"\bpipe rack\b", r"\bprocess piping\b", r"\bprocess flow\b", r"\bline list\b",
        r"\binstrument index\b", r"\bcontrol philosophy\b"],
    "survey_geospatial": [r"\btopographic survey\b", r"\bcontrol points\b", r"\bcoordinate schedule\b",
        r"\bsetting[- ]?out coordinates\b", r"\bbenchmark\b", r"\btraverse\b"],
}

SERIES_MAP = {
    "ARC": "architecture", "ARCH": "architecture", "A": "architecture",
    "STR": "structural", "ST": "structural", "S": "structural",
    "ELE": "electrical", "EL": "electrical", "E": "electrical",
    "MEC": "mechanical_hvac", "MECH": "mechanical_hvac", "ME": "mechanical_hvac", "M": "mechanical_hvac",
    "PLB": "plumbing_drainage", "PLBG": "plumbing_drainage", "PL": "plumbing_drainage", "P": "plumbing_drainage",
    "FIR": "fire_protection", "FIRE": "fire_protection", "FP": "fire_protection",
    "CIV": "civil_site", "C": "civil_site", "LAN": "landscape", "L": "landscape",
    "PRO": "process", "PIP": "process", "INS": "process",
}
CODE_RE = re.compile(r"\b[A-Z0-9]{2,10}(?:-[A-Z0-9]{1,10}){1,6}-([A-Z]{1,6})-\d{2,8}[A-Z]?\b", re.I)

def _matches(text, patterns):
    return [p for p in patterns if re.search(p, text or "", re.I)]

def classify_drawing(filename: str, text: str = "", declared_category=None) -> dict:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    body = re.sub(r"\s+", " ", text or "")
    title_window = body[:12000]
    # Sheet type: filename is strongest; otherwise use the extracted sheet text.
    sheet_hits = []
    for label, patterns in SHEET_RULES.items():
        hits = _matches(stem, patterns)
        if hits:
            sheet_hits.append((label, 8 + len(hits), "filename", hits))
    if not sheet_hits:
        for label, patterns in SHEET_RULES.items():
            hits = _matches(title_window, patterns)
            if hits:
                sheet_hits.append((label, len(hits), "text", hits))
    sheet_hits.sort(key=lambda x: (-x[1], x[0]))
    best = sheet_hits[0] if sheet_hits else None
    sheet_type = best[0] if best else (declared_category or "unclassified")
    sheet_conf = 0.0
    if best:
        sheet_conf = 0.92 if best[2] == "filename" else min(0.84, 0.48 + 0.07 * best[1])
        if len(sheet_hits) > 1 and sheet_hits[1][1] == best[1]:
            sheet_conf -= 0.12

    # Search the complete extracted text but cap pathological files.
    corpus = (stem + " " + body[:50000]).lower()
    discipline_scores = {}
    discipline_evidence = {}
    for label, patterns in DISCIPLINE_RULES.items():
        hits = _matches(corpus, patterns)
        if hits:
            filename_hits = _matches(stem, patterns)
            score = 5 * len(filename_hits) + 2 * len(hits)
            discipline_scores[label] = score
            discipline_evidence[label] = hits[:20]

    series_values = [m.group(1).upper() for m in CODE_RE.finditer(filename)]
    series_source = "filename"
    if not series_values:
        # Drawing-series code in the extracted title block / sheet text.
        series_values = [m.group(1).upper() for m in CODE_RE.finditer(body[:50000])]
        series_source = "drawing_text"
    series_evidence = []
    for series in series_values:
        mapped = SERIES_MAP.get(series)
        if mapped:
            discipline_scores[mapped] = discipline_scores.get(mapped, 0) + (9 if series_source == "filename" else 6)
            series_evidence.append(f"drawing_series:{series}:{series_source}")

    ranked = sorted(discipline_scores.items(), key=lambda item: (-item[1], item[0]))
    primary = ranked[0][0] if ranked else "undetermined"
    max_score = ranked[0][1] if ranked else 0
    # Retain multiple disciplines only when each has meaningful independent evidence.
    applicable = [name for name, score in ranked if score >= max(5, max_score * 0.45)]
    confidence = round(min(0.95, 0.40 + max_score / 24), 2) if max_score else 0.0
    if len(ranked) > 1 and ranked[1][1] >= max_score * 0.85:
        confidence = min(confidence, 0.60)

    return {
        "sheet_type": sheet_type,
        "sheet_type_confidence": round(max(0.0, sheet_conf), 2),
        "primary_discipline": primary,
        "applicable_disciplines": applicable,
        "discipline_confidence": confidence,
        "classification_evidence": {
            "sheet_type": best[3] if best else [],
            "sheet_type_source": best[2] if best else "none",
            "disciplines": discipline_evidence,
            "drawing_series": series_values[:100],
            "drawing_series_evidence": series_evidence,
        },
        "classification_warning": (
            "Text-based candidates only. Confirm discipline and sheet type against the "
            "title block, drawing graphics and project-specific conventions."
        ),
    }
