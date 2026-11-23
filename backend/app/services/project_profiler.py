"""Stage 4.4 evidence-weighted, project-agnostic project profiler."""
import re
from collections import defaultdict

PROJECT_SIGNALS = {
    "residential_building": [(r"\bvilla\b", 3), (r"\bresidential\b", 3), (r"\bbedroom\b", 1),
        (r"\bliving room\b", 1), (r"\bapartment\b", 2), (r"\bdwelling\b", 2), (r"\bhousing\b", 2)],
    "commercial_building": [(r"\boffice building\b", 2), (r"\bretail\b", 2), (r"\bmall\b", 3),
        (r"\bcommercial\b", 2), (r"\btenant fit[- ]?out\b", 2), (r"\boffice tower\b", 3)],
    "healthcare": [(r"\bhospital\b", 3), (r"\bclinic\b", 3), (r"\bpatient\b", 2),
        (r"\boperating theatre\b", 3), (r"\bmedical gas\b", 2), (r"\bhealthcare\b", 3)],
    "hospitality": [(r"\bhotel\b", 3), (r"\bguest room\b", 2), (r"\brestaurant\b", 1), (r"\bbanquet\b", 2)],
    "industrial_facility": [(r"\bfactory\b", 3), (r"\bindustrial\b", 2), (r"\bproduction line\b", 3),
        (r"\bprocess plant\b", 3), (r"\bworkshop\b", 1), (r"\bmanufacturing\b", 3), (r"\bplant room\b", 1)],
    "power_facility": [(r"\bpower station\b", 4), (r"\bsubstation\b", 4), (r"\btransformer yard\b", 3),
        (r"\bswitchgear\b", 2), (r"\bgenerator hall\b", 2), (r"\bswitchyard\b", 3)],
    "water_infrastructure": [(r"\bwater treatment\b", 4), (r"\bwastewater\b", 4),
        (r"\bsewage treatment\b", 4), (r"\bpumping station\b", 3), (r"\breservoir\b", 2), (r"\bRO plant\b", 3)],
    "transport_infrastructure": [(r"\bbridge\b", 3), (r"\bhighway\b", 3), (r"\broad alignment\b", 3),
        (r"\brailway\b", 3), (r"\btunnel\b", 3), (r"\bviaduct\b", 3), (r"\binterchange\b", 2)],
    "civil_infrastructure": [(r"\bdam\b", 3), (r"\bculvert\b", 2), (r"\bretaining wall\b", 2),
        (r"\bearthworks\b", 2), (r"\bdrainage network\b", 2), (r"\bcanal\b", 2), (r"\bembankment\b", 2)],
    "oil_gas_facility": [(r"\boil and gas\b", 4), (r"\brefinery\b", 4), (r"\bgas processing\b", 4),
        (r"\bwellhead\b", 3), (r"\bpipeline station\b", 3)],
}

def profile_project(drawings: list[dict], declared_type: str | None = None) -> dict:
    scores = defaultdict(float)
    evidence = defaultdict(list)
    hits_by_type = defaultdict(set)
    for idx, drawing in enumerate(drawings):
        filename = str(drawing.get("filename", ""))
        text = str(drawing.get("text", drawing.get("text_preview", "")))
        corpus = f"{filename} {text[:50000]}".lower()
        for kind, signals in PROJECT_SIGNALS.items():
            for pattern, weight in signals:
                if re.search(pattern, corpus, re.I):
                    scores[kind] += weight
                    hits_by_type[kind].add(idx)
                    label = pattern.replace(r"\b", "").replace("\\", "")
                    if label not in evidence[kind]:
                        evidence[kind].append(label)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranked:
        inferred, confidence = "undetermined", 0.0
    else:
        inferred, top_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        separation = (top_score - runner_up) / max(top_score, 1)
        coverage = min(len(hits_by_type[inferred]) / max(len(drawings), 1) * 3, 1)
        confidence = round(max(0.0, min(0.95, 0.30 + 0.45 * separation + 0.20 * coverage)), 2)
        if len(ranked) > 1 and top_score < runner_up * 1.25:
            confidence = min(confidence, 0.55)
    return {
        "declared_project_type": declared_type,
        "inferred_project_type": inferred,
        "project_type_confidence": confidence,
        "project_type_scores": {k: round(v, 2) for k, v in ranked},
        "project_type_evidence": {k: v[:25] for k, v in evidence.items()},
        "evidence_drawing_counts": {k: len(v) for k, v in hits_by_type.items()},
        "alternative_types": [k for k, _ in ranked[1:5]],
        "profiling_note": (
            "Confidence reflects the distribution of text evidence across the drawing set, "
            "not engineering certification. Mixed-use or ambiguous projects require review."
        ),
    }
