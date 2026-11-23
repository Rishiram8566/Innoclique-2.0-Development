
import json
from pathlib import Path

from app.services.project_report_stream_reader import (
   iter_project_drawings_streamed,
)

source = Path(
    "data/validation_stage5/project_report_reader_test.json"
)

original = json.loads(source.read_text(encoding="utf-8"))

drawing = drawing = next(iter_project_drawings_streamed(source))
pages = list(drawing["pages"])

restored_count = sum(
    len(list(page["primitives"]))
    for page in pages
)

expected_count = sum(
    len(page["primitives"])
    for page in original["drawings"][0]["pages"]
)

print("Drawing ID:", drawing["drawing_id"])
print("Pages:", len(pages))
print("Expected primitives:", expected_count)
print("Streamed primitives:", restored_count)

passed = (
    drawing["drawing_id"] == "DRAWING-001"
    and len(pages) == 1
    and restored_count == expected_count
)

print(
    "PROJECT GEOMETRY STREAM TEST:",
    "PASS" if passed else "FAIL"
)
