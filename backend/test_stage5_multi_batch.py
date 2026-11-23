
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import ijson

from app.services.geometry_pipeline import extract_project_geometry


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"

SOURCE_PROJECT = (
    Path("data/projects") / PROJECT_ID / "project.json"
)

SOURCE_DRAWINGS = (
    Path("data/projects") / PROJECT_ID / "drawings"
)

TEST_ROOT = (
    Path("data/validation_stage5")
    / f"multi_batch_test_{uuid.uuid4().hex[:8]}"
)

BATCH_SIZE = 1
TEST_DRAWING_COUNT = 3


def sha256_file(path):
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def read_report_metadata(report_path):
    with report_path.open("rb") as stream:
        drawing_ids = list(
            ijson.items(stream, "drawings.item.drawing_id")
        )

    with report_path.open("rb") as stream:
        source_hashes = list(
            ijson.items(stream, "drawings.item.source_sha256")
        )

    with report_path.open("rb") as stream:
        summary = next(ijson.items(stream, "summary"), {})

    with report_path.open("rb") as stream:
        failures = list(ijson.items(stream, "failed.item"))

    return drawing_ids, source_hashes, summary, failures


def main():
    if not SOURCE_PROJECT.is_file():
        raise FileNotFoundError(SOURCE_PROJECT)

    source_project = json.loads(
        SOURCE_PROJECT.read_text(encoding="utf-8")
    )

    source_records = source_project.get("drawings", [])

    if len(source_records) < TEST_DRAWING_COUNT:
        raise RuntimeError("Not enough source drawing records.")

    records = source_records[:TEST_DRAWING_COUNT]

    drawings_dir = TEST_ROOT / "drawings"
    drawings_dir.mkdir(parents=True, exist_ok=False)

    test_records = []
    expected_hashes = {}

    print("ISOLATED TEST FOLDER:", TEST_ROOT)

    # Copy and verify the first three original PDFs.
    for record in records:
        stored_filename = record.get("stored_filename")

        if not stored_filename:
            raise RuntimeError(
                f"Missing stored_filename: {record}"
            )

        source = SOURCE_DRAWINGS / Path(stored_filename).name
        destination = drawings_dir / Path(stored_filename).name

        if not source.is_file():
            raise FileNotFoundError(source)

        shutil.copy2(source, destination)

        source_hash = sha256_file(source)
        copied_hash = sha256_file(destination)

        if source_hash != copied_hash:
            raise RuntimeError(
                f"Copy SHA256 mismatch: {stored_filename}"
            )

        expected_hashes[str(record["id"])] = source_hash
        test_records.append(dict(record))

        print("PDF COPY VERIFIED:", record["filename"])

    test_project = {
        "id": "stage5-multi-batch-validation",
        "name": "Stage 5 Multi-Batch Validation",
        "drawings": test_records,
    }

    report_path = (
        TEST_ROOT
        / "artifacts"
        / "drawing_geometry_stage5.json"
    )

    # Process one drawing per call.
    for batch_number in range(1, TEST_DRAWING_COUNT + 1):
        print(f"\nSTARTING BATCH {batch_number}")

        result = extract_project_geometry(
            TEST_ROOT,
            test_project,
            batch_size=BATCH_SIZE,
        )

        if not report_path.is_file():
            raise AssertionError("Cumulative report was not created.")

        ids, hashes, summary, failures = read_report_metadata(
            report_path
        )

        expected_ids = [
            str(record["id"])
            for record in test_records[:batch_number]
        ]

        if ids != expected_ids:
            raise AssertionError(
                f"Unexpected drawing IDs: {ids}"
            )

        actual_hashes = dict(zip(ids, hashes))

        for drawing_id in expected_ids:
            if actual_hashes.get(drawing_id) != expected_hashes[drawing_id]:
                raise AssertionError(
                    f"Source hash mismatch for {drawing_id}"
                )

        if failures:
            raise AssertionError(
                f"Unexpected extraction failures: {failures}"
            )

        if summary.get("extracted_count") != batch_number:
            raise AssertionError(
                f"Unexpected extracted count: {summary}"
            )

        if summary.get("newly_processed_count") != 1:
            raise AssertionError(
                f"Expected exactly one new extraction: {summary}"
            )

        expected_remaining = TEST_DRAWING_COUNT - batch_number

        if summary.get("remaining_count") != expected_remaining:
            raise AssertionError(
                f"Unexpected remaining count: {summary}"
            )

        print("BATCH STATUS:", result.get("status"))
        print("DRAWINGS IN REPORT:", len(ids))
        print("NEWLY PROCESSED:", summary["newly_processed_count"])
        print("REMAINING:", summary["remaining_count"])
        print(f"BATCH {batch_number}: PASS")

    # Run once more. Nothing should be extracted again.
    print("\nCHECKING RESUME / NO-DUPLICATE BEHAVIOUR")

    final_result = extract_project_geometry(
        TEST_ROOT,
        test_project,
        batch_size=BATCH_SIZE,
    )

    final_summary = final_result.get("summary", {})

    if final_summary.get("newly_processed_count") != 0:
        raise AssertionError(
            "Completed drawings were unexpectedly re-extracted."
        )

    if final_summary.get("extracted_count") != TEST_DRAWING_COUNT:
        raise AssertionError("Final extracted count is incorrect.")

    if final_summary.get("remaining_count") != 0:
        raise AssertionError("Expected zero remaining drawings.")

    final_ids, final_hashes, _, final_failures = read_report_metadata(
        report_path
    )

    if final_ids != [
        str(record["id"]) for record in test_records
    ]:
        raise AssertionError("Final report drawing IDs are incorrect.")

    if final_failures:
        raise AssertionError(
            f"Final report contains failures: {final_failures}"
        )

    print("NO DUPLICATE EXTRACTION: PASS")
    print("ALL THREE DRAWINGS PRESERVED: PASS")
    print("FINAL STATUS:", final_result.get("status"))
    print("\nMULTI-DRAWING BATCH AND RESUME TEST: PASS")
    print("VALIDATION REPORT:", report_path)


if __name__ == "__main__":
    main()
