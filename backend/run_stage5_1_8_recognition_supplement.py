"""
Stage 5.1.8 - recognition supplement (CLI runner).

Re-runs recognition for chosen drawings with separate horizontal/vertical
candidate budgets and stores only the NEW parallel-pair candidates (geometry
checked against the page sidecar) next to the untouched Stage 5.1.6 report.

Usage (from the backend folder):
    python run_stage5_1_8_recognition_supplement.py --project-id <uuid> --drawing-id <uuid> [--drawing-id <uuid> ...]
"""

import argparse

from stage_script_args import add_project_argument, project_folder, uuid_arg
from app.services.recognition_supplement import generate_supplement


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.1.8 recognition supplement")
    add_project_argument(parser)
    parser.add_argument("--drawing-id", action="append", required=True, type=uuid_arg,
                        help="Drawing to re-run (repeat for several).")
    args = parser.parse_args()

    report, path = generate_supplement(project_folder(args.project_id), args.project_id, args.drawing_id)
    print("STAGE 5.1.8 - RECOGNITION SUPPLEMENT")
    print("Output:", path)
    for drawing in report["drawings"]:
        if drawing["drawing_id"] not in args.drawing_id:
            continue
        validated = sum(p["added_geometry_validated"] for p in drawing["pages"])
        existing = sum(
            1 for p in drawing["pages"] for status in p["existing_candidate_validation"].values()
            if status == "geometry_validated"
        )
        print(f"  {drawing['filename']}: +{drawing['summary']['added_candidates']} candidates "
              f"{drawing['summary']['added_by_orientation']}, {validated} geometry-validated; "
              f"existing unclassified pairs validated: {existing}")


if __name__ == "__main__":
    main()
