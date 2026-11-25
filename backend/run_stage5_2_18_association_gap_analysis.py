"""
Stage 5.2.18 - association gap analysis.

Usage (from the backend folder):
    python run_stage5_2_18_association_gap_analysis.py --project-id <uuid>
"""

import argparse
import json
import os

from stage_script_args import add_project_argument, project_folder
from app.services.association_gap_analysis import analyze_association_gaps

INPUT_FILENAME = "evidence_geometry_association_stage5_2_17.json"
OUTPUT_FILENAME = "association_gap_analysis_stage5_2_18.json"


def main():
    parser = argparse.ArgumentParser(description="Stage 5.2.18 association gap analysis")
    add_project_argument(parser)
    project_id = parser.parse_args().project_id

    artifact_dir = project_folder(project_id) / "artifacts"
    input_path = artifact_dir / INPUT_FILENAME
    output_path = artifact_dir / OUTPUT_FILENAME

    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite: {output_path}")

    with input_path.open("r", encoding="utf-8") as file:
        source = json.load(file)

    result = analyze_association_gaps(source)

    temporary = output_path.with_suffix(".json.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    print("\nSTAGE 5.2.18 COMPLETED")
    print("=" * 50)
    print("Project:", project_id)
    print("Output:", output_path)
    print("\nSUMMARY")

    for key, value in result["summary"].items():
        print(f"{key}: {value}")

    print("\nSafety:")
    print("Approved:", result["summary"]["approved_count"])
    print(
        "Geometry generation allowed:",
        result["summary"]["geometry_generation_allowed_count"],
    )


if __name__ == "__main__":
    main()
