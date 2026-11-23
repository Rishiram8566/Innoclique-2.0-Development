
"""Run Stage 5.2.16 cross-drawing consistency analysis."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.services.cross_drawing_consistency import (
    analyze_cross_drawing_consistency,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()

    project_id = args.project_id

    project_dir = (
        Path("data")
        / "projects"
        / project_id
    )

    artifact_dir = project_dir / "artifacts"

    input_path = (
        artifact_dir
        / "cross_drawing_geometry_stage5_2_13.json"
    )

    output_path = (
        artifact_dir
        / "cross_drawing_consistency_stage5_2_16.json"
    )

    # Validate the input artifact.
    if not input_path.is_file():
        raise FileNotFoundError(
            f"Stage 5.2.13 input artifact not found: {input_path}"
        )

    # Never overwrite an existing output artifact.
    if output_path.exists():
        raise FileExistsError(
            "Stage 5.2.16 output already exists. "
            "Preserving it; inspect it before deciding whether to rerun."
        )

    with input_path.open("r", encoding="utf-8") as file:
        input_data = json.load(file)

    if input_data.get("project_id") != project_id:
        raise ValueError(
            "Project ID mismatch between runner argument and input artifact."
        )

    # Run Stage 5.2.16 analysis.
    result = analyze_cross_drawing_consistency(input_data)

    artifact_dir.mkdir(parents=True, exist_ok=True)

    temporary_path = output_path.with_suffix(
        output_path.suffix + ".tmp"
    )

    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(
                result,
                file,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            file.write("\n")

        os.replace(temporary_path, output_path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    summary = result["summary"]

    print("\nSTAGE 5.2.16 COMPLETED")
    print(f"Project ID: {project_id}")
    print(f"Input:      {input_path}")
    print(f"Output:     {output_path}")

    print("\nSUMMARY")

    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
