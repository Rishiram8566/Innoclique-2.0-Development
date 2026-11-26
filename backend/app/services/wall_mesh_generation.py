"""
Stage 5.4 - verified wall model generation.

Builds 3D wall meshes for one drawing page from walls a reviewer has
confirmed in Stage 5.3.5, using the existing Stage 5.3 geometry builder.

Every wall in the output has passed all gates:

* height: an active, reviewer-confirmed Stage 5.3.5 verification supplies
  the builder's drawing-derived height evidence;
* calibration: the page has a Stage 5.2 calibration record;
* source lines: both source lines are read from the page's primitive sidecar,
  whose PDF hash and file hash are checked;
* mesh: the builder re-checks footprint, bounding box and closed topology.

Nothing is invented: unverified candidates are not built, the wall base is
page-local z = 0 (level placement is not established), and coordinates are
page-local millimetres, not a shared building frame.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import bim_geometry_builder as wall_builder
from app.services import height_verification_ledger as ledger_service
from app.services import column_detection as columns_module
from app.services import hatch_wall_detection as hatch_walls
from app.services import opening_detection as openings_module
from app.services.recognition_supplement import read_recognition


STAGE = "5.4"
SCHEMA_VERSION = "0.1.0"
OUTPUT_DIRECTORY = "mesh_batches"
SIDECAR_DIRECTORY = "primitive_sidecars"

# Walls are architectural elements; the product's LOD rules show them from LOD 300.
WALL_MINIMUM_LOD = 300


class GenerationBlocked(ValueError):
    """Generation cannot proceed for a reason the user must resolve first."""


def output_artifact_name(drawing_id: str) -> str:
    return f"{drawing_id}_stage5_4.json"


def _page_size(value: Any) -> tuple[float, float] | None:
    """Page size as (width, height); recognition stores {"width", "height"} or [w, h]."""
    try:
        if isinstance(value, dict):
            width, height = float(value["width"]), float(value["height"])
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            width, height = float(value[0]), float(value[1])
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sidecar_lines(
    artifacts: Path,
    drawing_id: str,
    page_number: int,
    wanted: set[tuple[int, int]],
    expected_pdf_sha256: str,
) -> tuple[dict[tuple[int, int], list[dict[str, Any]]], dict[str, Any]]:
    """
    Read the requested (path_index, item_index) primitives from a page sidecar.

    Returns ({key: [primitives]}, sidecar provenance). Every match is kept so
    the builder can reject duplicates instead of this function picking one.
    """
    stem = f"{drawing_id}_page_{page_number}"
    folder = artifacts / SIDECAR_DIRECTORY
    sidecar = folder / f"{stem}.jsonl"
    manifest_path = folder / f"{stem}_manifest.json"

    if not sidecar.is_file() or not manifest_path.is_file():
        raise GenerationBlocked(
            "Primitive sidecar for this page is missing. Create it with: "
            "python -m app.services.primitive_sidecar_batch "
            f"--project-id <project> --drawing-id {drawing_id}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("source_pdf_sha256") != expected_pdf_sha256:
        raise GenerationBlocked("Primitive sidecar was extracted from a different PDF")
    sidecar_hash = _sha256(sidecar)
    if manifest.get("sidecar_sha256") and manifest["sidecar_sha256"] != sidecar_hash:
        raise GenerationBlocked("Primitive sidecar file does not match its manifest hash")

    found: dict[tuple[int, int], list[dict[str, Any]]] = {key: [] for key in wanted}
    with sidecar.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            primitive = json.loads(line)
            key = (primitive.get("path_index"), primitive.get("item_index"))
            if key in found:
                found[key].append(primitive)

    provenance = {
        "file": sidecar.name,
        "sha256": sidecar_hash,
        "primitive_count": manifest.get("primitive_count"),
        "coordinate_system": manifest.get("coordinate_system"),
    }
    return found, provenance


def build_verified_wall_model(
    *,
    project_id: str,
    recognition_report: dict[str, Any],
    calibration_report: dict[str, Any] | None,
    ledger: dict[str, Any],
    drawing_id: str,
    page_number: int,
    source_line_loader,
    column_loader=None,
) -> dict[str, Any]:
    """
    Build the Stage 5.4 artifact for one page.

    ``source_line_loader(wanted_keys, source_pdf_sha256)`` returns
    ({(path_index, item_index): [primitives]}, provenance).
    """

    if recognition_report.get("project_id") != project_id:
        raise ValueError("Recognition report project ownership mismatch")
    if ledger.get("project_id") != project_id:
        raise ValueError("Verification ledger project ownership mismatch")

    try:
        drawing, page = ledger_service._find_recognition_page(
            recognition_report, drawing_id, page_number
        )
    except LookupError as exc:
        raise ValueError(str(exc)) from None

    calibration = None
    if isinstance(calibration_report, dict):
        calibration = (
            calibration_report.get("calibrations", {})
            .get(drawing_id, {})
            .get(str(page_number))
        )
    if not isinstance(calibration, dict) or calibration.get("status") != "calibrated":
        raise GenerationBlocked(
            "This page has no Stage 5.2 calibration, so metric walls cannot be built."
        )

    # Confirmed walls on this page, from the ledger only.
    confirmed: list[tuple[str, dict[str, Any]]] = []
    for element_id, record in sorted(ledger_service.active_verifications_by_element(ledger).items()):
        if (
            record.get("decision") == "confirmed"
            and record.get("drawing_id") == drawing_id
            and record.get("page_number") == page_number
        ):
            confirmed.append((element_id, record))
    confirmed_detected = {
        element_id: record.get("element_kind", "column")
        for element_id, record in ledger_service.active_column_confirmations(ledger).items()
        if record.get("drawing_id") == drawing_id and record.get("page_number") == page_number
    }
    confirmed_columns = [i for i, kind in confirmed_detected.items() if kind == "column"]
    confirmed_hatch_walls = [i for i, kind in confirmed_detected.items() if kind == "hatch_wall"]
    if not confirmed and not confirmed_detected:
        raise GenerationBlocked(
            "No reviewer-confirmed walls or columns on this page yet. Confirm walls in "
            "Stage 5.3.5 height verification (or columns / hatch walls) first."
        )

    candidates: dict[str, dict[str, Any]] = {}
    for candidate in page.get("candidates", []):
        if ledger_service._is_supported_wall(candidate):
            try:
                candidates[ledger_service.wall_element_id(drawing_id, page_number, candidate)] = candidate
            except (TypeError, ValueError):
                continue

    wanted: set[tuple[int, int]] = set()
    for element_id, _ in confirmed:
        _, lines = ledger_service.element_source_lines(element_id)
        wanted.update(lines)

    source_sha = drawing.get("source_sha256")
    lines_by_key, sidecar_provenance = (
        source_line_loader(wanted, source_sha) if wanted else ({}, None)
    )

    recognition_ref = {
        "drawing_id": drawing_id,
        "source_sha256": source_sha,
        "project_id": project_id,
    }

    elements, failed = [], []
    for element_id, record in confirmed:
        try:
            candidate = candidates.get(element_id)
            if candidate is None:
                raise ValueError("Confirmed element is no longer a wall candidate in the recognition report")
            height_evidence = ledger_service.resolve_builder_height_evidence(ledger, element_id)
            if height_evidence is None:
                raise ValueError("No confirmed height evidence for this element")
            _, lines = ledger_service.element_source_lines(element_id)
            primitives = [p for key in lines for p in lines_by_key.get(key, [])]

            # Only reviewer-confirmed elements reach this point, so an
            # unclassified parallel pair may be built as the wall it was confirmed as.
            mesh = wall_builder.build_wall_mesh(
                recognition_report=recognition_ref,
                candidate={**candidate, "reviewer_confirmed_wall": True},
                source_primitives=primitives,
                calibration_record=calibration,
                height_evidence=height_evidence,
            )
            if mesh["element_id"] != element_id:
                raise ValueError("Builder element ID does not match the verified element")

            component = (record.get("semantic_confirmation") or {}).get("component") or "wall"
            mesh["stage"] = STAGE
            mesh["component"] = component
            mesh["lod"] = {"category": "wall", "minimum_lod": WALL_MINIMUM_LOD}
            mesh["height_verification"] = {
                "verification_id": record["verification_id"],
                "reviewer": record.get("reviewer"),
                "verified_at": record.get("created_at"),
                "printed_measurements": (record.get("height_candidate") or {}).get(
                    "printed_measurements", []
                ),
            }
            mesh["placement"] = {
                "base_z_mm": 0.0,
                "base_status": "page_local_zero_level_not_established",
            }
            mesh["limitations"] = [
                "Wall semantics and height were confirmed by a reviewer; the "
                "confirmation itself is recorded in the Stage 5.3.5 ledger.",
                "Page calibration still requires verification.",
                "The wall base is page-local z = 0; floor level is not established.",
                "Page-local coordinates do not establish shared building coordinates.",
                "This mesh is not a native Revit element.",
            ]
            elements.append(mesh)
        except (ValueError, TypeError, KeyError) as exc:
            failed.append({"element_id": element_id, "reason": str(exc)})

    scale = float(calibration["millimetres_per_pdf_point"])
    page_size = _page_size(page.get("page_size_pdf_points"))

    # Columns (LOD 100) and legend-hatch walls (LOD 300): confirmed detections, full storey height.
    if confirmed_detected:
        detected = {
            c.get("column_id") or c.get("wall_id"): c
            for c in (column_loader(scale, page_size) if column_loader else [])
        }
        for element_id in sorted(confirmed_detected):
            noun = "column" if confirmed_detected[element_id] == "column" else "hatch wall"
            try:
                candidate = detected.get(element_id)
                if candidate is None:
                    raise ValueError(f"Confirmed {noun} is no longer detected on this page")
                height = ledger_service.resolve_column_height(ledger, element_id)
                if height is None:
                    raise ValueError(f"{noun.capitalize()} confirmation or plan placement is no longer active")
                builder = (columns_module.build_column_mesh if candidate.get("element_type") == "column_candidate"
                           else hatch_walls.build_hatch_wall_mesh)
                elements.append(builder(candidate, millimetres_per_pdf_point=scale, height_mm=height))
            except ValueError as exc:
                failed.append({"element_id": element_id, "reason": str(exc)})
    page_size_mm = (
        [round(page_size[0] * scale, 3), round(page_size[1] * scale, 3)]
        if page_size else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "artifact_type": "verified_wall_mesh_batch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "drawing_id": drawing_id,
        "drawing_title": drawing.get("filename"),
        "page_number": page_number,
        "source_pdf_sha256": source_sha,
        "coordinate_system": {
            "unit": "mm",
            "frame": "page_local",
            "origin_pdf": [0.0, 0.0],
            "x_axis": "right",
            "y_axis": "down",
            "z_axis": "up",
            "base_elevation": "page-local z = 0; level placement not established",
            "shared_building_coordinates_established": False,
        },
        "calibration": {
            "millimetres_per_pdf_point": scale,
            "method": calibration.get("method"),
            "source_reference": calibration.get("source_reference"),
            "verification_required": calibration.get("verification_required"),
        },
        "page": {
            "size_pdf_points": list(page_size) if page_size else None,
            "size_mm": page_size_mm,
        },
        "inputs": {
            "ledger_updated_at": ledger.get("updated_at"),
            "verification_ids": sorted({r["verification_id"] for _, r in confirmed}),
            "primitive_sidecar": sidecar_provenance,
        },
        "summary": {
            "confirmed_walls": len(confirmed),
            "confirmed_columns": len(confirmed_columns),
            "confirmed_hatch_walls": len(confirmed_hatch_walls),
            "meshes_built": len(elements),
            "failed": len(failed),
        },
        "elements": elements,
        "failed": failed,
        "limitations": [
            "Only reviewer-confirmed walls are built; no other element is inferred.",
            "Coordinates are page-local millimetres from a calibration that requires verification.",
            "Wall bases sit at page-local z = 0; floor levels are not yet associated.",
            "Columns and hatch walls are reviewer-confirmed detections at full storey height.",
            "No beams, slabs, doors or windows are generated at this stage.",
        ],
    }


def detected_elements(folder: Path, drawing_id: str, page_number: int, page_size, scale: float) -> list[dict[str, Any]]:
    """Column candidates plus legend-hatch wall candidates of one page."""
    columns = columns_module.page_columns(
        folder, drawing_id, page_number, page_size=page_size, millimetres_per_pdf_point=scale)
    walls, _ = hatch_walls.page_hatch_walls(
        folder, drawing_id, page_number, millimetres_per_pdf_point=scale, columns=columns)
    return columns + walls


def generate_page_model(project_folder: Path, project_id: str, drawing_id: str, page_number: int) -> tuple[dict[str, Any], Path]:
    """Build and save the Stage 5.4 artifact; returns (artifact, path)."""
    folder = Path(project_folder)
    artifacts = folder / "artifacts"

    recognition = read_recognition(folder)
    calibration = ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME)
    ledger = ledger_service.load_ledger(folder, project_id)

    def loader(wanted, source_sha):
        return load_sidecar_lines(artifacts, drawing_id, page_number, wanted, source_sha)

    artifact = build_verified_wall_model(
        project_id=project_id,
        recognition_report=recognition,
        calibration_report=calibration,
        ledger=ledger,
        drawing_id=drawing_id,
        page_number=page_number,
        source_line_loader=loader,
        column_loader=lambda scale, page_size: detected_elements(folder, drawing_id, page_number, page_size, scale),
    )

    add_openings(folder, artifact, drawing_id, page_number)

    path = artifacts / OUTPUT_DIRECTORY / output_artifact_name(drawing_id)
    # Fully derived from the ledger and source artifacts, so it is replaced on each run.
    ledger_service.save_json_atomic(path, artifact)
    return artifact, path


def add_openings(folder: Path, artifact: dict[str, Any], drawing_id: str, page_number: int) -> None:
    """Doors, windows and openings in the gaps of this page's built walls (see opening_detection)."""
    from app.services.sheet_registration import sheet_index

    scale = float((artifact.get("calibration") or {}).get("millimetres_per_pdf_point") or 0)
    sidecar = Path(folder) / "artifacts" / "primitive_sidecars" / f"{drawing_id}_page_{page_number}.jsonl"
    if not scale or not sidecar.is_file():
        return
    walls, columns = [], []
    for element in artifact.get("elements") or []:
        vertices = element["mesh"]["vertices"]
        xs = [v[0] for v in vertices]; ys = [v[1] for v in vertices]; zs = [v[2] for v in vertices]
        item = {"id": element["element_id"], "bbox": [min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale],
                "component": element.get("component"), "height_mm": max(zs) - min(zs)}
        if element.get("element_type") == "wall" and len(vertices) == 8:
            walls.append(item)
        elif element.get("element_type") == "column":
            columns.append(item)
    if not walls:
        return
    scan = hatch_walls.page_scan(sidecar, millimetres_per_pdf_point=scale)
    entry = sheet_index(folder).get(drawing_id)
    heights_by_id = {w["id"]: w["height_mm"] for w in walls + columns}
    counts = {"door": 0, "window": 0, "opening": 0, "assumed_heights": 0}
    for opening in openings_module.detect_openings(scan, walls, columns, millimetres_per_pdf_point=scale):
        heights = openings_module.opening_heights(opening, entry, millimetres_per_pdf_point=scale)
        wall_height = min(heights_by_id[i] for i in opening["between"])
        element_id = f"{drawing_id}:p{page_number}:{opening['kind']}:" + ",".join(f"{v:.2f}" for v in opening["bbox_pdf_points"])
        artifact["elements"].append(openings_module.build_opening_element(
            opening, heights, element_id=element_id, millimetres_per_pdf_point=scale, wall_height_mm=wall_height))
        counts[opening["kind"]] += 1
        counts["assumed_heights"] += heights["height_source"] == "assumed_typical"
    artifact["openings_summary"] = counts
