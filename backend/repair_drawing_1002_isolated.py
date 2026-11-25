"""
LEGACY FIXTURE SCRIPT - not a pipeline stage.

Pinned (by project ID, drawing ID and SHA-256 checks) to the repaired
ARC-1002 validation fixture of the older project 5e9627a7-...-c2e2c5baf845.
Kept for reproducibility of past validation runs only. Do not adapt it to run
against other projects; use the project-wide stage runners instead.
"""

import hashlib
import json
from pathlib import Path

import ijson

from app.services.geometry_stream_writer import write_json_atomic


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"
DRAWING_ID = "011cfd18-d896-4852-90aa-13ee043ecacf"
PDF_HASH = "960f5eb94b56676a4683bff760ae4286c99eaa1dce5213c06943125259252500"

PRODUCTION = Path("data/projects") / PROJECT_ID / "artifacts/drawing_geometry_stage5.json"
BACKUP = Path("data/validation_stage5_3/drawing_geometry_stage5_before_repair.json")
REPLACEMENT = Path("data/validation_stage5/drawing_1002_complete_test.json")
OUTPUT = Path("data/validation_stage5_3/drawing_geometry_stage5_repaired_test.json")

EXPECTED_HASH = "FEF3EECD19F51D0C8C395E549F96891EA7F2AF782DEE4B9A9759127CDF6A1C0D"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def read_top_metadata(path):
    metadata = {}

    with path.open("rb") as stream:
        for key, value in ijson.kvitems(stream, ""):
            if key != "drawings":
                metadata[key] = value

    return metadata


def validate_replacement():
    with REPLACEMENT.open("r", encoding="utf-8") as f:
        item = json.load(f)

    if item.get("drawing_id") != DRAWING_ID:
        raise RuntimeError("Replacement drawing ID mismatch.")

    if item.get("filename") != "DSP-DCP-DWG-ARC-1002_BASEMENT FLOOR - UPPER LEVEL.pdf":
        raise RuntimeError("Replacement filename mismatch.")

    if item.get("source", {}).get("sha256") != PDF_HASH:
        raise RuntimeError("Replacement PDF source hash mismatch.")

    pages = item.get("pages", [])

    if len(pages) != 1:
        raise RuntimeError("Expected exactly one page.")

    if len(pages[0].get("primitives", [])) != 282190:
        raise RuntimeError("Replacement primitive count mismatch.")

    if pages[0].get("vector_primitive_count") != 282190:
        raise RuntimeError("Reported primitive count mismatch.")

    item["source_sha256"] = PDF_HASH

    return item


def main():
    print("STEP 1: Checking production and backup hashes...")

    if sha256(PRODUCTION) != EXPECTED_HASH:
        raise RuntimeError("Production hash mismatch. STOP.")

    if sha256(BACKUP) != EXPECTED_HASH:
        raise RuntimeError("Backup hash mismatch. STOP.")

    print("Production and backup hashes verified.")

    print("STEP 2: Validating replacement drawing...")
    replacement = validate_replacement()

    print("Replacement drawing verified.")

    print("STEP 3: Reading project metadata...")
    metadata = read_top_metadata(PRODUCTION)

    print("PROJECT ID:", repr(metadata.get("project_id")))
    print("SCHEMA:", repr(metadata.get("schema_version")))

    if metadata.get("project_id") != PROJECT_ID:
        raise RuntimeError("Production project ID mismatch.")

    if metadata.get("schema_version") != "0.2.0":
        raise RuntimeError("Unexpected production report schema.")

    def drawing_stream():
        count = 0
        replaced = 0

        with PRODUCTION.open("rb") as stream:
            for drawing in ijson.items(stream, "drawings.item"):
                count += 1

                if drawing.get("drawing_id") == DRAWING_ID:
                    replaced += 1
                    yield replacement
                else:
                    yield drawing

        if count != 71:
            raise RuntimeError(
                f"Expected 71 drawings, found {count}."
            )

        if replaced != 1:
            raise RuntimeError(
                f"Expected one replacement, found {replaced}."
            )

    metadata["drawings"] = drawing_stream()

    print("STEP 4: Writing isolated validation report...")

    result = write_json_atomic(OUTPUT, metadata)

    print("\nISOLATED REPAIR CREATED")
    print("OUTPUT:", result["output_file"])
    print("SIZE:", result["size_bytes"])
    print("WRITE COMPLETE:", result["write_complete"])
    print("PRODUCTION UNCHANGED:", sha256(PRODUCTION) == EXPECTED_HASH)


if __name__ == "__main__":
    main()
