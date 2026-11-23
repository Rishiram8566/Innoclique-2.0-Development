from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ijson

from app.services import bim_geometry_builder as builder


PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"

RECOGNITION_FILE = "engineering_element_candidates_stage5_1_6.json"
GEOMETRY_FILE = "drawing_geometry_stage5.json"
REPORT_FILE = "wall_candidate_validation_stage5_3.json"

VALIDATOR_VERSION = "0.1.0"
GEOMETRY_TOLERANCE_PT = 0.65
RATIO_TOLERANCE = 0.01


def finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not numeric") from None

    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite")

    return result


def load_recognition(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    if not isinstance(data, dict):
        raise ValueError("Recognition artifact root must be an object")

    if not isinstance(data.get("drawings"), list):
        raise ValueError("Recognition artifact has no drawings list")

    return data


def find_drawing(report: dict, drawing_id: str) -> dict:
    matches = [
        item for item in report["drawings"]
        if isinstance(item, dict)
        and item.get("drawing_id") == drawing_id
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected one recognition drawing; found {len(matches)}"
        )

    return matches[0]


def load_geometry_drawing(path: Path, drawing_id: str) -> dict:
    """Load one drawing object, never the complete project artifact."""

    with path.open("rb") as stream:
        for drawing in ijson.items(stream, "drawings.item"):
            if (
                isinstance(drawing, dict)
                and drawing.get("drawing_id") == drawing_id
            ):
                return drawing

    raise ValueError(
        f"Drawing {drawing_id} not found in geometry artifact"
    )


def get_page(drawing: dict, page_number: int) -> dict:
    pages = drawing.get("pages")

    if not isinstance(pages, list):
        raise ValueError("Drawing has no pages list")

    matches = [
        page for page in pages
        if isinstance(page, dict)
        and page.get("page_number") == page_number
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected one geometry page {page_number}; found {len(matches)}"
        )

    return matches[0]


def source_reference(candidate: dict, key: str) -> tuple[int, int]:
    source = candidate.get("source")

    if not isinstance(source, dict):
        raise ValueError("Candidate source object is missing")

    ref = source.get(key)

    if not isinstance(ref, (list, tuple)) or len(ref) != 2:
        raise ValueError(f"Invalid {key} reference")

    if any(isinstance(v, bool) for v in ref):
        raise ValueError(f"Boolean index is not valid in {key}")

    try:
        path_index = int(ref[0])
        item_index = int(ref[1])
    except (TypeError, ValueError):
        raise ValueError(f"Non-integer source index in {key}") from None

    if path_index < 0 or item_index < 0:
        raise ValueError(f"Negative source index in {key}")

    return path_index, item_index


def build_source_map(
    primitives: list,
    candidates: list[dict],
    page_number: int,
) -> dict:
    """Index only the source primitives requested by this candidate batch."""

    wanted = set()

    for candidate in candidates:
        for key in ("line_a", "line_b"):
            wanted.add(source_reference(candidate, key))

    found: dict[tuple[int, int], list[dict]] = {
        ref: [] for ref in wanted
    }

    for primitive in primitives:
        if not isinstance(primitive, dict):
            continue

        try:
            key = (
                int(primitive.get("path_index")),
                int(primitive.get("item_index")),
            )
        except (TypeError, ValueError):
            continue

        if key not in wanted:
            continue

        if primitive.get("type") != "line":
            continue

        if int(primitive.get("page_number", 0)) != page_number:
            continue

        found[key].append(primitive)

    result = {}

    for ref, matches in found.items():
        if len(matches) != 1:
            raise ValueError(
                f"Source reference {ref}: expected exactly one line "
                f"on page {page_number}; found {len(matches)}"
            )
        result[ref] = matches[0]

    return result


def compare_measurement(
    candidate: dict,
    key: str,
    actual: float,
    tolerance: float,
    errors: list[str],
) -> None:
    expected_value = candidate.get(key)

    if expected_value is None:
        return

    try:
        expected = finite(expected_value, key)
    except ValueError as exc:
        errors.append(str(exc))
        return

    difference = abs(expected - actual)

    if difference > tolerance:
        errors.append(
            f"{key} mismatch: recognition={expected:.6f}, "
            f"source={actual:.6f}, difference={difference:.6f}"
        )


def validate_candidate(
    candidate: dict,
    source_map: dict,
    drawing: dict,
) -> dict:
    errors: list[str] = []
    measurements: dict[str, Any] = {}

    page_number = int(candidate.get("page_number", 0))
    geometry_type = candidate.get("geometry_type")

    ref_a = source_reference(candidate, "line_a")
    ref_b = source_reference(candidate, "line_b")

    if ref_a == ref_b:
        raise ValueError("Candidate references the same source line twice")

    line_a = source_map[ref_a]
    line_b = source_map[ref_b]

    if int(line_a.get("page_number", 0)) != page_number:
        errors.append("Source line A belongs to another page")

    if int(line_b.get("page_number", 0)) != page_number:
        errors.append("Source line B belongs to another page")

    if candidate.get("element_type") not in builder.SUPPORTED_WALL_CANDIDATE_TYPES:
        errors.append("Unsupported candidate element type")

    if geometry_type not in builder.SUPPORTED_WALL_GEOMETRIES:
        errors.append("Unsupported geometry orientation")

    if candidate.get("verification_required") is not True:
        errors.append("Candidate verification flag is not preserved")

    ax1, ay1, ax2, ay2 = builder._line_endpoints(line_a, "line_a")
    bx1, by1, bx2, by2 = builder._line_endpoints(line_b, "line_b")

    length_a = math.hypot(ax2 - ax1, ay2 - ay1)
    length_b = math.hypot(bx2 - bx1, by2 - by1)

    if geometry_type == "parallel_horizontal_lines":
        interval_a = sorted((ax1, ax2))
        interval_b = sorted((bx1, bx2))
        separation = abs(
            ((ay1 + ay2) / 2.0) - ((by1 + by2) / 2.0)
        )

    elif geometry_type == "parallel_vertical_lines":
        interval_a = sorted((ay1, ay2))
        interval_b = sorted((by1, by2))
        separation = abs(
            ((ax1 + ax2) / 2.0) - ((bx1 + bx2) / 2.0)
        )

    else:
        interval_a = [0.0, 0.0]
        interval_b = [0.0, 0.0]
        separation = 0.0

    overlap = max(
        0.0,
        min(interval_a[1], interval_b[1])
        - max(interval_a[0], interval_b[0]),
    )

    shorter_length = min(length_a, length_b)

    overlap_ratio = (
        overlap / shorter_length if shorter_length > 0 else 0.0
    )

    line_length_ratio = (
        shorter_length / max(length_a, length_b)
        if max(length_a, length_b) > 0 else 0.0
    )

    measurements = {
        "source_line_a_length_pdf_points": round(length_a, 6),
        "source_line_b_length_pdf_points": round(length_b, 6),
        "overlap_length_pdf_points": round(overlap, 6),
        "estimated_thickness_pdf_points": round(separation, 6),
        "overlap_ratio": round(overlap_ratio, 8),
        "line_length_ratio": round(line_length_ratio, 8),
    }

    if overlap <= 0:
        errors.append("Source lines have no positive longitudinal overlap")

    if separation <= 0:
        errors.append("Source lines have zero or negative separation")

    if max(length_a, length_b) <= 0:
        errors.append("Source line length is zero")

    if geometry_type in builder.SUPPORTED_WALL_GEOMETRIES:
        try:
            bbox = list(
                builder._wall_footprint(
                    line_a, line_b, geometry_type
                )
            )

            builder._validate_candidate_bbox(candidate, bbox)

            measurements["source_bbox_pdf_points"] = [
                round(value, 6) for value in bbox
            ]

            measurements["bbox_match"] = True

        except (ValueError, TypeError) as exc:
            errors.append(f"Bounding-box validation: {exc}")
            measurements["bbox_match"] = False

    compare_measurement(
        candidate,
        "estimated_thickness_pdf_points",
        separation,
        GEOMETRY_TOLERANCE_PT,
        errors,
    )

    compare_measurement(
        candidate,
        "overlap_length_pdf_points",
        overlap,
        GEOMETRY_TOLERANCE_PT,
        errors,
    )

    compare_measurement(
        candidate,
        "overlap_ratio",
        overlap_ratio,
        RATIO_TOLERANCE,
        errors,
    )

    compare_measurement(
        candidate,
        "line_length_ratio",
        line_length_ratio,
        RATIO_TOLERANCE,
        errors,
    )

    return {
        "element_type": candidate.get("element_type"),
        "geometry_type": geometry_type,
        "page_number": page_number,
        "source_references": {
            "line_a": list(ref_a),
            "line_b": list(ref_b),
        },
        "candidate_confidence": candidate.get("confidence"),
        "candidate_verification_required": True,
        "approval_status": "not_approved",
        "geometry_status": (
            "geometry_validated" if not errors else "geometry_invalid"
        ),
        "measurements": measurements,
        "errors": errors,
        "semantic_status": "unconfirmed_wall_hypothesis",
        "source_sha256": drawing.get("source_sha256"),
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=path.stem + "_",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            fd, "w", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(
                payload,
                stream,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_name, path)

    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def run_validation(
    project_id: str,
    drawing_id: str,
    limit: int = 25,
) -> dict:
    if limit < 1:
        raise ValueError("Candidate limit must be positive")

    folder = PROJECTS_DIR / project_id

    if not folder.is_dir():
        raise FileNotFoundError(f"Project folder not found: {folder}")

    artifacts = folder / "artifacts"

    recognition_path = artifacts / RECOGNITION_FILE
    geometry_path = artifacts / GEOMETRY_FILE

    report = load_recognition(recognition_path)
    recognition_drawing = find_drawing(report, drawing_id)
    geometry_drawing = load_geometry_drawing(
        geometry_path, drawing_id
    )

    recognition_hash = recognition_drawing.get("source_sha256")
    geometry_hash = (
        geometry_drawing.get("source", {}).get("sha256")
    )

    if recognition_hash and geometry_hash:
        if recognition_hash != geometry_hash:
            raise ValueError(
                "Recognition and geometry source PDF hashes do not match"
            )

    all_candidates = []

    for page in recognition_drawing.get("pages", []):
        if not isinstance(page, dict):
            continue

        candidates = page.get("candidates", [])

        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and candidate.get("element_type")
                in builder.SUPPORTED_WALL_CANDIDATE_TYPES
            ):
                all_candidates.append(candidate)

    selected = all_candidates[:limit]

    if not selected:
        raise ValueError("No supported wall candidates found")

    results = []
    pages_by_number = {}

    for candidate in selected:
        page_number = int(candidate.get("page_number", 0))

        if page_number not in pages_by_number:
            pages_by_number[page_number] = get_page(
                geometry_drawing, page_number
            )

        page = pages_by_number[page_number]
        primitives = page.get("primitives")

        if not isinstance(primitives, list):
            raise ValueError(
                f"Page {page_number} has no primitives list"
            )

        source_map = build_source_map(
            primitives,
            [candidate],
            page_number,
        )

        try:
            result = validate_candidate(
                candidate,
                source_map,
                recognition_drawing,
            )
        except (ValueError, TypeError, KeyError) as exc:
            result = {
                "element_type": candidate.get("element_type"),
                "page_number": page_number,
                "source_references": candidate.get("source"),
                "candidate_confidence": candidate.get("confidence"),
                "candidate_verification_required": True,
                "approval_status": "not_approved",
                "geometry_status": "geometry_invalid",
                "measurements": {},
                "errors": [str(exc)],
                "semantic_status": "unconfirmed_wall_hypothesis",
            }

        results.append(result)

    validated_count = sum(
        item["geometry_status"] == "geometry_validated"
        for item in results
    )

    invalid_count = len(results) - validated_count

    output = {
        "schema_version": "0.1.0",
        "validator_version": VALIDATOR_VERSION,
        "stage": "5.3.4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "drawing_id": drawing_id,
        "drawing_filename": recognition_drawing.get("filename"),
        "source_pdf_sha256": recognition_hash,
        "source_geometry_artifact": GEOMETRY_FILE,
        "source_recognition_artifact": RECOGNITION_FILE,
        "validation_policy": {
            "geometry_tolerance_pdf_points": GEOMETRY_TOLERANCE_PT,
            "ratio_tolerance": RATIO_TOLERANCE,
            "real_world_scale_applied": False,
            "semantic_wall_confirmation_performed": False,
            "automatic_approval": False,
        },
        "summary": {
            "available_wall_candidates": len(all_candidates),
            "processed_candidates": len(results),
            "geometry_validated_count": validated_count,
            "geometry_invalid_count": invalid_count,
            "semantic_confirmation_count": 0,
            "approved_wall_count": 0,
        },
        "results": results,
        "limitations": [
            "Geometric validation does not prove that a candidate is a physical wall.",
            "Real-world scale remains unresolved and no metric dimensions are inferred.",
            "No wall height or 3D mesh is generated.",
            "All candidates remain unapproved and require engineering verification.",
            "Only the requested candidate batch was processed.",
        ],
    }

    output_path = artifacts / REPORT_FILE
    atomic_write_json(output_path, output)

    print("\n=== WALL GEOMETRY VALIDATION ===")
    print("Drawing:", recognition_drawing.get("filename"))
    print("Available wall candidates:", len(all_candidates))
    print("Processed:", len(results))
    print("Geometry validated:", validated_count)
    print("Geometry invalid:", invalid_count)
    print("Approved walls: 0")
    print("Report:", output_path)

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Stage 5.1.6 wall geometry candidates"
    )

    parser.add_argument("--project-id", required=True)
    parser.add_argument("--drawing-id", required=True)
    parser.add_argument("--limit", type=int, default=25)

    args = parser.parse_args()

    run_validation(
        project_id=args.project_id,
        drawing_id=args.drawing_id,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
