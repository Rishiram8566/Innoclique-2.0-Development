"""Stage 5.2.9 project-level runner for Stage 5.2.8 dimension classification.

Reads the existing Stage 5.0 metadata index only. It does not read the large
geometry artifact, reopen PDFs, calibrate drawings, or modify prior reports.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.dimension_evidence_classifier_v2 import (
    CLASSIFICATION_STAGE,
    classify_drawing_evidence,
)

INDEX_FILENAME = "drawing_geometry_stage5_metadata_index.json"
OUTPUT_FILENAME = "dimension_evidence_stage5_2_8.json"
RUNNER_STAGE = "5.2.9"


class DimensionEvidenceRunnerError(RuntimeError):
    """Raised when project metadata cannot safely be processed."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def build_project_dimension_evidence_report(
    project_folder: str | Path,
    project_id: str,
) -> dict[str, Any]:
    """Build an in-memory report from the Stage 5.0 metadata index."""
    project_path = Path(project_folder).resolve()
    artifacts_dir = project_path / "artifacts"
    index_path = artifacts_dir / INDEX_FILENAME

    if not project_path.exists() or not project_path.is_dir():
        raise DimensionEvidenceRunnerError(
            f"Project folder does not exist: {project_path}"
        )
    if not index_path.exists() or not index_path.is_file():
        raise DimensionEvidenceRunnerError(
            f"Required metadata index is missing: {index_path}"
        )

    try:
        with index_path.open("r", encoding="utf-8") as stream:
            index = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise DimensionEvidenceRunnerError(
            f"Could not read metadata index: {exc}"
        ) from exc

    if not isinstance(index, dict) or not isinstance(index.get("drawings"), list):
        raise DimensionEvidenceRunnerError(
            "Metadata index must be an object containing a drawings list."
        )

    source_hash = _sha256_file(index_path)
    drawing_reports: list[dict[str, Any]] = []
    skipped_drawings: list[dict[str, Any]] = []
    semantic_counts: Counter[str] = Counter()
    unit_counts: Counter[str] = Counter()
    total_pages = 0
    restricted_pages = 0
    total_evidence = 0

    for position, drawing in enumerate(index["drawings"]):
        if not isinstance(drawing, dict):
            skipped_drawings.append({
                "position": position,
                "reason": "drawing_record_is_not_an_object",
            })
            continue

        drawing_id = drawing.get("drawing_id")
        filename = drawing.get("filename")

        try:
            report = classify_drawing_evidence(drawing)
            report["stage"] = CLASSIFICATION_STAGE
            drawing_reports.append(report)

            summary = report.get("summary") or {}
            total_pages += int(summary.get("page_count", 0))
            restricted_pages += int(
                summary.get("pages_with_scale_restriction", 0)
            )

            for page in report.get("pages", []):
                for evidence in page.get("dimension_evidence", []):
                    total_evidence += 1
                    semantic_counts[
                        str(evidence.get("semantic_type") or "unknown")
                    ] += 1
                    unit_counts[
                        str(evidence.get("normalized_unit") or "unknown")
                    ] += 1

        except Exception as exc:
            skipped_drawings.append({
                "position": position,
                "drawing_id": drawing_id,
                "filename": filename,
                "reason": f"classification_error: {type(exc).__name__}: {exc}",
            })

    return {
        "schema_version": "0.1.0",
        "stage": RUNNER_STAGE,
        "classification_stage": CLASSIFICATION_STAGE,
        "status": "completed" if not skipped_drawings else "completed_with_skips",
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "artifact": INDEX_FILENAME,
            "sha256": source_hash,
            "drawing_count_in_index": len(index["drawings"]),
            "read_only": True,
        },
        "summary": {
            "indexed_drawing_count": len(index["drawings"]),
            "processed_drawing_count": len(drawing_reports),
            "skipped_drawing_count": len(skipped_drawings),
            "processed_page_count": total_pages,
            "dimension_evidence_count": total_evidence,
            "pages_with_scale_restriction": restricted_pages,
            "calibration_eligible_count": 0,
            "verification_required": True,
            "semantic_type_counts": dict(sorted(semantic_counts.items())),
            "normalized_unit_counts": dict(sorted(unit_counts.items())),
        },
        "drawings": drawing_reports,
        "skipped_drawings": skipped_drawings,
        "limitations": [
            "This stage reads only the existing Stage 5.0 metadata index.",
            "No source PDFs or large geometry artifact are reprocessed.",
            "Semantic labels are contextual candidates, not verified engineering facts.",
            "Repeated annotations are not independent confirmations.",
            "No calibration, geometry generation, BIM approval, or source mutation occurs.",
            "Pages without a detected no-scale note are not thereby proven to be to scale.",
        ],
    }


def run_project_dimension_evidence(
    project_folder: str | Path,
    project_id: str,
) -> dict[str, Any]:
    """Build and atomically write the separate Stage 5.2.8 report."""
    project_path = Path(project_folder).resolve()
    report = build_project_dimension_evidence_report(project_path, project_id)
    output_path = project_path / "artifacts" / OUTPUT_FILENAME
    _write_json_atomic(output_path, report)

    return {
        "status": report["status"],
        "stage": RUNNER_STAGE,
        "classification_stage": CLASSIFICATION_STAGE,
        "project_id": project_id,
        "output_artifact": str(output_path),
        "summary": report["summary"],
        "source_index_sha256": report["source"]["sha256"],
    }
