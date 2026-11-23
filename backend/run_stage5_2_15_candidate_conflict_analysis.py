
"""Run Stage 5.2.15 candidate conflict analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.services.candidate_conflict_analysis import (
    analyze_candidate_conflicts,
)


DEFAULT_PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

INPUT_FILENAME = "engineering_element_candidates_stage5_1_6.json"
OUTPUT_FILENAME = "candidate_conflict_analysis_stage5_2_15_v2.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raise FileExistsError(
            f"Output already exists. Refusing to overwrite: {path}"
        )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}_",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        if path.exists():
            raise FileExistsError(
                f"Output appeared during execution: {path}"
            )

        os.rename(temporary_name, path)

    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Stage 5.2.15 candidate conflict analysis."
    )

    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help="Project ID to analyze.",
    )

    args = parser.parse_args()

    backend_dir = Path(__file__).resolve().parent

    project_dir = (
        backend_dir / "data" / "projects" / args.project_id
    )

    input_path = (
        project_dir / "artifacts" / INPUT_FILENAME
    )

    output_path = (
        project_dir / "artifacts" / OUTPUT_FILENAME
    )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"Recognition artifact not found: {input_path}"
        )

    print("Stage 5.2.15 - Candidate Conflict Analysis")
    print("=" * 60)
    print(f"Project ID: {args.project_id}")
    print(f"Input:      {input_path}")

    input_hash = sha256_file(input_path)

    with input_path.open("r", encoding="utf-8") as file:
        recognition_data = json.load(file)

    if recognition_data.get("project_id") != args.project_id:
        raise ValueError(
            "Project ID mismatch between requested project "
            "and recognition artifact."
        )

    result = analyze_candidate_conflicts(recognition_data)

    result["input_artifact"] = INPUT_FILENAME
    result["input_sha256"] = input_hash
    result["generated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    atomic_write_json(output_path, result)

    summary = result["summary"]

    print("\nAnalysis completed.")
    print("-" * 60)
    print(f"Wall candidates:                  {summary['wall_candidate_count']}")
    print(f"Invalid bounding boxes:           {summary['invalid_bbox_count']}")
    print(f"Conflict pairs:                   {summary['conflict_pair_count']}")
    print(f"Shared-source pairs:              {summary['shared_source_pair_count']}")
    print(f"Bbox-overlap pairs:               {summary['bbox_overlap_pair_count']}")
    print(
        "Unverified orthogonal overlaps:   "
        f"{summary['orthogonal_bbox_overlap_unverified_count']}"
    )
    print(
        "Possible confirmed junctions:    "
        f"{summary['possible_geometric_junction_count']}"
    )
    print(
        "Substantial bbox overlaps:        "
        f"{summary['substantial_bbox_overlap_count']}"
    )
    print(f"Approved conflicts:               {summary['approved_conflict_count']}")
    print(
        "Geometry generation allowed:     "
        f"{summary['geometry_generation_allowed_count']}"
    )

    print(f"\nInput SHA256: {input_hash}")
    print(f"Output: {output_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
