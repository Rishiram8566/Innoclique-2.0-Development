"""
Stage 5.2.4 - resume a checkpointed page-local coordinate transformation.

Usage (from the backend folder):
    python -m app.services.transform_stage524_resume --project-id <uuid> --drawing-id <uuid> [--page 1] [--batch-size 5000]

Coordinates stay page-local millimetres; calibration verification is still required.
"""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from app.services.scale_calibration import transform_primitive_batch

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError(f"not a valid UUID: {value!r}") from None


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume a Stage 5.2.4 batch transformation")
    parser.add_argument("--project-id", required=True, type=_uuid)
    parser.add_argument("--drawing-id", required=True, type=_uuid)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    project = PROJECTS_DIR / args.project_id
    if not project.is_dir():
        raise SystemExit(f"Project folder not found: {project}")

    artifacts = project / "artifacts" / "coordinate_transform_stage5_2_4"
    checkpoint_path = artifacts / f"{args.drawing_id}_page_{args.page}_checkpoint.json"

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    start = int(checkpoint["next_start_index"])
    total = int(checkpoint["total_primitives"])

    print("=== STAGE 5.2.4 RESUMABLE TRANSFORMATION ===")
    print("Starting index:", start)
    print("Total primitives:", total)
    print("Remaining:", total - start)
    print("Batch size:", args.batch_size)
    print("Calibration verification required: True")

    while start < total:
        expected_next = min(start + args.batch_size, total)

        result = transform_primitive_batch(
            project,
            drawing_id=args.drawing_id,
            page_number=args.page,
            start_index=start,
            batch_size=args.batch_size,
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

        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    main()
