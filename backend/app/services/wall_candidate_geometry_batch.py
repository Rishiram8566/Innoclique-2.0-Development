from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone

import ijson

from app.services import wall_candidate_geometry_validator as validator


def wall_candidates(drawing):
    grouped = defaultdict(list)
    malformed = []

    for page in drawing.get("pages", []):
        if not isinstance(page, dict):
            continue

        for candidate in page.get("candidates", []):
            if (
                not isinstance(candidate, dict)
                or candidate.get("element_type")
                not in validator.builder.SUPPORTED_WALL_CANDIDATE_TYPES
            ):
                continue

            try:
                page_number = int(candidate.get("page_number", 0))
                if page_number < 1:
                    raise ValueError("Invalid page number")
                grouped[page_number].append(candidate)
            except (TypeError, ValueError) as exc:
                malformed.append((candidate, str(exc)))

    return grouped, malformed


def failed_result(candidate, message, drawing):
    return {
        "element_type": candidate.get("element_type"),
        "geometry_type": candidate.get("geometry_type"),
        "page_number": candidate.get("page_number"),
        "source_references": candidate.get("source"),
        "candidate_confidence": candidate.get("confidence"),
        "candidate_verification_required": True,
        "approval_status": "not_approved",
        "geometry_status": "geometry_invalid",
        "measurements": {},
        "errors": [str(message)],
        "semantic_status": "unconfirmed_wall_hypothesis",
        "source_sha256": drawing.get("source_sha256"),
    }


def validate_one(candidate, source_map, recognition_drawing):
    return validator.validate_candidate(
        candidate,
        source_map,
        recognition_drawing,
    )


def process_drawing(geometry_drawing, recognition_drawing):
    drawing_id = recognition_drawing.get("drawing_id")
    filename = recognition_drawing.get("filename")

    recognition_hash = recognition_drawing.get("source_sha256")
    geometry_hash = (
        geometry_drawing.get("source", {}).get("sha256")
    )

    grouped, malformed = wall_candidates(recognition_drawing)
    candidates = [
        candidate
        for page_candidates in grouped.values()
        for candidate in page_candidates
    ]

    results = []

    if recognition_hash and geometry_hash and recognition_hash != geometry_hash:
        for candidate in candidates:
            results.append(
                failed_result(
                    candidate,
                    "Recognition and geometry source PDF hashes do not match",
                    recognition_drawing,
                )
            )
        for candidate, error in malformed:
            results.append(
                failed_result(candidate, error, recognition_drawing)
            )

        return drawing_id, filename, results, "source_hash_mismatch"

    for candidate, error in malformed:
        results.append(failed_result(candidate, error, recognition_drawing))

    for page_number, page_candidates in grouped.items():
        try:
            page = validator.get_page(geometry_drawing, page_number)
            primitives = page.get("primitives")

            if not isinstance(primitives, list):
                raise ValueError(
                    f"Page {page_number} has no primitives list"
                )

        except (ValueError, TypeError, KeyError) as exc:
            for candidate in page_candidates:
                results.append(
                    failed_result(candidate, exc, recognition_drawing)
                )
            continue

        # Build the source map once for the entire page.
        try:
            source_map = validator.build_source_map(
                primitives,
                page_candidates,
                page_number,
            )

            for candidate in page_candidates:
                try:
                    results.append(
                        validate_one(
                            candidate,
                            source_map,
                            recognition_drawing,
                        )
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    results.append(
                        failed_result(candidate, exc, recognition_drawing)
                    )

        except (ValueError, TypeError, KeyError):
            # If one candidate has a bad reference, isolate it by
            # retrying candidates individually.
            for candidate in page_candidates:
                try:
                    source_map = validator.build_source_map(
                        primitives,
                        [candidate],
                        page_number,
                    )
                    results.append(
                        validate_one(
                            candidate,
                            source_map,
                            recognition_drawing,
                        )
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    results.append(
                        failed_result(candidate, exc, recognition_drawing)
                    )

    for result in results:
        result["drawing_id"] = drawing_id
        result["drawing_filename"] = filename

    return drawing_id, filename, results, "processed"


def main():
    parser = argparse.ArgumentParser(
        description="Stream and validate wall candidates across a project"
    )
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()

    project_id = args.project_id
    project_folder = validator.PROJECTS_DIR / project_id
    artifacts = project_folder / "artifacts"

    if not project_folder.is_dir():
        raise FileNotFoundError(f"Project folder not found: {project_folder}")

    recognition_path = artifacts / validator.RECOGNITION_FILE
    geometry_path = artifacts / validator.GEOMETRY_FILE

    recognition = validator.load_recognition(recognition_path)

    recognition_by_id = {
        item["drawing_id"]: item
        for item in recognition["drawings"]
        if isinstance(item, dict) and item.get("drawing_id")
    }

    expected_ids = set(recognition_by_id)
    seen_ids = set()
    all_results = []
    drawing_statuses = []
    geometry_drawing_count = 0

    print("Starting project-wide wall geometry validation...")
    print("Recognition drawings:", len(expected_ids))
    print("Geometry artifact:", geometry_path)
    print("Processing one geometry drawing at a time.")

    with geometry_path.open("rb") as stream:
        for geometry_drawing in ijson.items(stream, "drawings.item"):
            if not isinstance(geometry_drawing, dict):
                continue

            drawing_id = geometry_drawing.get("drawing_id")
            if not drawing_id:
                continue

            geometry_drawing_count += 1

            recognition_drawing = recognition_by_id.get(drawing_id)
            if recognition_drawing is None:
                continue

            seen_ids.add(drawing_id)

            drawing_id, filename, results, status = process_drawing(
                geometry_drawing,
                recognition_drawing,
            )

            all_results.extend(results)
            drawing_statuses.append({
                "drawing_id": drawing_id,
                "filename": filename,
                "status": status,
                "processed_candidates": len(results),
            })

            print(
                f"[{len(seen_ids)}/{len(expected_ids)}] "
                f"{filename}: {len(results)} candidates - {status}"
            )

    missing_ids = expected_ids - seen_ids
    missing_drawings = []

    for drawing_id in sorted(missing_ids):
        drawing = recognition_by_id[drawing_id]
        grouped, malformed = wall_candidates(drawing)
        count = sum(len(items) for items in grouped.values()) + len(malformed)

        missing_drawings.append({
            "drawing_id": drawing_id,
            "filename": drawing.get("filename"),
            "wall_candidate_count": count,
            "status": "missing_from_geometry_artifact",
        })

    validated = sum(
        item.get("geometry_status") == "geometry_validated"
        for item in all_results
    )
    invalid = len(all_results) - validated
    hash_mismatches = sum(
        item["status"] == "source_hash_mismatch"
        for item in drawing_statuses
    )
    missing_candidate_count = sum(
        item["wall_candidate_count"]
        for item in missing_drawings
    )

    output = {
        "schema_version": "0.1.0",
        "validator_version": validator.VALIDATOR_VERSION,
        "stage": "5.3.4-project-batch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "source_geometry_artifact": validator.GEOMETRY_FILE,
        "source_recognition_artifact": validator.RECOGNITION_FILE,
        "validation_policy": {
            "geometry_tolerance_pdf_points": validator.GEOMETRY_TOLERANCE_PT,
            "ratio_tolerance": validator.RATIO_TOLERANCE,
            "real_world_scale_applied": False,
            "semantic_wall_confirmation_performed": False,
            "automatic_approval": False,
        },
        "summary": {
            "recognition_drawing_count": len(expected_ids),
            "geometry_drawing_count": geometry_drawing_count,
            "matched_drawing_count": len(seen_ids),
            "missing_geometry_drawing_count": len(missing_drawings),
            "source_hash_mismatch_drawing_count": hash_mismatches,
            "processed_candidate_count": len(all_results),
            "geometry_validated_count": validated,
            "geometry_invalid_count": invalid,
            "missing_geometry_candidate_count": missing_candidate_count,
            "semantic_confirmation_count": 0,
            "approved_wall_count": 0,
        },
        "drawing_statuses": drawing_statuses,
        "missing_drawings": missing_drawings,
        "results": all_results,
        "limitations": [
            "Geometric validation does not prove that a candidate is a physical wall.",
            "Real-world scale remains unresolved; no metric dimensions are inferred.",
            "No wall height or 3D mesh is generated.",
            "All candidates remain unapproved and require engineering verification.",
            "Drawings missing from the geometry artifact are not validated.",
            "A source hash mismatch prevents geometry validation for that drawing.",
        ],
    }

    output_path = (
        artifacts / "wall_candidate_validation_stage5_3_project.json"
    )
    validator.atomic_write_json(output_path, output)

    print("\n=== PROJECT WALL VALIDATION SUMMARY ===")
    print(json.dumps(output["summary"], indent=2))
    print("Approved walls: 0")
    print("Project report:", output_path)


if __name__ == "__main__":
    main()
