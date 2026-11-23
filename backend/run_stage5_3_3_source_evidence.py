
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from app.services.geometry_source_evidence import (
    build_geometry_source_evidence_report,
)


BASE_DIR = Path(__file__).resolve().parent

PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

RECOGNITION_FILE = (
    BASE_DIR
    / "data"
    / "projects"
    / PROJECT_ID
    / "artifacts"
    / "engineering_element_candidates_stage5_1_6.json"
)

GEOMETRY_FILE = (
    BASE_DIR
    / "data"
    / "validation_stage5_3"
    / "drawing_geometry_stage5_repaired_test.json"
)

# Updated output filename to preserve the original report.
OUTPUT_FILE = (
    BASE_DIR
    / "data"
    / "validation_stage5_3"
    / "geometry_source_evidence_stage5_3_3_v2.json"
)


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")

    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raise FileExistsError(
            f"Output already exists; refusing to overwrite: {path}"
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
            json.dump(data, file, indent=2, ensure_ascii=False)
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
    parser = argparse.ArgumentParser(
        description="Stage 5.3.3 geometry source evidence analysis"
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=24.0,
        help="Maximum spatial suggestion distance in PDF points",
    )
    args = parser.parse_args()

    print("STAGE 5.3.3 - GEOMETRY SOURCE EVIDENCE")
    print(f"Project: {PROJECT_ID}")
    print("Loading recognition artifact...")

    recognition_data = load_json(RECOGNITION_FILE)

    print("Loading repaired geometry artifact...")
    geometry_data = load_json(GEOMETRY_FILE)

    if recognition_data.get("project_id") != PROJECT_ID:
        raise ValueError("Recognition project ID does not match runner")

    print("Analyzing source evidence...")

    report = build_geometry_source_evidence_report(
        recognition_data,
        geometry_data,
        max_spatial_distance_pdf_points=args.max_distance,
    )

    write_json_atomic(OUTPUT_FILE, report)

    summary = report["summary"]

    print("\nANALYSIS COMPLETE")
    print(f"Processed pages: {summary['processed_pages']}")
    print(f"Skipped pages: {summary['skipped_pages']}")
    print(f"Evidence records: {summary['evidence_count']}")
    print(f"Categories: {summary['category_counts']}")
    print(
        "Height evidence candidates: "
        f"{summary['height_evidence_candidate_count']}"
    )
    print(
        "Spatial suggestions: "
        f"{summary['spatial_suggestion_count']}"
    )
    print(
        "Source hash mismatches: "
        f"{summary['source_hash_mismatches']}"
    )
    print(f"Approved: {summary['approved_count']}")
    print(
        "Geometry generation allowed: "
        f"{summary['geometry_generation_allowed_count']}"
    )
    print(f"\nOutput: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
