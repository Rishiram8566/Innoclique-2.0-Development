"""
Stage 5.3.4 - wall-height evidence association (CLI runner).

Usage (from the backend folder):
    python run_stage5_3_4_height_association.py --project-id <uuid>
    python run_stage5_3_4_height_association.py --project-id <uuid> --overwrite

Reads  data/projects/<id>/artifacts/geometry_source_evidence_stage5_3_3_v2.json
Writes data/projects/<id>/artifacts/wall_height_association_stage5_3_4.json

Nothing is approved and no geometry is generated.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from app.services.wall_height_evidence_association import (
    SOURCE_ARTIFACT_NAME,
    generate_project_height_association,
)


BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "data" / "projects"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--source", default=SOURCE_ARTIFACT_NAME)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    project_id = str(UUID(args.project_id))
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise SystemExit(f"Project folder not found: {folder}")

    report, output_path = generate_project_height_association(
        folder, overwrite=args.overwrite, source_artifact_name=args.source,
    )

    summary = report["summary"]
    print("=" * 65)
    print("STAGE 5.3.4 - WALL-HEIGHT EVIDENCE ASSOCIATION")
    print("=" * 65)
    print(f"Project:  {project_id}")
    print(f"Output:   {output_path}")
    print(f"Status:   {report['status']}")
    print(f"Level unit convention: {report['level_unit_convention']['status']}"
          f" ({report['level_unit_convention']['unit']})")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    print("\nLevel register:")
    for level in report["level_register"]:
        print(f"  {level['level_key']:<18} x{level['occurrence_count']:<4}"
              f" on {level['drawing_count']} drawing(s)  {level['printed_forms'][:2]}")

    print("\nAdjacent level intervals (NOT wall heights):")
    for interval in report["adjacent_level_intervals"]:
        print(f"  {interval['lower_level_key']} -> {interval['upper_level_key']}:"
              f" {interval['interval_mm']:.0f} mm")

    print("\nAbbreviation context:")
    for item in report["abbreviation_context"]:
        print(f"  {item['abbreviation']}: {item['description_text']!r}"
              f" x{item['occurrence_count']}")

    print("\nWall-type height candidates (unapproved, not linked to geometry):")
    for candidate in report["wall_type_height_candidates"]:
        height = candidate["height_mm"] or candidate["height_range_mm"]
        print(f"  {candidate['component_words']} {height} mm"
              f" x{candidate['occurrence_count']} on {candidate['drawing_count']} drawing(s)")

    print("\nNon-wall / unassigned component heights:")
    for item in report["component_heights"]:
        if item["wall_height_eligible"]:
            continue
        print(f"  [{item['component_class']}] {item['text'][:55]!r}"
              f" -> {item['values_mm']}  {item['warnings'][:1]}")


if __name__ == "__main__":
    main()
