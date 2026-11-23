
"""Incremental, disk-backed Stage 5.0 geometry pipeline.

Preserves the project-level report schema (drawings[] / failed[]) while
reading existing drawing records one at a time and streaming the report to disk.
Only one drawing record is materialized at a time; primitive arrays are streamed
by the standalone extractor and the JSON writer.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any, Iterator

from .streaming_geometry_extractor import stream_pdf_geometry_to_json
from .geometry_stream_writer import write_json_atomic


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _iter_array_items(path: Path, prefix: str) -> Iterator[dict]:
    """Yield objects from a top-level JSON array without loading the report."""
    if not path.is_file():
        return

    try:
        import ijson
    except ImportError as exc:
        raise RuntimeError(
            "Incremental Stage 5.0 report reading requires ijson. "
            "Install it with: pip install ijson"
        ) from exc

    with path.open("rb") as stream:
        yield from ijson.items(stream, prefix)


def _iter_drawing_metadata(path: Path) -> Iterator[dict]:
    """Yield drawing IDs, source hashes and summaries without building geometry."""
    if not path.is_file():
        return

    try:
        import ijson
        from ijson.common import ObjectBuilder
    except ImportError as exc:
        raise RuntimeError(
            "Incremental report reading requires ijson. "
            "Install it with: pip install ijson"
        ) from exc

    with path.open("rb") as stream:
        current = None
        summary_builder = None

        for prefix, event, value in ijson.parse(stream):

            if prefix == "drawings.item" and event == "start_map":
                current = {}
                summary_builder = None
                continue

            if current is None:
                continue

            if summary_builder is not None:
                summary_builder.event(event, value)

                if (
                    prefix == "drawings.item.summary"
                    and event == "end_map"
                ):
                    current["summary"] = summary_builder.value
                    summary_builder = None

                continue

            if prefix == "drawings.item.drawing_id":
                if event in ("string", "number"):
                    current["drawing_id"] = str(value)

            elif prefix == "drawings.item.source_sha256":
                if event == "string":
                    current["source_sha256"] = value

            elif prefix == "drawings.item.source.sha256":
                if (
                    event == "string"
                    and not current.get("source_sha256")
                ):
                    current["source_sha256"] = value

            elif (
                prefix == "drawings.item.summary"
                and event == "start_map"
            ):
                summary_builder = ObjectBuilder()
                summary_builder.event(event, value)

            elif (
                prefix == "drawings.item"
                and event == "end_map"
            ):
                yield current
                current = None


def _previous_indexes(
    target: Path,
) -> tuple[dict[str, str], dict[str, dict]]:
    """Read drawing hashes and cached failure records."""
    drawing_hashes: dict[str, str] = {}
    failures: dict[str, dict] = {}

    if not target.is_file():
        return drawing_hashes, failures

    for item in _iter_drawing_metadata(target):
        drawing_id = str(item.get("drawing_id") or "")

        if drawing_id:
            drawing_hashes[drawing_id] = str(
                item.get("source_sha256") or ""
            )

    for item in _iter_array_items(target, "failed.item"):
        drawing_id = str(
            item.get("drawing_id") or item.get("filename") or ""
        )

        if drawing_id:
            failures[drawing_id] = item

    return drawing_hashes, failures


def _sidecar_summary(path: Path) -> dict:
    """Read only a small summary object from one standalone drawing JSON file."""
    try:
        import ijson
    except ImportError as exc:
        raise RuntimeError(
            "Install ijson with: pip install ijson"
        ) from exc

    with path.open("rb") as stream:
        values = list(ijson.items(stream, "summary"))

    return values[0] if values else {}


def _load_one_drawing(path: Path) -> dict:
    """Load one drawing at a time when the project report writer consumes it."""
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)

    if not isinstance(value, dict):
        raise ValueError(
            f"Streaming extractor produced an invalid drawing object: {path}"
        )

    return value


def _report_summary(
    records: list[dict],
    previous_path: Path,
    replaced_ids: set[str],
    new_sidecars: list[tuple[str, Path]],
    failed: list[dict],
    newly_processed: int,
    batch_size: int,
) -> dict:
    """Summarize retained and newly extracted drawings without double-counting failures."""
    totals = {
        "page_count": 0,
        "vector_path_count": 0,
        "vector_primitive_count": 0,
        "text_span_count": 0,
        "dimension_candidate_count": 0,
        "level_candidate_count": 0,
    }

    extracted_count = 0
    processed_ids: set[str] = set()

    # Keep previous successful geometry unless it was successfully replaced.
    for item in _iter_drawing_metadata(previous_path):
        drawing_id = str(item.get("drawing_id") or "")

        if not drawing_id or drawing_id in replaced_ids:
            continue

        processed_ids.add(drawing_id)
        summary = item.get("summary") or {}
        extracted_count += 1

        for key in totals:
            totals[key] += int(summary.get(key, 0) or 0)

    # Include newly extracted drawings and their summaries.
    for drawing_id, sidecar in new_sidecars:
        if drawing_id:
            processed_ids.add(str(drawing_id))

        summary = _sidecar_summary(sidecar)
        extracted_count += 1

        for key in totals:
            totals[key] += int(summary.get(key, 0) or 0)

    # Failed/missing drawings count as processed attempts, but a failed retry
    # of a drawing with retained geometry must not count that drawing twice.
    for item in failed:
        drawing_id = str(
            item.get("drawing_id") or item.get("filename") or ""
        )
        if drawing_id:
            processed_ids.add(drawing_id)

    total = len(records)
    attempted = len(processed_ids)
    remaining = max(total - attempted, 0)

    return {
        "drawing_count": total,
        "processed_count": attempted,
        "extracted_count": extracted_count,
        "newly_processed_count": newly_processed,
        "remaining_count": remaining,
        "failed_count": len(failed),
        **totals,
        "batch_size": batch_size,
    }


def extract_project_geometry(
    project_dir: str | Path,
    project: dict,
    *,
    batch_size: int = 1,
    filename: str | None = None,
) -> dict:
    """Process pending/changed PDFs and persist a cumulative streaming report.

    Existing drawing objects are read one at a time. New drawings are first
    extracted to temporary standalone JSON sidecars. The final project report is
    written atomically while drawing records are yielded one at a time.
    """

    if not isinstance(batch_size, int) or not 1 <= batch_size <= 5:
        raise ValueError("batch_size must be an integer between 1 and 5")

    root = Path(project_dir)
    drawings_dir = root / "drawings"
    artifacts_dir = root / "artifacts"
    target = artifacts_dir / "drawing_geometry_stage5.json"

    artifacts_dir.mkdir(parents=True, exist_ok=True)

    drawing_hashes, cached_failures = _previous_indexes(target)

    records = project.get("drawings", [])

    if not isinstance(records, list):
        raise ValueError("Project drawings must be a list.")

    if filename:
        exact_matches = [
            item
            for item in records
            if str(item.get("filename", "")).casefold()
            == filename.strip().casefold()
        ]

        if not exact_matches:
            raise ValueError(
                f"No project PDF exactly matches filename: {filename}"
            )

        if len(exact_matches) > 1:
            raise ValueError(
                f"More than one project PDF matches filename: {filename}"
            )

        records_to_check = exact_matches
    else:
        records_to_check = records

    missing: list[dict] = []
    pending: list[tuple[dict, Path, str]] = []

    for record in records_to_check:
        drawing_id = str(
            record.get("id")
            or record.get("drawing_id")
            or record.get("filename")
            or ""
        )

        stored = record.get("stored_filename")

        if not stored:
            missing.append({
                "drawing_id": drawing_id,
                "filename": record.get("filename"),
                "reason": "stored_filename is missing",
            })
            continue

        source = drawings_dir / Path(stored).name

        if not source.is_file():
            missing.append({
                "drawing_id": drawing_id,
                "filename": record.get("filename"),
                "reason": (
                    "PDF file is missing from project drawings folder"
                ),
            })
            continue

        source_hash = _sha256_file(source)

        if drawing_hashes.get(drawing_id) == source_hash:
            cached_failures.pop(drawing_id, None)
            continue

        if (
            cached_failures.get(drawing_id, {}).get("source_sha256")
            == source_hash
        ):
            continue

        pending.append((record, source, source_hash))

    sidecars: list[tuple[str, Path]] = []
    newly_processed = 0

    # Only successfully extracted drawings may replace existing geometry.
    successful_replaced_ids: set[str] = set()

    new_failures: dict[str, dict] = {}

    try:
        for record, source, source_hash in pending[:batch_size]:
            drawing_id = str(
                record.get("id")
                or record.get("drawing_id")
                or record.get("filename")
                or ""
            )

            cached_failures.pop(drawing_id, None)

            sidecar = artifacts_dir / (
                f".stage5_stream_{uuid.uuid4().hex}.json"
            )

            try:
                extraction = stream_pdf_geometry_to_json(
                    source,
                    sidecar,
                    drawing_id=drawing_id or None,
                    filename=record.get("filename"),
                )

                if extraction.get("source_sha256") != source_hash:
                    raise RuntimeError(
                        "PDF source changed during extraction; "
                        "the generated result was rejected."
                    )

                sidecars.append((drawing_id, sidecar))

                # Mark for replacement only after successful extraction.
                successful_replaced_ids.add(drawing_id)

            except Exception as exc:
                sidecar.unlink(missing_ok=True)

                new_failures[drawing_id] = {
                    "drawing_id": drawing_id,
                    "filename": record.get("filename"),
                    "source_sha256": source_hash,
                    "reason": f"{type(exc).__name__}: {exc}",
                }

            newly_processed += 1

        # Preserve previous failures for drawings not being retried.
        # Successful retries remove their previous failure records.
        failed_by_id = dict(cached_failures)
        failed_by_id.update(new_failures)

        for item in missing:
            key = str(
                item.get("drawing_id") or item.get("filename") or ""
            )
            failed_by_id.setdefault(key, item)

        failed = list(failed_by_id.values())

        summary = _report_summary(
            records,
            target,
            successful_replaced_ids,
            sidecars,
            failed,
            newly_processed,
            batch_size,
        )

        remaining = summary["remaining_count"]

        status = (
            "completed"
            if remaining == 0
            else (
                "in_progress"
                if summary["processed_count"]
                else "pending"
            )
        )

        def drawing_stream() -> Iterator[dict]:
            # Preserve unchanged and unsuccessfully retried drawings.
            for old_drawing in _iter_array_items(
                target, "drawings.item"
            ):
                old_id = str(old_drawing.get("drawing_id") or "")

                if old_id not in successful_replaced_ids:
                    yield old_drawing

            # Read one newly extracted drawing at a time.
            for _, sidecar_path in sidecars:
                drawing = _load_one_drawing(sidecar_path)

                source_metadata = drawing.get("source")

                nested_hash = (
                    source_metadata.get("sha256")
                    if isinstance(source_metadata, dict)
                    else None
                )

                drawing["source_sha256"] = (
                    drawing.get("source_sha256") or nested_hash
                )

                yield drawing

        report = {
            "schema_version": "0.2.0",
            "stage": "5.0",
            "status": status,
            "project_id": project.get("id"),
            "project_name": project.get("name"),
            "generated_at": _utc_now(),
            "summary": summary,
            "drawings": drawing_stream(),
            "failed": failed,
            "engineering_scope": ["architecture", "structural"],
            "semantic_classification_status": (
                "candidate_geometry_only_not_yet_verified"
            ),
            "processing": {
                "mode": "incremental_cached_batches_streaming",
                "next_action": (
                    "Call POST geometry/extract again to process the next batch"
                    if remaining
                    else "All drawings have been attempted"
                ),
            },
            "limitations": [
                "This milestone extracts PDF-space geometry and positioned text; it does not yet produce a BIM model.",
                "No wall/column/beam semantics are asserted without additional recognition and cross-sheet validation.",
                "Scale remains unresolved until explicitly calibrated from a trustworthy dimension or user-confirmed scale.",
                "Scanned image geometry requires a separate raster/vector recognition stage.",
                "Failed drawings require review; successful extraction does not certify engineering correctness.",
                "PyMuPDF materializes one page's vector path collection; text spans and candidates are retained one page at a time.",
            ],
        }

        write_json_atomic(target, report)

        return {
            "schema_version": report["schema_version"],
            "stage": report["stage"],
            "status": status,
            "project_id": project.get("id"),
            "project_name": project.get("name"),
            "generated_at": report["generated_at"],
            "summary": summary,
            "failed": failed,
            "engineering_scope": report["engineering_scope"],
            "semantic_classification_status": (
                report["semantic_classification_status"]
            ),
            "processing": report["processing"],
            "limitations": report["limitations"],
            "report_file": str(target),
        }

    finally:
        for _, sidecar in sidecars:
            sidecar.unlink(missing_ok=True)
