
"""Stage 5.2.5 project runner for Stage 5.2.4 dimension evidence.

Reads only the Stage 5.0 metadata index, classifies all indexed drawing
pages, and writes a separate JSON artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.dimension_evidence_classifier import (
    classify_drawing_evidence,
)


METADATA_INDEX_NAME = "drawing_geometry_stage5_metadata_index.json"
OUTPUT_NAME = "dimension_evidence_stage5_2_4.json"


def _sha256_file(path: Path) -> str:
    """Calculate SHA256 without loading the complete file into memory."""

    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _write_json_atomic(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """Write JSON safely using a temporary file and atomic replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:

            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
            )

            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_name, path)

    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass

        raise


def build_project_dimension_evidence(
    project_folder: str | Path,
    project_id: str,
) -> dict[str, Any]:
    """
    Classify dimension evidence for every drawing in the metadata index.

    Important:
    - Does not reopen PDFs.
    - Does not regenerate Stage 5.0 geometry.
    - Does not modify calibration artifacts.
    - Does not approve dimensions for BIM generation.
    """

    project_folder = Path(project_folder).resolve()

    artifacts_dir = project_folder / "artifacts"

    index_path = artifacts_dir / METADATA_INDEX_NAME

    if not index_path.is_file():
        raise FileNotFoundError(
            f"Stage 5.0 metadata index not found: {index_path}"
        )

    # Read only the metadata index, not the large geometry artifact.
    with index_path.open(
        "r",
        encoding="utf-8",
    ) as stream:
        index = json.load(stream)

    drawings = index.get("drawings")

    if not isinstance(drawings, list):
        raise ValueError(
            "Metadata index is invalid: 'drawings' must be a list."
        )

    drawing_reports = []
    total_pages = 0
    total_dimensions = 0
    pages_with_scale_restriction = 0

    skipped_drawings = []

    for drawing in drawings:

        if not isinstance(drawing, dict):
            skipped_drawings.append({
                "drawing_id": "unknown",
                "reason": "Drawing entry is not an object.",
            })
            continue

        pages = drawing.get("pages")

        if not isinstance(pages, list):
            skipped_drawings.append({
                "drawing_id": str(
                    drawing.get("drawing_id") or "unknown"
                ),
                "reason": "Pages field is missing or invalid.",
            })
            continue

        # Uses the existing Stage 5.2.4 classifier.
        report = classify_drawing_evidence(drawing)

        drawing_reports.append(report)

        summary = report.get("summary", {})

        total_pages += summary.get("page_count", 0)

        total_dimensions += summary.get(
            "dimension_evidence_count",
            0,
        )

        pages_with_scale_restriction += summary.get(
            "pages_with_scale_restriction",
            0,
        )

    index_hash = _sha256_file(index_path)

    result = {
        "schema_version": "0.1.0",
        "stage": "5.2.5",
        "classification_stage": "5.2.4",
        "status": "completed",
        "project_id": project_id,

        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": {
            "artifact": METADATA_INDEX_NAME,
            "sha256": index_hash,
            "size_bytes": index_path.stat().st_size,
            "source_stage": index.get("source_artifact"),
            "source_index_generated_at": index.get("generated_at"),
        },

        "summary": {
            "indexed_drawing_count": len(drawings),
            "processed_drawing_count": len(drawing_reports),
            "skipped_drawing_count": len(skipped_drawings),
            "processed_page_count": total_pages,
            "dimension_evidence_count": total_dimensions,
            "pages_with_scale_restriction": (
                pages_with_scale_restriction
            ),
            "calibration_eligible_count": 0,
            "verification_required": True,
        },

        "drawings": drawing_reports,

        "skipped_drawings": skipped_drawings,

        "limitations": [
            "Reads the Stage 5.0 metadata index only.",
            "Does not read or regenerate the full geometry artifact.",
            "Does not reopen or re-extract original PDFs.",
            "Dimension classifications are provisional text-context evidence.",
            "No dimension is approved for calibration or BIM geometry generation.",
            "A detected no-scale note blocks reliance on printed scale for that page.",
            "Absence of a no-scale note does not validate printed scale.",
            "The source metadata index is not modified.",
        ],
    }

    return result


def run_project_dimension_evidence(
    project_folder: str | Path,
    project_id: str,
    *,
    output_name: str = OUTPUT_NAME,
) -> dict[str, Any]:
    """Build and save the independent Stage 5.2.5 artifact."""

    if (
        Path(output_name).name != output_name
        or not output_name.endswith(".json")
    ):
        raise ValueError(
            "output_name must be a simple JSON filename."
        )

    project_folder = Path(project_folder).resolve()

    report = build_project_dimension_evidence(
        project_folder,
        project_id,
    )

    output_path = (
        project_folder
        / "artifacts"
        / output_name
    )

    _write_json_atomic(
        output_path,
        report,
    )

    # Return a compact execution receipt.
    return {
        "status": report["status"],
        "stage": report["stage"],
        "project_id": project_id,
        "output_artifact": str(output_path),
        "summary": report["summary"],
        "source_index_sha256": report["source"]["sha256"],
    }
