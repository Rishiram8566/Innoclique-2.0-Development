
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.services.evidence_deduplication import (
    STAGE,
    deduplicate_evidence,
)

DEFAULT_PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

INPUT_FILENAME = "vertical_level_evidence_stage5_2_11.json"
OUTPUT_FILENAME = "evidence_relationships_stage5_2_12.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def run(
    project_id: str,
    backend_dir: Path,
    overwrite: bool = False,
) -> dict:

    project_dir = backend_dir / "data" / "projects" / project_id
    artifacts_dir = project_dir / "artifacts"

    input_path = artifacts_dir / INPUT_FILENAME
    output_path = artifacts_dir / OUTPUT_FILENAME

    if not project_dir.is_dir():
        raise FileNotFoundError(
            f"Project directory does not exist: {project_dir}"
        )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"Stage 5.2.11 input report not found: {input_path}"
        )

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}. "
            "Use --overwrite only after reviewing the existing report."
        )

    input_hash_before = sha256_file(input_path)

    with input_path.open("r", encoding="utf-8") as file:
        source_report = json.load(file)

    if source_report.get("stage") != "5.2.11":
        raise ValueError(
            f"Expected Stage 5.2.11, found {source_report.get('stage')!r}"
        )

    if source_report.get("project_id") != project_id:
        raise ValueError("Project ID mismatch in source report.")

    result = deduplicate_evidence(source_report)

    if result.get("stage") != STAGE:
        raise ValueError("Unexpected output stage.")

    if len(result["candidates"]) != len(source_report["candidates"]):
        raise ValueError("Candidate count changed. Refusing to write.")

    result["source_report"]["filename"] = INPUT_FILENAME
    result["source_report"]["sha256"] = input_hash_before

    if sha256_file(input_path) != input_hash_before:
        raise RuntimeError("Source report changed during processing.")

    atomic_write_json(output_path, result)

    return {
        "project_id": project_id,
        "stage": STAGE,
        "input_candidate_count": len(source_report["candidates"]),
        "output_candidate_count": len(result["candidates"]),
        "summary": result["summary"],
        "input_sha256": input_hash_before,
        "output_path": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stage 5.2.12 evidence deduplication."
    )

    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
    )

    parser.add_argument(
        "--backend-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    args = parser.parse_args()

    report = run(
        project_id=args.project_id,
        backend_dir=args.backend_dir.resolve(),
        overwrite=args.overwrite,
    )

    print("\nSTAGE 5.2.12 COMPLETED")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
