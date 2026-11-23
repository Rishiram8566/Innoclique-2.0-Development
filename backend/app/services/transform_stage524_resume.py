import json
import sys
from pathlib import Path

from app.services.scale_calibration import transform_primitive_batch

PROJECT = Path("data/projects/a5482b08-1c59-4bf0-9eb2-7ea41eb26f9c")
DRAWING_ID = "f165c57e-fcaf-48f2-a2fb-1ec018382638"
PAGE = 1
BATCH_SIZE = 5000

ARTIFACTS = PROJECT / "artifacts/coordinate_transform_stage5_2_4"
CHECKPOINT = ARTIFACTS / f"{DRAWING_ID}_page_{PAGE}_checkpoint.json"

checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
start = int(checkpoint["next_start_index"])
total = int(checkpoint["total_primitives"])

print("=== STAGE 5.2.4 RESUMABLE TRANSFORMATION ===")
print("Starting index:", start)
print("Total primitives:", total)
print("Remaining:", total - start)
print("Batch size:", BATCH_SIZE)
print("Calibration verification required: True")

while start < total:
    expected_next = min(start + BATCH_SIZE, total)

    result = transform_primitive_batch(
        PROJECT,
        drawing_id=DRAWING_ID,
        page_number=PAGE,
        start_index=start,
        batch_size=BATCH_SIZE,
    )

    actual_next = int(result["next_start_index"])
    failures = int(result["failed_count"])
    transformed = int(result["transformed_count"])
    selected = int(result["primitives_in_batch"])

    print(
        f"Batch {start}-{actual_next}: "
        f"selected={selected}, transformed={transformed}, "
        f"failed={failures}"
    )

    if failures:
        print("STOPPED: Transformation failures detected.")
        print(json.dumps(result.get("failures", []), indent=2))
        sys.exit(2)

    if transformed != selected or actual_next != expected_next:
        print("STOPPED: Unexpected batch or checkpoint progression.")
        print(json.dumps(result, indent=2))
        sys.exit(3)

    saved = json.loads(CHECKPOINT.read_text(encoding="utf-8"))

    if int(saved["next_start_index"]) != actual_next:
        print("STOPPED: Saved checkpoint does not match batch result.")
        sys.exit(4)

    start = actual_next

print("\n=== TRANSFORMATION COMPLETE ===")
print("Transformed through:", start)
print("Total primitives:", total)
print("Remaining:", total - start)
print("Calibration verification required: True")
print("Coordinates: page-local millimetres only")
