
"""
Innoclique — Stage 5.2.10
Project-level Vertical Level Evidence Runner

Reads the existing Stage 5.0 metadata index and generates a separate
vertical-level evidence report.

Does not reprocess PDFs, modify existing reports, approve evidence,
or generate BIM geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.services.vertical_level_evidence import (
    extract_vertical_level_evidence,
)


STAGE = "5.2.10"
DEFAULT_METADATA_FILENAME = "drawing_geometry_stage5_metadata_index.json"
DEFAULT_OUTPUT_FILENAME = "vertical_level_evidence_stage5_2_10.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_name(path.name + ".tmp")

    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def run_project(
    project_id: str,
    backend_root: Path,
    metadata_path: Path | None = None,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> dict:
    if not project_id.strip():
        raise ValueError("project_id cannot be empty")

    project_root = backend_root / "data" / "projects" / project_id
    artifacts_dir = project_root / "artifacts"

    if metadata_path is None:
        metadata_path = artifacts_dir / DEFAULT_METADATA_FILENAME

    if output_path is None:
        output_path = artifacts_dir / DEFAULT_OUTPUT_FILENAME

    metadata_path = metadata_path.resolve()
    output_path = output_path.resolve()

    if not metadata_path.is_file():
        raise FileNotFoundError(
            f"Metadata index not found: {metadata_path}"
        )

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Use --overwrite only if you intentionally want to replace it."
        )

    print(f"[{STAGE}] Reading metadata index...")
    print(f"Project ID: {project_id}")
    print(f"Metadata: {metadata_path}")

    source_hash = sha256_file(metadata_path)

    with metadata_path.open("r", encoding="utf-8") as file:
        metadata_index = json.load(file)

    if not isinstance(metadata_index, dict):
        raise ValueError("Metadata index root must be a JSON object")

    print(f"[{STAGE}] Extracting vertical-level evidence...")

    report = extract_vertical_level_evidence(
        metadata_index=metadata_index,
        project_id=project_id,
    )

    report["source"]["metadata_index_path"] = str(metadata_path)
    report["source"]["metadata_index_sha256"] = source_hash
    report["source"]["metadata_index_filename"] = metadata_path.name

    report["runner"] = {
        "stage": STAGE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_hash_algorithm": "sha256",
        "pdf_reprocessing_performed": False,
        "existing_reports_modified": False,
        "evidence_approved": False,
        "geometry_generated": False,
    }

    write_json_atomic(output_path, report)

    summary = report["summary"]

    print("\n========== STAGE 5.2.10 REPORT ==========")
    print(f"Status: {report['status']}")
    print(f"Project ID: {project_id}")
    print(f"Drawings indexed: {report['source']['metadata_index_drawing_count']}")
    print(f"Pages inspected: {report['source']['metadata_index_page_count']}")
    print(f"Level candidates: {summary['candidate_count']}")
    print(
        "Unpaired numeric values skipped: "
        f"{summary['skipped_unpaired_numeric_count']}"
    )
    print(f"Approved candidates: {summary['approved_count']}")
    print(
        "Calibration eligible: "
        f"{summary['calibration_eligible_count']}"
    )
    print(
        "Geometry generation allowed: "
        f"{summary['geometry_generation_allowed_count']}"
    )

    print("\nSemantic type counts:")
    for semantic_type, count in summary["semantic_type_counts"].items():
        print(f"  {semantic_type}: {count}")

    print("\nSource metadata SHA256:")
    print(source_hash)

    print("\nOutput report:")
    print(output_path)

    print("\nStage 5.2.10 completed successfully.")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Stage 5.2.10 vertical-level evidence extraction."
    )

    parser.add_argument(
        "--project-id",
        required=True,
        help="Innoclique project ID",
    )

    parser.add_argument(
        "--metadata-index",
        type=Path,
        default=None,
        help="Optional custom metadata index path",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional custom output JSON path",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output report",
    )

    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parent

    try:
        run_project(
            project_id=args.project_id,
            backend_root=backend_root,
            metadata_path=args.metadata_index,
            output_path=args.output,
            overwrite=args.overwrite,
        )
        return 0

    except Exception as exc:
        print(f"\n[ERROR] Stage {STAGE} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
