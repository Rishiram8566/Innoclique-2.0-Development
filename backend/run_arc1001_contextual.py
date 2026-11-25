"""
Experimental Stage 5.1.7 contextual recognition for one drawing.

First used on ARC-1001; works for any drawing in a project.

Usage (from the backend folder):
    python run_arc1001_contextual.py --project-id <uuid> --drawing-id <uuid>

Writes artifacts/experimental_contextual_recognition_<drawing-id>.json and
leaves the Stage 5.1.6 recognition report untouched.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from stage_script_args import add_drawing_argument, add_project_argument, project_folder
from app.services.engineering_element_recognition import recognize_pdf_elements


def counts(drawing):
    counter = Counter()
    if drawing:
        for page in drawing.get("pages", []):
            counter.update(
                c.get("element_type", "unknown")
                for c in page.get("candidates", [])
            )
    return counter


def main():
    parser = argparse.ArgumentParser(description="Experimental contextual recognition for one drawing")
    add_project_argument(parser)
    add_drawing_argument(parser)
    args = parser.parse_args()
    drawing_id = args.drawing_id

    root = project_folder(args.project_id)
    project = json.loads((root / "project.json").read_text(encoding="utf-8-sig"))

    record = next(
        (d for d in project["drawings"] if d.get("id") == drawing_id),
        None,
    )
    if record is None:
        raise SystemExit("Drawing not found in this project.")

    pdf_path = root / "drawings" / Path(record["stored_filename"]).name
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    # Run the recognizer directly. Do not call the project-level service.
    result = recognize_pdf_elements(pdf_path, record)

    # Preserve the recognizer result metadata and identify this as experimental.
    result["experimental_contextual_recognition"] = True
    result["experimental_version"] = "5.1.7-contextual-prototype"
    result["stage"] = "5.1.7-experimental"

    artifact = root / "artifacts" / f"experimental_contextual_recognition_{drawing_id}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Load the Stage 5.1.6 report read-only for comparison.
    old_path = root / "artifacts" / "engineering_element_candidates_stage5_1_6.json"
    old_report = json.loads(old_path.read_text(encoding="utf-8"))

    old_drawing = next(
        (d for d in old_report.get("drawings", [])
         if d.get("drawing_id") == drawing_id),
        None,
    )

    old_counts = counts(old_drawing)
    new_counts = counts(result)

    print("\n===== RECOGNITION COMPARISON =====")
    print("Drawing:", record["filename"])
    print("Pages processed:", result["summary"]["page_count"])
    print("Original candidates:", sum(old_counts.values()))
    print("New candidates:", sum(new_counts.values()))

    print("\nTYPE                              OLD       NEW")
    for kind in sorted(set(old_counts) | set(new_counts)):
        print(f"{kind:34} {old_counts[kind]:>5} {new_counts[kind]:>9}")

    print("\n===== VALIDATION CHECKS =====")
    print("Tank boundary candidates:", new_counts["tank_boundary_candidate"])
    print("Equipment outline candidates:", new_counts["equipment_outline_candidate"])
    print("Wall edge candidates:", new_counts["wall_edge_pair_candidate"])
    print("Unclassified pairs:", new_counts["unclassified_parallel_line_pair_candidate"])
    print("Endpoint-only wall candidates:", new_counts["wall_geometry_supported_candidate"])

    print("\nExperimental artifact:", artifact.resolve())
    print("Original report preserved:", old_path.is_file())
    print("Source PDF SHA256:", result["source_sha256"])


if __name__ == "__main__":
    main()
