
import json
import os
from pathlib import Path

from app.services.evidence_geometry_association import (
    analyze_evidence_geometry_associations,
)


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

PROJECT_DIR = Path("data/projects") / PROJECT_ID
ARTIFACT_DIR = PROJECT_DIR / "artifacts"

RECOGNITION_PATH = (
    ARTIFACT_DIR / "engineering_element_candidates_stage5_1_6.json"
)

EVIDENCE_PATH = (
    ARTIFACT_DIR / "evidence_relationships_stage5_2_12.json"
)

OUTPUT_PATH = (
    ARTIFACT_DIR / "evidence_geometry_association_stage5_2_17.json"
)


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
    print("\nSTAGE 5.2.17 - EVIDENCE-TO-GEOMETRY ASSOCIATION")
    print("=" * 60)

    recognition = read_json(RECOGNITION_PATH)
    evidence = read_json(EVIDENCE_PATH)

    print("Recognition artifact loaded.")
    print("Evidence artifact loaded.")

    result = analyze_evidence_geometry_associations(
        recognition,
        evidence,
    )

    write_json_atomic(OUTPUT_PATH, result)

    summary = result["summary"]

    print("\nSTAGE 5.2.17 COMPLETED")
    print("-" * 60)

    print("Project ID:", PROJECT_ID)
    print("Output:", OUTPUT_PATH)

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
