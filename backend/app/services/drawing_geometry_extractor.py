

"""Stage 5.0: source-grounded 2D geometry extraction from engineering PDFs.

Extracts all supported vector primitives and positioned text.

Geometry is processed in configurable batches while preserving the existing

Stage 5.0 JSON schema and original PDF provenance.

"""

from __future__ import annotations

from pathlib import Path

from typing import Any, Iterator

import hashlib

import math

import re

try:

    import fitz  # PyMuPDF

except ImportError:  # pragma: no cover

    fitz = None

SCHEMA_VERSION = "0.1.0"

EXTRACTION_BATCH_SIZE = 10000

DIMENSION_RE = re.compile(

    r"(?<![A-Za-z0-9.])(?:\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.\d{1,3})?\s*(?:mm|cm|m|ft|feet|in|inch|')\b",

    re.IGNORECASE,

)

LEVEL_RE = re.compile(

    r"\b(?:FFL|SSL|TOS|TOC|LEVEL|EL\.?|RL)\s*[:=]?\s*[+\-]?\d+(?:\.\d+)?\s*(?:m|mm)?\b",

    re.I,

)

def _sha256_file(path: Path) -> str:

    """Hash a file incrementally without loading the entire PDF into memory."""

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for chunk in iter(lambda: stream.read(1024 * 1024), b""):

            digest.update(chunk)

    return digest.hexdigest()

def _point(p: Any) -> list[float]:

    return [round(float(p.x), 3), round(float(p.y), 3)]

def _rect(r: Any) -> list[float]:

    return [

        round(float(r.x0), 3),

        round(float(r.y0), 3),

        round(float(r.x1), 3),

        round(float(r.y1), 3),

    ]

def _safe_color(value: Any):

    if value is None:

        return None

    try:

        return [round(float(v), 4) for v in value]

    except Exception:

        return None

def _extract_path_item(

    item: tuple,

    page_number: int,

    path_index: int,

    item_index: int,

) -> dict | None:

    if not item:

        return None

    kind = item[0]

    base = {

        "page_number": page_number,

        "path_index": path_index,

        "item_index": item_index,

        "source": "pdf_vector_path",

    }

    try:

        if kind == "l" and len(item) >= 3:

            return {

                **base,

                "type": "line",

                "start": _point(item[1]),

                "end": _point(item[2]),

            }

        if kind == "re" and len(item) >= 2:

            return {

                **base,

                "type": "rectangle",

                "bbox": _rect(item[1]),

            }

        if kind == "qu" and len(item) >= 2:

            q = item[1]

            return {

                **base,

                "type": "quad",

                "points": [

                    _point(q.ul),

                    _point(q.ur),

                    _point(q.lr),

                    _point(q.ll),

                ],

            }

        if kind == "c" and len(item) >= 5:

            return {

                **base,

                "type": "bezier",

                "points": [_point(p) for p in item[1:5]],

            }

    except Exception:

        return None

    return None

def _iter_primitive_batches(

    paths: list,

    page_number: int,

    batch_size: int,

) -> Iterator[list[dict]]:

    """Yield supported primitives in bounded processing batches.

    Original path_index and item_index values are retained.

    No total primitive limit is applied.

    """

    batch: list[dict] = []

    for path_index, drawing_path in enumerate(paths):

        for item_index, item in enumerate(drawing_path.get("items", [])):

            primitive = _extract_path_item(

                item,

                page_number,

                path_index,

                item_index,

            )

            if primitive is None:

                continue

            primitive.update({

                "stroke": _safe_color(drawing_path.get("color")),

                "fill": _safe_color(drawing_path.get("fill")),

                "stroke_width_pt": round(

                    float(drawing_path.get("width") or 0), 4

                ),

                "closed": bool(drawing_path.get("closePath", False)),

            })

            batch.append(primitive)

            if len(batch) >= batch_size:

                yield batch

                batch = []

    if batch:

        yield batch

def _extract_text(page, page_number: int) -> list[dict]:

    """Extract all non-empty native text spans from the page."""

    result = []

    raw = page.get_text("dict")

    for block_index, block in enumerate(raw.get("blocks", [])):

        if block.get("type") != 0:

            continue

        for line_index, line in enumerate(block.get("lines", [])):

            for span_index, span in enumerate(line.get("spans", [])):

                value = (span.get("text") or "").strip()

                if not value:

                    continue

                bbox = span.get("bbox")

                result.append({

                    "page_number": page_number,

                    "block_index": block_index,

                    "line_index": line_index,

                    "span_index": span_index,

                    "text": value,

                    "bbox": (

                        [round(float(x), 3) for x in bbox]

                        if bbox else None

                    ),

                    "font": span.get("font"),

                    "font_size_pt": round(

                        float(span.get("size", 0)), 3

                    ),

                    "flags": span.get("flags"),

                    "source": "pdf_text_span",

                })

    return result

def _text_candidates(spans: list[dict]) -> dict:

    dimensions, levels = [], []

    for span in spans:

        text = span["text"]

        for match in DIMENSION_RE.finditer(text):

            dimensions.append({

                "value_text": match.group(0),

                "source_span": span,

                "confidence": 0.55,

                "verification_required": True,

            })

        for match in LEVEL_RE.finditer(text):

            levels.append({

                "value_text": match.group(0),

                "source_span": span,

                "confidence": 0.60,

                "verification_required": True,

            })

    return {

        "dimension_candidates": dimensions,

        "level_candidates": levels,

    }

def extract_pdf_geometry(

    pdf_path: str | Path,

    *,

    drawing_id: str | None = None,

    filename: str | None = None,

    real_world_units_per_pdf_point: float | None = None,

    real_world_unit: str | None = None,

    batch_size: int = EXTRACTION_BATCH_SIZE,

) -> dict:

    """Extract all supported PDF geometry without a fixed total cap."""

    if fitz is None:

        raise RuntimeError(

            "PyMuPDF is required. Install it with: pip install pymupdf"

        )

    path = Path(pdf_path)

    if not path.is_file():

        raise FileNotFoundError(path)

    if not isinstance(batch_size, int) or batch_size < 1:

        raise ValueError("batch_size must be a positive integer.")

    if (

        real_world_units_per_pdf_point is not None

        and real_world_units_per_pdf_point <= 0

    ):

        raise ValueError(

            "real_world_units_per_pdf_point must be positive"

        )

    if (

        real_world_units_per_pdf_point is not None

        and not real_world_unit

    ):

        raise ValueError(

            "real_world_unit is required when an explicit scale factor is supplied"

        )

    digest = _sha256_file(path)

    document = fitz.open(path)

    pages = []

    total_paths = 0

    total_primitives = 0

    total_spans = 0

    total_primitive_batches = 0

    try:

        for page_index, page in enumerate(document):

            page_number = page_index + 1

            # PyMuPDF currently materializes this page's path collection.

            paths = page.get_drawings()

            primitives = []

            page_batch_count = 0

            # Process every supported primitive in bounded batches.

            for primitive_batch in _iter_primitive_batches(

                paths,

                page_number,

                batch_size,

            ):

                primitives.extend(primitive_batch)

                page_batch_count += 1

            spans = _extract_text(page, page_number)

            candidates = _text_candidates(spans)

            total_paths += len(paths)

            total_primitives += len(primitives)

            total_spans += len(spans)

            total_primitive_batches += page_batch_count

            pages.append({

                "page_number": page_number,

                "page_size_pdf_points": {

                    "width": round(page.rect.width, 3),

                    "height": round(page.rect.height, 3),

                },

                "coordinate_frame": (

                    "pdf_page_top_left_origin_x_right_y_down"

                ),

                "vector_path_count": len(paths),

                "vector_primitive_count": len(primitives),

                "primitive_batch_size": batch_size,

                "primitive_batch_count": page_batch_count,

                "primitive_extraction_complete": True,

                "text_span_count": len(spans),

                "primitives": primitives,

                "text_spans": spans,

                **candidates,

                "quality": {

                    "vector_data_present": bool(primitives),

                    "native_text_present": bool(spans),

                    "geometry_semantics_verified": False,

                },

            })

    finally:

        document.close()

    calibrated = real_world_units_per_pdf_point is not None

    return {

        "schema_version": SCHEMA_VERSION,

        "stage": "5.0",

        "drawing_id": drawing_id,

        "filename": filename or path.name,

        "source": {

            "stored_file_name": path.name,

            "sha256": digest,

            "page_count": len(pages),

        },

        "coordinate_system": {

            "unit": "pdf_point",

            "points_per_inch": 72,

            "origin": "top_left",

            "x_axis": "right",

            "y_axis": "down",

            "real_world_scale_status": (

                "explicitly_calibrated" if calibrated else "unresolved"

            ),

            "real_world_units_per_pdf_point": (

                real_world_units_per_pdf_point

            ),

            "real_world_unit": real_world_unit,

        },

        "summary": {

            "page_count": len(pages),

            "vector_path_count": total_paths,

            "vector_primitive_count": total_primitives,

            "text_span_count": total_spans,

            "primitive_batch_size": batch_size,

            "primitive_batch_count": total_primitive_batches,

            "dimension_candidate_count": sum(

                len(p["dimension_candidates"]) for p in pages

            ),

            "level_candidate_count": sum(

                len(p["level_candidates"]) for p in pages

            ),

        },

        "limitations": [

            "PDF vector paths are recorded as 2D primitives; they are not automatically classified as walls, columns, beams, doors, or slabs.",

            "Text dimensions and levels are candidates requiring visual verification and do not calibrate the drawing by themselves.",

            "Scanned raster content is not converted into vector geometry by this Stage 5.0 extractor.",

            "No 3D geometry, engineering approval, clash detection, or Revit elements are generated in this stage.",

            "Primitive processing uses bounded batches, but the complete page result is retained in memory for compatibility with the existing JSON pipeline.",

            "PyMuPDF page.get_drawings() materializes the page path collection before primitive batching.",

        ],

        "pages": pages,

    }
