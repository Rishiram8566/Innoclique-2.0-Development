
import json
from pathlib import Path

from app.services.project_report_stream_reader import (
    iter_project_drawing_metadata,
    get_project_drawing_metadata,
)

test_dir = Path("data/validation_stage5")
test_dir.mkdir(parents=True, exist_ok=True)

report_path = test_dir / "project_report_reader_test.json"

report = {
    "schema_version": "0.2.0",
    "stage": "5.0",
    "status": "completed",
    "drawings": [
        {
            "drawing_id": "DRAWING-001",
            "filename": "floor1.pdf",
            "source_sha256": "abc123",
            "summary": {
                "page_count": 1,
                "vector_primitive_count": 3,
                "text_span_count": 2
            },
            "pages": [
                {
                    "page_number": 1,
                    "primitives": [
                        {"type": "line", "x": 10},
                        {"type": "line", "x": 20},
                        {"type": "line", "x": 30}
                    ]
                }
            ]
        },
        {
            "drawing_id": "DRAWING-002",
            "filename": "floor2.pdf",
            "source_sha256": "def456",
            "summary": {
                "page_count": 2,
                "vector_primitive_count": 5,
                "text_span_count": 4
            },
            "pages": [
                {
                    "page_number": 1,
                    "primitives": [{"type": "line", "x": 40}]
                },
                {
                    "page_number": 2,
                    "primitives": [{"type": "line", "x": 50}]
                }
            ]
        }
    ],
    "failed": []
}

with report_path.open("w", encoding="utf-8") as stream:
    json.dump(report, stream)

items = list(iter_project_drawing_metadata(report_path))
index = get_project_drawing_metadata(report_path)

print("Drawing records:", len(items))
print("Drawing IDs:", [x["drawing_id"] for x in items])
print("First hash:", items[0]["source_sha256"])
print("First summary:", items[0]["summary"])
print("Second summary:", items[1]["summary"])
print("Index count:", len(index))
print("Geometry loaded into metadata:", any("pages" in x for x in items))

passed = (
    len(items) == 2
    and items[0]["drawing_id"] == "DRAWING-001"
    and items[1]["drawing_id"] == "DRAWING-002"
    and items[0]["summary"]["vector_primitive_count"] == 3
    and items[1]["summary"]["vector_primitive_count"] == 5
    and len(index) == 2
    and all("pages" not in x for x in items)
)

print("PROJECT REPORT METADATA TEST:", "PASS" if passed else "FAIL")
