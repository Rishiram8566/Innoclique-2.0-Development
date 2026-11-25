"""
Stage 5.2.17 - evidence-to-geometry association.

Usage (from the backend folder):
    python run_stage5_2_17_evidence_geometry_association.py --project-id <uuid>
"""

import argparse
import json
import os
from pathlib import Path

from stage_script_args import add_project_argument, project_folder
from app.services.evidence_geometry_association import (
    analyze_evidence_geometry_associations,
)


RECOGNITION_FILENAME = "engineering_element_candidates_stage5_1_6.json"
EVIDENCE_FILENAME = "evidence_relationships_stage5_2_12.json"
OUTPUT_FILENAME = "evidence_geometry_association_stage5_2_17.json"


def read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Input artifact not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json_atomic(path: Path, data: dict) -> None:
    if path.exists():
        raise FileExistsError(
            f"Output already exists. Refusing to overwrite: {path}"
        )

    temporary_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temporary_path.open("x", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")

        os.replace(temporary_path, path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main():
    parser = argparse.ArgumentParser(description="Stage 5.2.17 evidence-to-geometry association")
    add_project_argument(parser)
    project_id = parser.parse_args().project_id

    artifact_dir = project_folder(project_id) / "artifacts"
    recognition_path = artifact_dir / RECOGNITION_FILENAME
    evidence_path = artifact_dir / EVIDENCE_FILENAME
    output_path = artifact_dir / OUTPUT_FILENAME

    print("\nSTAGE 5.2.17 - EVIDENCE-TO-GEOMETRY ASSOCIATION")
    print("=" * 60)

    recognition = read_json(recognition_path)
    evidence = read_json(evidence_path)

    for name, report in (("Recognition", recognition), ("Evidence", evidence)):
        if report.get("project_id") not in (None, project_id):
            raise ValueError(f"{name} artifact belongs to another project")

    print("Recognition artifact loaded.")
    print("Evidence artifact loaded.")

    result = analyze_evidence_geometry_associations(
        recognition,
        evidence,
    )

    write_json_atomic(output_path, result)

    summary = result["summary"]

    print("\nSTAGE 5.2.17 COMPLETED")
    print("-" * 60)

    print("Project ID:", project_id)
    print("Output:", output_path)

    print("\nSUMMARY")

    for key, value in summary.items():
        print(f"{key}: {value}")

    print("\nAssociation status counts:")

    status_counts = {}

    for item in result["evidence_associations"]:
        status = item["association_status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    for status, count in sorted(status_counts.items()):
        print(f"{status}: {count}")

    print("\nSafety checks:")
    print("Approved associations:", summary["approved_association_count"])
    print(
        "Geometry generation allowed:",
        summary["geometry_generation_allowed_count"],
    )

    print("\nOriginal input artifacts were not modified.")


if __name__ == "__main__":
    main()
