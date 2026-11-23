
import json
from pathlib import Path

from app.services.nested_geometry_reader import (
    _read_page_metadata,
    iter_drawing_pages,
)

TEST_DIR = Path("data/validation_stage5/edge_cases")
TEST_DIR.mkdir(parents=True, exist_ok=True)


def run_test(name, filename, content, expected_success):
    path = TEST_DIR / filename
    path.write_text(content, encoding="utf-8")

    try:
        metadata = _read_page_metadata(path)
        pages = list(iter_drawing_pages(path))

        total = sum(
            sum(1 for _ in page["primitives"])
            for page in pages
        )

        success = True

        print(f"\n{name}")
        print("Pages:", len(pages))
        print("Primitives:", total)
        print("Metadata:", len(metadata))
        print("Result: PASS")

    except Exception as exc:
        success = False
        print(f"\n{name}")
        print("Exception:", type(exc).__name__, str(exc))
        print("Result:", "REJECTED" if not expected_success else "FAIL")

    if success != expected_success:
        print("EXPECTATION MISMATCH")


# 1. Empty primitive array.
run_test(
    "EMPTY PRIMITIVES",
    "empty_primitives.json",
    json.dumps({
        "pages": [{
            "page_number": 1,
            "primitives": [],
            "text_spans": [],
            "dimension_candidates": [],
            "level_candidates": [],
            "quality": {},
        }]
    }),
    True,
)

# 2. Missing optional metadata.
run_test(
    "MISSING OPTIONAL METADATA",
    "missing_metadata.json",
    json.dumps({
        "pages": [{
            "page_number": 1,
            "primitives": [
                {"page_number": 1, "type": "line"}
            ]
        }]
    }),
    True,
)

# 3. Empty page list.
run_test(
    "EMPTY PAGE LIST",
    "empty_pages.json",
    json.dumps({"pages": []}),
    True,
)

# 4. Malformed JSON.
run_test(
    "MALFORMED JSON",
    "malformed.json",
    '{"pages": [ invalid json ]',
    False,
)

print("\nEDGE-CASE TESTS FINISHED")
