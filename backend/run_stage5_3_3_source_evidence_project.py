
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import ijson

from app.services.geometry_source_evidence import (
    build_geometry_source_evidence_report,
)


BASE_DIR = Path(__file__).resolve().parent

PROJECT_ID = "a5482b08-1c59-4bf0-9eb2-7ea41eb26f9c"

ARTIFACTS_DIR = (
    BASE_DIR / "data" / "projects" / PROJECT_ID / "artifacts"
)

RECOGNITION_FILE = (
    ARTIFACTS_DIR
    / "engineering_element_candidates_stage5_1_6.json"
)

GEOMETRY_FILE = (
    ARTIFACTS_DIR / "drawing_geometry_stage5.json"
)

OUTPUT_FILE = (
    ARTIFACTS_DIR
    / "geometry_source_evidence_stage5_3_3_v2.json"
)

MAX_DISTANCE = 24.0


def json_safe_default(value):
    """Convert Decimal values into JSON-compatible numbers."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    raise TypeError(
        f"Object of type {type(value).__name__} "
        "is not JSON serializable"
    )


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Input file missing: {path}")

    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")

    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing artifact: {path}"
        )

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
                default=json_safe_default,
                allow_nan=False,
            )

            file.write("\n")

        if path.exists():
            raise FileExistsError(
                f"Output appeared during processing: {path}"
            )

        os.replace(temp_path, path)
        temp_path = None

    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def main() -> None:
    print("=" * 65)
    print("STAGE 5.3.3 - PROJECT-WIDE SOURCE EVIDENCE")
    print("=" * 65)

    if OUTPUT_FILE.exists():
        raise FileExistsError(
            f"Output already exists. Nothing overwritten: {OUTPUT_FILE}"
        )

    print("\nCHECKING INPUT FILES...")

    for path in [RECOGNITION_FILE, GEOMETRY_FILE]:
        if not path.is_file():
            raise FileNotFoundError(path)

        print(f"FOUND: {path.name}")

    print("\nLOADING RECOGNITION DATA...")

    recognition_data = load_json(RECOGNITION_FILE)

    if recognition_data.get("project_id") != PROJECT_ID:
        raise ValueError("Recognition project ID mismatch.")

    recognition_drawings = recognition_data.get("drawings")

    if not isinstance(recognition_drawings, list):
        raise ValueError("Recognition drawings must be a list.")

    recognition_map = {}

    for drawing in recognition_drawings:
        drawing_id = drawing.get("drawing_id")

        if not drawing_id:
            raise ValueError("Recognition drawing has no drawing ID.")

        if drawing_id in recognition_map:
            raise ValueError(
                f"Duplicate recognition drawing: {drawing_id}"
            )

        recognition_map[drawing_id] = drawing

    print(f"Recognition drawings: {len(recognition_map)}")

    all_evidence = []
    all_warnings = []
    all_limitations = set()
    category_counts = Counter()

    processed_geometry_ids = set()

    totals = {
        "recognition_drawings": len(recognition_map),
        "geometry_drawings": 0,
        "processed_pages": 0,
        "skipped_pages": 0,
        "evidence_count": 0,
        "height_evidence_candidate_count": 0,
        "spatial_suggestion_count": 0,
        "source_hash_mismatches": 0,
        "approved_count": 0,
        "geometry_generation_allowed_count": 0,
    }

    print("\nSTREAMING GEOMETRY DRAWINGS...")
    print("Original geometry file will remain unchanged.")

    with GEOMETRY_FILE.open("rb") as geometry_file:

        for geometry_drawing in ijson.items(
            geometry_file,
            "drawings.item",
            use_float=True,
        ):
            drawing_id = geometry_drawing.get("drawing_id")

            if not drawing_id:
                raise ValueError(
                    "Geometry drawing has no drawing ID."
                )

            if drawing_id in processed_geometry_ids:
                raise ValueError(
                    f"Duplicate geometry drawing: {drawing_id}"
                )

            processed_geometry_ids.add(drawing_id)

            recognition_drawing = recognition_map.get(drawing_id)

            if recognition_drawing is None:
                raise ValueError(
                    f"Recognition drawing missing: {drawing_id}"
                )

            totals["geometry_drawings"] += 1

            print(
                f"\nProcessing "
                f"{totals['geometry_drawings']}/"
                f"{len(recognition_map)}: {drawing_id}"
            )

            # Process one drawing at a time.
            single_recognition = {
                "project_id": PROJECT_ID,
                "drawings": [recognition_drawing],
            }

            single_geometry = {
                "project_id": PROJECT_ID,
                "drawings": [geometry_drawing],
            }

            report = build_geometry_source_evidence_report(
                single_recognition,
                single_geometry,
                max_spatial_distance_pdf_points=MAX_DISTANCE,
            )

            summary = report["summary"]

            if summary["source_hash_mismatches"] != 0:
                raise RuntimeError(
                    f"Source hash mismatch for drawing {drawing_id}"
                )

            all_evidence.extend(report["evidence"])
            all_warnings.extend(report["warnings"])
            all_limitations.update(report["limitations"])

            category_counts.update(
                summary["category_counts"]
            )

            for key in totals:
                if key in (
                    "recognition_drawings",
                    "geometry_drawings",
                ):
                    continue

                totals[key] += summary.get(key, 0)

            print(
                f"  Pages processed: "
                f"{summary['processed_pages']}"
            )

            print(
                f"  Evidence records: "
                f"{summary['evidence_count']}"
            )

    missing_geometry = (
        set(recognition_map) - processed_geometry_ids
    )

    if missing_geometry:
        raise RuntimeError(
            f"Missing geometry drawings: {sorted(missing_geometry)}"
        )

    if totals["geometry_drawings"] != len(recognition_map):
        raise RuntimeError(
            "Geometry and recognition drawing counts differ."
        )

    if totals["source_hash_mismatches"] != 0:
        raise RuntimeError(
            "Source hash audit failed."
        )

    final_report = {
        "stage": "5.3.3",
        "schema_version": "0.2.1",
        "project_id": PROJECT_ID,
        "status": "completed_with_verification_required",
        "summary": {
            **totals,
            "category_counts": dict(category_counts),
        },
        "evidence": all_evidence,
        "warnings": all_warnings,
        "limitations": sorted(all_limitations),
    }

    print("\nWRITING NEW EVIDENCE ARTIFACT...")

    write_json_atomic(OUTPUT_FILE, final_report)

    print("\n" + "=" * 65)
    print("STAGE 5.3.3 PROJECT-WIDE ANALYSIS COMPLETE")
    print("=" * 65)

    for key, value in final_report["summary"].items():
        print(f"{key}: {value}")

    print(f"\nWarnings: {len(all_warnings)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
