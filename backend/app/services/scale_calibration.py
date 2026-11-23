"""Stage 5.2: evidence-led drawing scale calibration and PDF-to-mm conversion.



Calibration is never guessed from a title-block scale string or dimension text.

A user/engineer supplies two PDF-page control points and the known real distance

between them. The resulting transform is stored separately from Stage 5.0/5.1.

"""

from __future__ import annotations



from datetime import datetime, timezone

from pathlib import Path

from typing import Any

import json

import math

import re

from decimal import Decimal



ARTIFACT_NAME = "drawing_scale_calibration_stage5_2.json"

METADATA_INDEX_NAME = "drawing_geometry_stage5_metadata_index.json"

UNIT_TO_MM = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0,

            "cm": 10.0, "m": 1000.0, "metre": 1000.0, "meter": 1000.0,

            "in": 25.4, "inch": 25.4, "inches": 25.4,

            "ft": 304.8, "feet": 304.8, "foot": 304.8}

DIM_RE = re.compile(

    r"(?<![A-Za-z0-9])(?P<num>(?:\d{1,3}(?:,\d{3})+|\d{1,7})(?:\.\d+)?)\s*"

    r"(?P<unit>mm|millimet(?:er|re)s?|cm|m|met(?:er|re)s?|inches|inch|in|ft|feet|foot)\b",

    re.I,

)

SCALE_RE = re.compile(r"\b(?:scale\s*[:=]?\s*)?(?P<a>1)\s*[:/]\s*(?P<b>\d{1,5})\b", re.I)





def _now() -> str:

    return datetime.now(timezone.utc).isoformat()





def _read_json(path: Path, default: Any) -> Any:

    if not path.is_file():

        return default

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except (OSError, json.JSONDecodeError):

        return default





def _json_safe(value):

    """Convert ijson Decimal values into JSON-compatible numeric values."""

    if isinstance(value, Decimal):

        return int(value) if value == value.to_integral_value() else float(value)

    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")





def _write_atomic(path: Path, data: dict) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=_json_safe), encoding="utf-8")

    temp.replace(path)





def _dimension_value(text: str):

    match = DIM_RE.search(text or "")

    if not match:

        return None

    unit = match.group("unit").lower()

    factor = UNIT_TO_MM.get(unit)

    if factor is None:

        return None

    try:

        value = float(match.group("num").replace(",", ""))

    except ValueError:

        return None

    return {"text": match.group(0), "value": value, "unit": unit,

            "value_mm": round(value * factor, 6)}





def _iter_geometry_drawings(project_folder: Path):

    """Yield one drawing at a time while skipping its large primitive arrays.



    The Stage 5.0 project artifact can be several GB. This parser builds only

    one drawing's non-primitive metadata/evidence at a time. Primitive arrays

    are traversed by ijson but never materialized in Python.

    """

    try:

        import ijson

        from ijson.common import ObjectBuilder

    except ImportError as exc:

        raise RuntimeError(

            "Streaming dependency missing. Install with: "

            "python -m pip install ijson"

        ) from exc



    path = project_folder / "artifacts" / "drawing_geometry_stage5.json"

    if not path.is_file():

        raise FileNotFoundError(

            "Stage 5.0 geometry artifact is missing. "

            "Expected artifacts/drawing_geometry_stage5.json."

        )



    saw_drawings_array = False

    builder = None

    skipping_primitives = False

    skip_depth = 0



    with path.open("rb") as source:

        for prefix, event, value in ijson.parse(source):

            if prefix == "drawings" and event == "start_array":

                saw_drawings_array = True

                continue



            if builder is None:

                if prefix == "drawings.item" and event == "start_map":

                    builder = ObjectBuilder()

                    builder.event(event, value)

                continue



            # Ignore the complete primitives array, including nested maps and

            # arrays. This is the multi-GB portion of the Stage 5.0 artifact.

            if skipping_primitives:

                if event in ("start_map", "start_array"):

                    skip_depth += 1

                elif event in ("end_map", "end_array"):

                    skip_depth -= 1

                    if skip_depth == 0:

                        skipping_primitives = False

                continue



            if (

                prefix == "drawings.item.pages.item.primitives"

                and event == "start_array"

            ):

                skipping_primitives = True

                skip_depth = 1

                continue



            builder.event(event, value)



            if prefix == "drawings.item" and event == "end_map":

                drawing = builder.value

                if not isinstance(drawing, dict):

                    raise ValueError("Invalid drawing record in Stage 5.0 artifact.")

                yield drawing

                builder = None



    if not saw_drawings_array:

        raise ValueError(

            "Stage 5.0 geometry artifact is invalid: missing drawings array."

        )

    if builder is not None or skipping_primitives:

        raise ValueError("Stage 5.0 geometry artifact ended inside a drawing record.")





def _metadata_index_path(project_folder: Path) -> Path:

    return project_folder / "artifacts" / METADATA_INDEX_NAME





def _load_geometry_metadata_index(project_folder: Path) -> dict:

    """Load a small cached metadata index, rebuilding it when Stage 5.0 changes."""

    source_path = project_folder / "artifacts" / "drawing_geometry_stage5.json"

    if not source_path.is_file():

        raise FileNotFoundError(

            "Stage 5.0 geometry artifact is missing. "

            "Expected artifacts/drawing_geometry_stage5.json."

        )



    stat = source_path.stat()

    index_path = _metadata_index_path(project_folder)

    cached = _read_json(index_path, None)

    if (

        isinstance(cached, dict)

        and cached.get("source_size_bytes") == stat.st_size

        and cached.get("source_mtime_ns") == stat.st_mtime_ns

        and isinstance(cached.get("drawings"), list)

    ):

        return cached



    drawings = list(_iter_geometry_drawings(project_folder))

    index = {

        "schema_version": "1.0.0",

        "source_artifact": "drawing_geometry_stage5.json",

        "source_size_bytes": stat.st_size,

        "source_mtime_ns": stat.st_mtime_ns,

        "generated_at": _now(),

        "drawings": drawings,

    }

    _write_atomic(index_path, index)

    return index





def _find_geometry_drawing(project_folder: Path, drawing_id: str) -> dict | None:

    """Find one drawing record through the compact metadata index."""

    index = _load_geometry_metadata_index(project_folder)

    for drawing in index.get("drawings", []):

        if str(drawing.get("drawing_id")) == str(drawing_id):

            return drawing

    return None





def _sha256_file(path: Path) -> str:

    digest = __import__("hashlib").sha256()

    with path.open("rb") as source:

        for chunk in iter(lambda: source.read(1024 * 1024), b""):

            digest.update(chunk)

    return digest.hexdigest()





def _current_pdf_sha256(project_folder: Path, drawing: dict) -> str:

    """Hash the currently stored source PDF and compare it with Stage 5.0."""

    source = drawing.get("source") or {}

    stored_name = source.get("stored_file_name")

    expected_hash = source.get("sha256") or drawing.get("source_sha256")

    if not stored_name or not expected_hash:

        raise ValueError(

            "Cannot validate calibration: Stage 5.0 source filename or SHA256 is missing."

        )



    # The extractor stores project PDFs in the project's drawings directory.

    filename = Path(str(stored_name)).name

    if filename != str(stored_name) or filename in ("", ".", ".."):

        raise ValueError("Invalid stored source PDF filename in Stage 5.0 metadata.")



    pdf_path = project_folder / "drawings" / filename

    if not pdf_path.is_file():

        raise ValueError(

            f"Cannot validate calibration: source PDF is missing: drawings/{filename}"

        )



    actual_hash = _sha256_file(pdf_path)

    if actual_hash.lower() != str(expected_hash).lower():

        raise ValueError(

            "The source PDF has changed since Stage 5.0 extraction. "

            "Re-extract this drawing before calibrating or transforming it."

        )

    return actual_hash





def _page_evidence(page: dict) -> dict:

    dimensions = []

    for candidate in page.get("dimension_candidates", []):

        span = candidate.get("source_span") or {}

        parsed = _dimension_value(candidate.get("value_text", ""))

        if parsed:

            dimensions.append({

                **parsed,

                "page_number": page.get("page_number"),

                "bbox_pdf_points": span.get("bbox"),

                "source": "stage5_0_dimension_candidate",

                "confidence": candidate.get("confidence", 0.55),

                "verification_required": True,

            })

    scales = []

    for span in page.get("text_spans", []):

        text = span.get("text", "")

        for match in SCALE_RE.finditer(text):

            scales.append({

                "text": match.group(0),

                "ratio": f"1:{match.group('b')}",

                "denominator": int(match.group("b")),

                "bbox_pdf_points": span.get("bbox"),

                "source": "native_pdf_text",

                "verification_required": True,

                "note": "Printed scale is evidence only; it is not applied automatically.",

            })

    return {"dimension_evidence": dimensions, "printed_scale_evidence": scales}





def build_calibration_preview(project_folder: str | Path, project_id: str) -> dict:

    folder = Path(project_folder)

    geometry_index = _load_geometry_metadata_index(folder)

    results = []

    for drawing in geometry_index.get("drawings", []):

        pages = []

        for page in drawing.get("pages", []):

            evidence = _page_evidence(page)

            pages.append({

                "page_number": page.get("page_number"),

                "page_size_pdf_points": page.get("page_size_pdf_points"),

                "coordinate_frame": page.get("coordinate_frame"),

                "vector_primitive_count": page.get("vector_primitive_count", 0),

                "text_span_count": page.get("text_span_count", 0),

                **evidence,

                "calibration_status": "unresolved",

            })

        results.append({

            "drawing_id": drawing.get("drawing_id"),

            "filename": drawing.get("filename"),

            "source_sha256": drawing.get("source_sha256") or drawing.get("source", {}).get("sha256"),

            "pages": pages,

        })

    existing = _read_json(folder / "artifacts" / ARTIFACT_NAME, {})

    calibration_map = existing.get("calibrations", {}) if isinstance(existing, dict) else {}

    for drawing in results:

        saved = calibration_map.get(str(drawing.get("drawing_id")), {})

        for page in drawing["pages"]:

            key = str(page["page_number"])

            if key in saved:

                page["calibration_status"] = saved[key].get("status", "calibrated")

                page["calibration"] = saved[key]

    summary = {

        "geometry_drawings_available": len(results),

        "page_count": sum(len(item["pages"]) for item in results),

        "dimension_evidence_count": sum(len(p["dimension_evidence"]) for d in results for p in d["pages"]),

        "printed_scale_evidence_count": sum(len(p["printed_scale_evidence"]) for d in results for p in d["pages"]),

        "calibrated_page_count": sum(p["calibration_status"] == "calibrated" for d in results for p in d["pages"]),

        "unresolved_page_count": sum(p["calibration_status"] != "calibrated" for d in results for p in d["pages"]),

    }

    return {

        "schema_version": "0.1.0",

        "stage": "5.2.1",

        "project_id": project_id,

        "generated_at": _now(),

        "status": "ready_for_control_point_calibration" if results else "no_geometry",

        "summary": summary,

        "drawings": results,

        "method": "Evidence discovery only. No scale is inferred or applied without explicit control points and a known real-world distance.",

        "coordinate_convention": {

            "source_unit": "PDF point (1/72 inch)",

            "source_origin": "top_left",

            "source_x_axis": "right",

            "source_y_axis": "down",

            "target_unit": "millimetre",

            "target_origin": "top_left",

            "target_x_axis": "right",

            "target_y_axis": "down",

        },

        "limitations": [

            "Dimension strings and printed scale labels are candidates, not proof of drawing scale.",

            "Scanned/image-only drawings may have no native text or vector geometry evidence.",

            "A calibration is valid only for the specified drawing page and source PDF version.",

            "One page calibration must not be assumed to apply to other pages or separately exported sheets.",

        ],

    }





def calibrate_page(

    project_folder: str | Path, project_id: str, *, drawing_id: str, page_number: int,

    point_a_pdf: list[float], point_b_pdf: list[float], known_distance: float,

    unit: str, source_reference: str,

) -> dict:

    folder = Path(project_folder)

    drawing = _find_geometry_drawing(folder, drawing_id)

    if not drawing:

        raise ValueError("drawing_id was not found in the Stage 5.0 geometry artifact.")

    current_source_sha256 = _current_pdf_sha256(folder, drawing)

    pages = {int(p.get("page_number")): p for p in drawing.get("pages", [])}

    page = pages.get(int(page_number))

    if not page:

        raise ValueError("page_number was not found in the selected drawing.")

    if len(point_a_pdf) != 2 or len(point_b_pdf) != 2:

        raise ValueError("Each control point must contain exactly [x, y].")

    coords = [*point_a_pdf, *point_b_pdf]

    if not all(math.isfinite(float(v)) for v in coords):

        raise ValueError("Control-point coordinates must be finite numbers.")

    width = float(page["page_size_pdf_points"]["width"])

    height = float(page["page_size_pdf_points"]["height"])

    for x, y in (point_a_pdf, point_b_pdf):

        if not (0 <= float(x) <= width and 0 <= float(y) <= height):

            raise ValueError("Control point lies outside the selected PDF page.")

    distance_pdf = math.hypot(

        float(point_b_pdf[0]) - float(point_a_pdf[0]),

        float(point_b_pdf[1]) - float(point_a_pdf[1]),

    )

    if distance_pdf <= 1e-6:

        raise ValueError("Control points must be distinct.")

    if not math.isfinite(float(known_distance)) or float(known_distance) <= 0:

        raise ValueError("known_distance must be a positive number.")

    normalized_unit = str(unit).strip().lower()

    if normalized_unit not in UNIT_TO_MM:

        raise ValueError(f"Unsupported unit. Choose one of: {', '.join(sorted(UNIT_TO_MM))}")

    reference = str(source_reference).strip()

    if len(reference) < 3:

        raise ValueError("source_reference is required (for example, verified dimension label or engineer instruction).")

    known_mm = float(known_distance) * UNIT_TO_MM[normalized_unit]

    mm_per_point = known_mm / distance_pdf

    points_per_metre = 1000.0 / mm_per_point

    record = {

        "status": "calibrated",

        "method": "two_user_supplied_control_points_and_known_distance",

        "drawing_id": str(drawing_id),

        "filename": drawing.get("filename"),

        "page_number": int(page_number),

        "source_sha256": current_source_sha256,

        "control_points_pdf": {"a": [float(point_a_pdf[0]), float(point_a_pdf[1])],

                            "b": [float(point_b_pdf[0]), float(point_b_pdf[1])]},

        "distance_pdf_points": round(distance_pdf, 8),

        "known_distance": float(known_distance),

        "known_distance_unit": normalized_unit,

        "known_distance_mm": round(known_mm, 6),

        "millimetres_per_pdf_point": round(mm_per_point, 12),

        "pdf_points_per_metre": round(points_per_metre, 8),

        "source_reference": reference,

        "verification_required": True,

        "calibrated_at": _now(),

    }

    target = folder / "artifacts" / ARTIFACT_NAME

    report = _read_json(target, {})

    if not isinstance(report, dict) or not isinstance(report.get("calibrations"), dict):

        report = {"schema_version": "0.1.0", "stage": "5.2.2", "project_id": project_id,

                "created_at": _now(), "calibrations": {}}

    report.setdefault("calibrations", {}).setdefault(str(drawing_id), {})[str(page_number)] = record

    report["stage"] = "5.2.2"

    report["updated_at"] = _now()

    report["method_note"] = "Only explicitly supplied control points and known dimensions are used; no inferred scale is silently accepted."

    _write_atomic(target, report)

    return {"project_id": project_id, "calibration": record, "artifact_file": str(target)}





def get_calibration_report(project_folder: str | Path) -> dict:

    path = Path(project_folder) / "artifacts" / ARTIFACT_NAME

    report = _read_json(path, None)

    if report is None:

        raise FileNotFoundError(f"No Stage 5.2 calibration exists yet: {ARTIFACT_NAME}")

    return report





def _get_page_calibration(project_folder: str | Path, drawing_id: str, page_number: int) -> dict:

    """Return a saved page calibration or raise a clear validation error."""

    try:
        report = get_calibration_report(project_folder)
    except FileNotFoundError as exc:
        raise ValueError(
            "No Stage 5.2 calibration report exists. Calibrate the drawing page first."
        ) from exc

    record = (report.get("calibrations", {})

                    .get(str(drawing_id), {})

                    .get(str(page_number)))

    if not isinstance(record, dict) or record.get("status") != "calibrated":

        raise ValueError(

            f"No valid calibration exists for drawing {drawing_id}, page {page_number}."

        )

    scale = record.get("millimetres_per_pdf_point")

    if not isinstance(scale, (int, float)) or not math.isfinite(float(scale)) or float(scale) <= 0:

        raise ValueError("Saved calibration has an invalid millimetres_per_pdf_point value.")



    drawing = _find_geometry_drawing(Path(project_folder), drawing_id)

    if not drawing:

        raise ValueError(

            f"Drawing {drawing_id} is missing from the current Stage 5.0 geometry artifact."

        )

    current_hash = _current_pdf_sha256(Path(project_folder), drawing)

    saved_hash = record.get("source_sha256")

    if not saved_hash or str(saved_hash).lower() != current_hash.lower():

        raise ValueError(

            "Saved calibration belongs to a different source PDF version. "

            "Recalibrate this drawing page before transforming geometry."

        )

    return record





def pdf_point_to_mm(point: list[float] | tuple[float, float],

                    millimetres_per_pdf_point: float,

                    origin_pdf: list[float] | tuple[float, float] = (0.0, 0.0)) -> list[float]:

    """Convert a PDF point to page-local millimetres (top-left origin, +X right, +Y down).



    The page origin is explicitly [0, 0]. This creates page-local coordinates;

    it does not establish a shared building/site coordinate system.

    """

    if len(point) != 2 or len(origin_pdf) != 2:

        raise ValueError("PDF point and origin must each contain exactly [x, y].")

    values = [float(point[0]), float(point[1]),

            float(origin_pdf[0]), float(origin_pdf[1]),

            float(millimetres_per_pdf_point)]

    if not all(math.isfinite(v) for v in values) or values[4] <= 0:

        raise ValueError("Coordinate values must be finite and scale must be positive.")

    scale = values[4]

    return [round((values[0] - values[2]) * scale, 6),

            round((values[1] - values[3]) * scale, 6)]





def transform_primitive_for_page(project_folder: str | Path, *, drawing_id: str,

                                page_number: int, primitive: dict,

                                _validated_calibration: dict | None = None) -> dict:

    """Transform one Stage 5.0 line/bezier/quad primitive without changing source data."""

    if not isinstance(primitive, dict):

        raise ValueError("primitive must be a JSON object.")

    if int(primitive.get("page_number", page_number)) != int(page_number):

        raise ValueError("Primitive page_number does not match the requested calibrated page.")

    # Reuse calibration already validated by the batch processor.
    # Standalone calls continue to validate against the original PDF.
    record = (
        _validated_calibration
        if _validated_calibration is not None
        else _get_page_calibration(project_folder, drawing_id, page_number)
    )

    if not isinstance(record, dict):
        raise ValueError("Calibration record must be an object.")

    try:
        record_page = int(record.get("page_number", 0))
    except (TypeError, ValueError):
        raise ValueError("Calibration page number is invalid.") from None

    if (
        record.get("status") != "calibrated"
        or str(record.get("drawing_id")) != str(drawing_id)
        or record_page != int(page_number)
    ):
        raise ValueError("Calibration does not match the requested drawing page.")

    scale_check = float(record.get("millimetres_per_pdf_point", 0))
    if not math.isfinite(scale_check) or scale_check <= 0:
        raise ValueError("Calibration scale must be finite and positive.")


    scale = float(record["millimetres_per_pdf_point"])

    kind = str(primitive.get("type", "")).lower()

    if kind == "line":

        if not isinstance(primitive.get("start"), (list, tuple)) or not isinstance(primitive.get("end"), (list, tuple)):

            raise ValueError("Line primitive must contain start and end coordinate pairs.")

        transformed = {

            "start_mm": pdf_point_to_mm(primitive["start"], scale),

            "end_mm": pdf_point_to_mm(primitive["end"], scale),

        }

    elif kind in {"bezier", "quad"}:

        points = primitive.get("points")

        expected = 4

        if not isinstance(points, list) or len(points) != expected:

            raise ValueError(f"{kind} primitive must contain exactly four points.")

        transformed = {"points_mm": [pdf_point_to_mm(p, scale) for p in points]}

    else:

        raise ValueError(f"Unsupported primitive type '{kind}'. Supported types: line, bezier, quad.")

    return {

        "drawing_id": str(drawing_id),

        "page_number": int(page_number),

        "primitive_type": kind,

        "source_pdf_coordinates": (

            {"start": primitive["start"], "end": primitive["end"]}

            if kind == "line" else {"points": primitive["points"]}

        ),

        "coordinates_mm": transformed,

        "transform": {

            "method": "uniform_scale_from_explicit_two_point_calibration",

            "millimetres_per_pdf_point": scale,

            "origin_pdf": [0.0, 0.0],

            "coordinate_frame": "page_local_top_left_origin_x_right_y_down",

            "target_unit": "mm",

        },

        "calibration_verification_required": bool(record.get("verification_required", True)),

        "calibration_source_reference": record.get("source_reference"),

    }








def _read_indexed_primitive_sidecar(
    project_folder: Path,
    *,
    drawing_id: str,
    page_number: int,
    start_index: int,
    batch_size: int,
    calibration: dict,
) -> tuple[int, list[dict]] | None:
    """Read a validated, indexed JSONL primitive sidecar."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(drawing_id))
    stem = f"{safe_id}_page_{int(page_number)}"
    folder = project_folder / "artifacts" / "primitive_sidecars"
    manifest_path = folder / f"{stem}_manifest.json"

    if not manifest_path.is_file():
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("drawing_id") != str(drawing_id):
        raise ValueError("Primitive sidecar drawing ID mismatch.")

    if int(manifest.get("page_number", -1)) != int(page_number):
        raise ValueError("Primitive sidecar page mismatch.")

    expected_hash = str(calibration.get("source_sha256") or "").lower()
    sidecar_source_hash = str(
        manifest.get("source_pdf_sha256") or ""
    ).lower()

    if not expected_hash or expected_hash != sidecar_source_hash:
        raise ValueError("Primitive sidecar PDF hash mismatch.")

    total = manifest.get("primitive_count")
    if not isinstance(total, int) or total < 0:
        raise ValueError("Invalid sidecar primitive count.")

    if start_index > total:
        raise ValueError(
            f"start_index {start_index} exceeds primitive count {total}."
        )

    sidecar_name = manifest.get("sidecar_file")
    index_name = manifest.get("index_file")

    if (
        not isinstance(sidecar_name, str)
        or Path(sidecar_name).name != sidecar_name
        or not isinstance(index_name, str)
        or Path(index_name).name != index_name
    ):
        raise ValueError("Invalid sidecar/index filename.")

    sidecar_path = folder / sidecar_name
    index_path = folder / index_name

    if not sidecar_path.is_file() or not index_path.is_file():
        raise FileNotFoundError("Sidecar or offset index is missing.")

    if (
        not isinstance(manifest.get("sidecar_size_bytes"), int)
        or sidecar_path.stat().st_size != manifest["sidecar_size_bytes"]
    ):
        raise ValueError("Sidecar file size mismatch.")

    index_data = json.loads(index_path.read_text(encoding="utf-8"))

    stride = index_data.get("stride")
    offsets = index_data.get("offsets")

    if (
        index_data.get("drawing_id") != str(drawing_id)
        or int(index_data.get("page_number", -1)) != int(page_number)
        or not isinstance(stride, int)
        or stride <= 0
        or not isinstance(offsets, list)
        or index_data.get("primitive_count") != total
        or index_data.get("sidecar_sha256") != manifest.get("sidecar_sha256")
    ):
        raise ValueError("Offset index does not match sidecar manifest.")

    expected_entries = (total + stride - 1) // stride
    if len(offsets) != expected_entries:
        raise ValueError("Offset index entry count mismatch.")

    if start_index == total:
        return total, []

    block_number = start_index // stride
    block_start = block_number * stride

    if block_number >= len(offsets):
        raise ValueError("Requested offset is missing.")

    offset = offsets[block_number]
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid primitive byte offset.")

    selected = []

    with sidecar_path.open("rb") as stream:
        stream.seek(offset)

        for _ in range(start_index - block_start):
            if not stream.readline():
                raise ValueError("Unexpected end of sidecar.")

        requested = min(batch_size, total - start_index)

        for _ in range(requested):
            line = stream.readline()
            if not line:
                raise ValueError("Sidecar ended before expected primitive count.")

            try:
                primitive = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid primitive JSONL record.") from exc

            if (
                not isinstance(primitive, dict)
                or int(primitive.get("page_number", -1)) != int(page_number)
            ):
                raise ValueError("Invalid primitive or page number.")

            selected.append(primitive)

    if len(selected) != min(batch_size, total - start_index):
        raise ValueError("Incomplete primitive batch returned.")

    return total, selected


def transform_primitive_batch(

    project_folder: str | Path, *, drawing_id: str, page_number: int,

    start_index: int = 0, batch_size: int = 1000,

) -> dict:

    """Transform a bounded primitive batch using event-based JSON parsing.



    Parses the project report incrementally and builds only the requested

    primitive records. It does not materialize a complete drawing or page.

    """

    try:

        import ijson

        from ijson.common import ObjectBuilder

    except ImportError as exc:

        raise RuntimeError(

            "Streaming dependency missing. Install with: "

            "python -m pip install ijson"

        ) from exc



    if start_index < 0:

        raise ValueError("start_index must be zero or greater.")

    if not 1 <= batch_size <= 5000:

        raise ValueError("batch_size must be between 1 and 5000.")



    folder = Path(project_folder)

    source_path = folder / "artifacts" / "drawing_geometry_stage5.json"

    if not source_path.is_file():

        raise FileNotFoundError("Stage 5.0 geometry artifact is missing.")



    calibration = _get_page_calibration(folder, drawing_id, page_number)



    sidecar_result = _read_indexed_primitive_sidecar(
        folder,
        drawing_id=drawing_id,
        page_number=page_number,
        start_index=start_index,
        batch_size=batch_size,
        calibration=calibration,
    )

    if sidecar_result is not None:
        total, selected = sidecar_result
    else:
        total = 0

        selected = []

        drawing_found = False

        page_found = False

        current_drawing_id = None

        current_page_number = None



        with source_path.open("rb") as source:

            events = iter(ijson.parse(source))



            while True:

                try:

                    prefix, event, value = next(events)

                except StopIteration:

                    break



                if prefix == "drawings.item.drawing_id" and event in ("string", "number"):

                    current_drawing_id = str(value)

                    if current_drawing_id == str(drawing_id):

                        drawing_found = True



                elif (

                    prefix == "drawings.item.pages.item.page_number"

                    and event in ("number", "string")

                ):

                    current_page_number = int(value)

                    if (

                        current_drawing_id == str(drawing_id)

                        and current_page_number == int(page_number)

                    ):

                        page_found = True



                elif (

                    prefix == "drawings.item.pages.item"

                    and event == "end_map"

                ):

                    current_page_number = None



                elif (

                    prefix == "drawings.item.pages.item.primitives.item"

                    and event == "start_map"

                    and current_drawing_id == str(drawing_id)

                    and current_page_number == int(page_number)

                ):

                    builder = ObjectBuilder()

                    builder.event(event, value)

                    depth = 1



                    while depth:

                        try:

                            _, child_event, child_value = next(events)

                        except StopIteration as exc:

                            raise ValueError(

                                "Unexpected end of JSON while reading a primitive."

                            ) from exc



                        builder.event(child_event, child_value)



                        if child_event in ("start_map", "start_array"):

                            depth += 1

                        elif child_event in ("end_map", "end_array"):

                            depth -= 1



                    primitive = builder.value

                    if not isinstance(primitive, dict):

                        raise ValueError(

                            "Invalid primitive record in Stage 5.0 artifact."

                        )



                    if start_index <= total < start_index + batch_size:

                        selected.append(primitive)



                    total += 1



        if not drawing_found:

            raise ValueError(

                f"Drawing {drawing_id} was not found in Stage 5.0 geometry."

            )

        if not page_found:

            raise ValueError(

                f"Page {page_number} was not found in drawing {drawing_id}."

            )

        if start_index > total:

            raise ValueError(

                f"start_index {start_index} exceeds primitive count {total}."

            )



    transformed = []

    failures = []



    for local_index, primitive in enumerate(selected):

        absolute_index = start_index + local_index

        try:

            item = transform_primitive_for_page(

                folder,

                drawing_id=drawing_id,

                page_number=page_number,

                primitive=primitive,

                _validated_calibration=calibration,

            )

            item["primitive_index"] = absolute_index

            transformed.append(item)

        except (TypeError, ValueError, KeyError) as exc:

            failures.append({

                "primitive_index": absolute_index,

                "error": str(exc),

            })



    artifacts = folder / "artifacts" / "coordinate_transform_stage5_2_4"

    artifacts.mkdir(parents=True, exist_ok=True)



    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(drawing_id))

    end_index = start_index + len(selected)

    chunk_name = (

        f"{safe_id}_page_{int(page_number)}_{start_index}_{end_index}.jsonl"

    )

    chunk_path = artifacts / chunk_name

    temp_path = chunk_path.with_suffix(chunk_path.suffix + ".tmp")



    with temp_path.open("w", encoding="utf-8", newline="\n") as out:

        for item in transformed:

            out.write(

                json.dumps(

                    item,

                    ensure_ascii=False,

                    separators=(",", ":"),

                    default=_json_safe,

                ) + "\n"

            )

    temp_path.replace(chunk_path)



    next_index = end_index

    complete = next_index >= total



    checkpoint = {

        "schema_version": "0.1.0",

        "stage": "5.2.4",

        "drawing_id": str(drawing_id),

        "page_number": int(page_number),

        "source_artifact": "drawing_geometry_stage5.json",

        "source_size_bytes": source_path.stat().st_size,

        "source_mtime_ns": source_path.stat().st_mtime_ns,

        "calibration_at": calibration.get("calibrated_at"),

        "total_primitives": total,

        "next_start_index": next_index,

        "complete": complete,

        "updated_at": _now(),

    }



    checkpoint_path = (

        artifacts / f"{safe_id}_page_{int(page_number)}_checkpoint.json"

    )

    _write_atomic(checkpoint_path, checkpoint)



    return {

        "stage": "5.2.4",

        "drawing_id": str(drawing_id),

        "page_number": int(page_number),

        "start_index": start_index,

        "next_start_index": next_index,

        "batch_size_requested": batch_size,

        "primitives_in_batch": len(selected),

        "transformed_count": len(transformed),

        "failed_count": len(failures),

        "failures": failures[:50],

        "total_primitives": total,

        "complete": complete,

        "output_jsonl": str(chunk_path),

        "checkpoint_file": str(checkpoint_path),

        "calibration_verification_required": bool(

            calibration.get("verification_required", True)

        ),

        "note": (

            "Page-local millimetres only; "

            "not a shared building coordinate system."

        ),

    }
