
"""Run Stage 5.2.5 dimension evidence classification."""

from __future__ import annotations

import argparse
import json

from pathlib import Path

from app.services.dimension_evidence_project_runner import (
    run_project_dimension_evidence,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stage 5.2.5 dimension evidence classification."
    )

    parser.add_argument(
        "--project-id",
        required=True,
        help="Project UUID.",
    )

    parser.add_argument(
        "--project-folder",
        default=None,
        help="Optional project folder path.",
    )

    args = parser.parse_args()

    backend_dir = Path(__file__).resolve().parent

    if args.project_folder:
        project_folder = Path(args.project_folder)
    else:
        project_folder = (
            backend_dir
            / "data"
            / "projects"
            / args.project_id
        )

    result = run_project_dimension_evidence(
        project_folder=project_folder,
        project_id=args.project_id,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
