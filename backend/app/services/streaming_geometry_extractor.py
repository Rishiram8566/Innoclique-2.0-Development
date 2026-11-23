
"""Stage 5.0: disk-backed PDF geometry extraction.

Keeps the existing Stage 5.0 drawing schema while streaming primitive
arrays to disk. The original drawing_geometry_extractor remains unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
import fitz

from .drawing_geometry_extractor import (
    SCHEMA_VERSION,
    EXTRACTION_BATCH_SIZE,
    _sha256_file,
    _iter_primitive_batches,
    _extract_text,
    _text_candidates,
)
from .geometry_stream_writer import write_json_atomic


def _count_primitives(paths, page_number: int, batch_size: int) -> int:
    return sum(
        len(batch)
        for batch in _iter_primitive_batches(paths, page_number, batch_size)
    )


def _primitive_stream(paths, page_number: int, batch_size: int):
    for batch in _iter_primitive_batches(paths, page_number, batch_size):
        yield from batch


def stream_pdf_geometry_to_json(
    pdf_path: str | Path,
    output_path: str | Path,
    *,
    drawing_id: str | None = None,
    filename: str | None = None,
    batch_size: int = EXTRACTION_BATCH_SIZE,
) -> dict:
    """Extract a PDF into a standalone Stage 5.0 JSON drawing artifact.

    Uses two passes:
    1. Count page geometry and text candidates.
    2. Reopen the PDF and stream page primitives into the output JSON.

    Only one page's PyMuPDF path collection is held at a time.
    """

    if fitz is None:
        raise RuntimeError("PyMuPDF is required. Install it with pip install pymupdf")

    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    source = Path(pdf_path)
    target = Path(output_path)

    if not source.is_file():
        raise FileNotFoundError(source)

    source_hash = _sha256_file(source)

    # Pass 1: collect metadata and counts, not primitive arrays.
    page_metadata = []
    total_paths = 0
    total_primitives = 0
    total_spans = 0
    total_batches = 0
    total_dimensions = 0
    total_levels = 0

    with fitz.open(source) as document:
        page_count = len(document)

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            paths = page.get_drawings()
            primitive_count = _count_primitives(
                paths, page_number, batch_size
            )
            spans = _extract_text(page, page_number)
            candidates = _text_candidates(spans)

            batch_count = (
                (primitive_count + batch_size - 1) // batch_size
                if primitive_count else 0
            )

            page_metadata.append({
                "page_number": page_number,
                "page_size_pdf_points": {
                    "width": round(page.rect.width, 3),
                    "height": round(page.rect.height, 3),
                },
                "vector_path_count": len(paths),
                "vector_primitive_count": primitive_count,
                "primitive_batch_size": batch_size,
                "primitive_batch_count": batch_count,
                "text_span_count": len(spans),
                "dimension_candidate_count": len(
                    candidates["dimension_candidates"]
                ),
                "level_candidate_count": len(
                    candidates["level_candidates"]
                ),
            })

            total_paths += len(paths)
            total_primitives += primitive_count
            total_spans += len(spans)
            total_batches += batch_count
            total_dimensions += len(candidates["dimension_candidates"])
            total_levels += len(candidates["level_candidates"])

            del paths, spans, candidates

    # Pass 2: stream page arrays into the JSON writer.
    def page_stream() -> Iterator[dict]:
        with fitz.open(source) as document:
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                metadata = page_metadata[page_index]
                paths = page.get_drawings()
                spans = _extract_text(page, page_number)
                candidates = _text_candidates(spans)

                yield {
                    **{
                        key: value
                        for key, value in metadata.items()
                        if key not in (
                            "dimension_candidate_count",
                            "level_candidate_count",
                        )
                    },
                    "coordinate_frame": (
                        "pdf_page_top_left_origin_x_right_y_down"
                    ),
                    "primitive_extraction_complete": True,
                    "primitives": _primitive_stream(
                        paths, page_number, batch_size
                    ),
                    "text_spans": spans,
                    **candidates,
                    "quality": {
                        "vector_data_present": bool(
                            metadata["vector_primitive_count"]
                        ),
                        "native_text_present": bool(spans),
                        "geometry_semantics_verified": False,
                    },
                }

    calibrated = False

    drawing = {
        "schema_version": SCHEMA_VERSION,
        "stage": "5.0",
        "drawing_id": drawing_id,
        "filename": filename or source.name,
        "source": {
            "stored_file_name": source.name,
            "sha256": source_hash,
            "page_count": len(page_metadata),
        },
        "coordinate_system": {
            "unit": "pdf_point",
            "points_per_inch": 72,
            "origin": "top_left",
            "x_axis": "right",
            "y_axis": "down",
            "real_world_scale_status": "unresolved",
            "real_world_units_per_pdf_point": None,
            "real_world_unit": None,
        },
        "summary": {
            "page_count": len(page_metadata),
            "vector_path_count": total_paths,
            "vector_primitive_count": total_primitives,
            "text_span_count": total_spans,
            "primitive_batch_size": batch_size,
            "primitive_batch_count": total_batches,
            "dimension_candidate_count": total_dimensions,
            "level_candidate_count": total_levels,
        },
        "limitations": [
            "PDF vector paths are recorded as 2D primitives; they are not automatically classified as walls, columns, beams, doors, or slabs.",
            "Text dimensions and levels are candidates requiring visual verification and do not calibrate the drawing by themselves.",
            "Scanned raster content is not converted into vector geometry by this Stage 5.0 extractor.",
            "No 3D geometry, engineering approval, clash detection, or Revit elements are generated in this stage.",
            "Primitive arrays are streamed to disk, but PyMuPDF materializes each page's path collection.",
            "Text spans and text candidates are retained for one page at a time.",
        ],
        "pages": page_stream(),
    }

    result = write_json_atomic(target, drawing)

    return {
        **result,
        "drawing_id": drawing_id,
        "source_sha256": source_hash,
        "page_count": len(page_metadata),
        "vector_path_count": total_paths,
        "vector_primitive_count": total_primitives,
        "text_span_count": total_spans,
        "primitive_batch_count": total_batches,
    }
