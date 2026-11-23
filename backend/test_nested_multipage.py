
import json
from pathlib import Path

from app.services.nested_geometry_reader import (
    iter_drawing_pages,
    _read_page_metadata,
)

source = Path("data/validation_stage5/multipage_test.json")
source.parent.mkdir(parents=True, exist_ok=True)

# Create an isolated three-page test drawing.
drawing = {
    "drawing_id": "multipage_validation_test",
    "filename": "multipage_test.pdf",
    "pages": [
        {
            "page_number": 1,
            "page_size_pdf_points": [1000, 700],
            "vector_path_count": 2,
            "vector_primitive_count": 2,
            "text_span_count": 1,
            "primitives": [
                {"page_number": 1, "type": "line", "x1": 10, "y1": 20},
                {"page_number": 1, "type": "line", "x1": 30, "y1": 40},
            ],
            "text_spans": [{"text": "PAGE ONE"}],
            "dimension_candidates": [],
            "level_candidates": [],
            "quality": {"vector_data_present": True},
        },
        {
            "page_number": 2,
            "page_size_pdf_points": [1000, 700],
            "vector_path_count": 3,
            "vector_primitive_count": 3,
            "text_span_count": 1,
            "primitives": [
                {"page_number": 2, "type": "line", "x1": 50, "y1": 60},
                {"page_number": 2, "type": "line", "x1": 70, "y1": 80},
                {"page_number": 2, "type": "line", "x1": 90, "y1": 100},
            ],
            "text_spans": [{"text": "PAGE TWO"}],
            "dimension_candidates": [{"value_text": "250mm"}],
            "level_candidates": [],
            "quality": {"vector_data_present": True},
        },
        {
            "page_number": 3,
            "page_size_pdf_points": [1000, 700],
            "vector_path_count": 1,
            "vector_primitive_count": 1,
            "text_span_count": 1,
            "primitives": [
                {"page_number": 3, "type": "line", "x1": 110, "y1": 120},
            ],
            "text_spans": [{"text": "PAGE THREE"}],
            "dimension_candidates": [],
            "level_candidates": [{"value_text": "LEVEL 1"}],
            "quality": {"vector_data_present": True},
        },
    ],
}

source.write_text(json.dumps(drawing), encoding="utf-8")

# Verify metadata.
metadata_pages = _read_page_metadata(source)

# Verify streamed geometry.
streamed_pages = list(iter_drawing_pages(source))

print("\n========== MULTI-PAGE VALIDATION ==========")
print("Pages found:", len(streamed_pages))
print("Metadata pages:", len(metadata_pages))

expected_counts = {1: 2, 2: 3, 3: 1}
total = 0
all_pages_correct = True

for page in streamed_pages:
    page_number = page["page_number"]
    primitives = list(page["primitives"])
    actual_count = len(primitives)
    expected_count = expected_counts[page_number]

    total += actual_count

    correct = (
        actual_count == expected_count
        and all(p["page_number"] == page_number for p in primitives)
    )

    all_pages_correct = all_pages_correct and correct

    print(
        f"Page {page_number}: "
        f"{actual_count} primitives; "
        f"expected {expected_count}; "
        f"correct={correct}"
    )

print("Total primitives:", total)
print("Expected total:", sum(expected_counts.values()))
print("All pages correct:", all_pages_correct)

print("Text metadata preserved:", all(
    "text_spans" in page for page in metadata_pages
))
print("Quality metadata preserved:", all(
    "quality" in page for page in metadata_pages
))

print("MULTI-PAGE TEST:", "PASS" if (
    len(streamed_pages) == 3
    and len(metadata_pages) == 3
    and total == 6
    and all_pages_correct
) else "FAIL")

print("==========================================")
