
import json
import os
from pathlib import Path

from app.services.association_gap_analysis import analyze_association_gaps

PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

ARTIFACT_DIR = Path("data/projects") / PROJECT_ID / "artifacts"

INPUT_PATH = ARTIFACT_DIR / "evidence_geometry_association_stage5_2_17.json"
OUTPUT_PATH = ARTIFACT_DIR / "association_gap_analysis_stage5_2_18.json"


def main():
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite: {OUTPUT_PATH}")

    with INPUT_PATH.open("r", encoding="utf-8") as file:
        source = json.load(file)

    result = analyze_association_gaps(source)

    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary, OUTPUT_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()

    print("\nSTAGE 5.2.18 COMPLETED")
    print("=" * 50)
    print("Project:", PROJECT_ID)
    print("Output:", OUTPUT_PATH)
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
