
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.services.cross_drawing_geometry import (
    analyze_cross_drawing_geometry,
)


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT = (
    BASE_DIR
    / "data"
    / "projects"
    / PROJECT_ID
    / "artifacts"
    / "engineering_element_candidates_stage5_1_6.json"
)

DEFAULT_OUTPUT = (
    BASE_DIR
    / "data"
    / "projects"
    / PROJECT_ID
    / "artifacts"
    / "cross_drawing_geometry_stage5_2_13.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=path.stem + "_",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_name, path)

    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 5.2.13 cross-drawing geometry analysis"
    )

    parser.add_argument(
        "--project-id",
        default=PROJECT_ID,
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    project_id = args.project_id

    input_path = args.input.resolve()

    output_path = (
        args.output.resolve()
        if args.output
        else (
            BASE_DIR
            / "data"
            / "projects"
            / project_id
            / "artifacts"
            / "cross_drawing_geometry_stage5_2_13.json"
        )
    )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"Recognition input not found: {input_path}"
        )

    if output_path.exists():
        raise FileExistsError(
            "Output already exists. Refusing to overwrite: "
            f"{output_path}"
        )

    source_hash = sha256_file(input_path)

    recognition_data = json.loads(
        input_path.read_text(encoding="utf-8")
    )

    if recognition_data.get("project_id") != project_id:
        raise ValueError(
            "Input project ID does not match requested project ID."
        )

    report = analyze_cross_drawing_geometry(recognition_data)

    report["input_artifact"] = {
        "filename": input_path.name,
        "sha256": source_hash,
        "stage": recognition_data.get("stage"),
    }

    report["source_artifact_unchanged"] = True

    atomic_write_json(output_path, report)

    print("\nSTAGE 5.2.13 COMPLETED")
    print("=" * 55)
    print("Project ID:", project_id)
    print("Input drawings:", report["summary"]["drawing_count"])
    print("Wall candidates:", report["summary"]["wall_candidate_count"])
    print(
        "Exact cross-drawing relationships:",
        report["summary"]["exact_cross_drawing_relationship_count"],
    )
    print(
        "Relationship members:",
        report["summary"]["relationship_member_count"],
    )
    print(
        "Approved relationships:",
        report["summary"]["approved_relationship_count"],
    )
    print(
        "Geometry generation allowed:",
        report["summary"]["geometry_generation_allowed_count"],
    )
    print("Input SHA256:", source_hash)
    print("Output:", output_path)


if __name__ == "__main__":
    main()
