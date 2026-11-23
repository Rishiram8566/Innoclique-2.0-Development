"""Stage 4.6 universal drawing coordination and extraction diagnostics."""
from collections import Counter
from .project_profiler import profile_project
from .drawing_relationships import build_drawing_relationships
from .engineering_extractor import extract_engineering_candidates
from .universal_classifier import classify_drawing


def coordinate_project(project: dict, drawings: list[dict]) -> dict:
    enriched = []
    for item in drawings:
        filename = str(item.get("filename", "unknown.pdf"))
        text = str(item.get("text", item.get("text_preview", "")))
        classification = classify_drawing(filename, text, item.get("category"))
        extraction = extract_engineering_candidates(filename, text)
        enriched.append({**item, **classification, **extraction})

    profile = profile_project(enriched, project.get("project_type"))
    graph = build_drawing_relationships(enriched)
    sheet_summary = dict(sorted(Counter(d["sheet_type"] for d in enriched).items()))
    discipline_summary = dict(sorted(Counter(
        discipline for drawing in enriched for discipline in drawing["applicable_disciplines"]
    ).items()))
    unclassified = [d["filename"] for d in enriched if d["sheet_type"] == "unclassified"]
    low_confidence = [
        {"filename": d["filename"], "sheet_type_confidence": d["sheet_type_confidence"],
         "discipline_confidence": d["discipline_confidence"],
         "primary_discipline": d["primary_discipline"]}
        for d in enriched
        if d["sheet_type_confidence"] < 0.60 or d["discipline_confidence"] < 0.60
    ]
    dimension_count = sum(len(d["dimension_candidates"]) for d in enriched)
    low_conf_dimensions = sum(
        1 for d in enriched for candidate in d["dimension_evidence"]
        if candidate["confidence"] == "low"
    )
    discipline_undetermined = sum(d["primary_discipline"] == "undetermined" for d in enriched)
    page_count = sum(int(d.get("page_count", 0) or 0) for d in enriched)
    ocr_pages = sum(int(d.get("ocr_page_count", 0) or 0) for d in enriched)
    unreadable_pages = sum(int(d.get("image_only_or_unreadable_page_count", 0) or 0) for d in enriched)
    identity = graph["identity_coverage"]

    quality_notes = [
        "Stage 4.6 preserves Stage 4.5 drawing identity and drawing-register membership.",
        "Technical references are separated from schedule memberships; schedule listing is not a technical dependency.",
        "Explicit full-number callouts are stronger evidence; short sheet-code matches are candidates because PDF text extraction can lose spatial layout.",
        "All relationship candidates require visual verification against the source PDF; no engineering dependency is certified automatically.",
        "Extracted dimensions, levels, grids, rooms, equipment tags and drawing references are candidate data, not certified engineering facts.",
        "Stage 4.6 does not generate geometry, perform clash detection, or create BIM geometry.",
    ]
    if unreadable_pages:
        quality_notes.append(f"{unreadable_pages} page(s) remain image-only or unreadable and require review.")
    if unclassified:
        quality_notes.append(f"{len(unclassified)} drawing(s) remain unclassified and should be reviewed.")
    if discipline_undetermined:
        quality_notes.append(f"{discipline_undetermined} drawing(s) have undetermined primary discipline.")
    if graph["unresolved_references"]:
        quality_notes.append(f"{len(graph['unresolved_references'])} drawing reference(s) could not be resolved to a unique uploaded sheet.")
    if identity["drawings_without_own_code"]:
        quality_notes.append(f"{identity['drawings_without_own_code']} drawing(s) have no unambiguous own drawing number.")
    if identity.get("conflict_count") or identity.get("ambiguous_count"):
        quality_notes.append("Conflicting or ambiguous sheet identities were excluded from automatic relationship matching.")

    return {
        "schema_version": "1.5",
        "stage": "4.6",
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "project_profile": profile,
        "drawing_count": len(enriched),
        "page_count": page_count,
        "extraction_summary": {
            "ocr_available_for_any_page": any(d.get("ocr_available", False) for d in enriched),
            "ocr_page_count": ocr_pages,
            "image_only_or_unreadable_page_count": unreadable_pages,
            "extraction_modes": dict(Counter(d.get("extraction_mode", "unknown") for d in enriched)),
        },
        "sheet_type_summary": sheet_summary,
        "discipline_summary": discipline_summary,
        "relationship_graph": graph,
        "quality_summary": {
            "unclassified_drawing_count": len(unclassified),
            "unclassified_drawings": unclassified[:200],
            "low_confidence_drawing_count": len(low_confidence),
            "low_confidence_drawings": low_confidence[:200],
            "undetermined_primary_discipline_count": discipline_undetermined,
            "dimension_candidate_count": dimension_count,
            "low_confidence_dimension_count": low_conf_dimensions,
            "relationship_count": len(graph["relationships"]),
            "technical_relationship_count": len(graph["technical_relationships"]),
            "schedule_membership_count": len(graph["schedule_memberships"]),
            "unresolved_reference_count": len(graph["unresolved_references"]),
            "drawings_with_own_code": identity["drawings_with_own_code"],
            "drawings_without_own_code": identity["drawings_without_own_code"],
            "identity_conflict_count": identity.get("conflict_count", 0),
            "identity_ambiguous_count": identity.get("ambiguous_count", 0),
        },
        "drawings": enriched,
        "quality_notes": quality_notes,
    }
