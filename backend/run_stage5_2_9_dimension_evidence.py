"""CLI entry point for Stage 5.2.9 project dimension classification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.dimension_evidence_project_runner_v2 import (
    run_project_dimension_evidence,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stage 5.2.8 dimension classification on an existing metadata index."
    )
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()

    backend_dir = Path(__file__).resolve().parent
    project_folder = (
        backend_dir / "data" / "projects" / args.project_id
    )

    result = run_project_dimension_evidence(
        project_folder=project_folder,
        project_id=args.project_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
