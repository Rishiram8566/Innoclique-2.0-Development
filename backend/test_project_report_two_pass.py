
import json
from pathlib import Path

from app.services.project_report_stream_reader import (
    iter_project_drawings_streamed,
)

source = Path(
    "data/validation_stage5/project_report_reader_test.json"
)

original = json.loads(source.read_text(encoding="utf-8"))

restored_drawings = []
total_primitives = 0

for drawing in iter_project_drawings_streamed(source):
    restored = dict(drawing)
    restored_pages = []

    for page in drawing["pages"]:
        restored_page = dict(page)
        primitives = list(page["primitives"])

        total_primitives += len(primitives)
        restored_page["primitives"] = primitives
        restored_pages.append(restored_page)

    restored["pages"] = restored_pages
    restored_drawings.append(restored)

print("Drawings:", len(restored_drawings))
print("First drawing:", restored_drawings[0]["drawing_id"])
print("Second drawing:", restored_drawings[1]["drawing_id"])
print("Total streamed primitives:", total_primitives)

print(
    "First drawing primitive count:",
    sum(len(p["primitives"]) for p in restored_drawings[0]["pages"])
)

print(
    "Second drawing primitive count:",
    sum(len(p["primitives"]) for p in restored_drawings[1]["pages"])
)

passed = restored_drawings == original["drawings"]

print("Exact drawing equality:", passed)
print(
    "TWO-PASS PROJECT STREAM TEST:",
    "PASS" if passed else "FAIL"
)
