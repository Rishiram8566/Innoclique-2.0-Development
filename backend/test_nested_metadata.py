from pathlib import Path
from app.services.nested_geometry_reader import _read_page_metadata

source = Path("data/validation_stage5/drawing_1002_complete_test.json")
pages = _read_page_metadata(source)

print("Pages:", len(pages))

if not pages:
    raise RuntimeError("No pages found!")

page = pages[0]

print("Fields:", list(page.keys()))

for key in ("text_spans", "dimension_candidates", "level_candidates", "quality"):
    print(key, "PRESENT" if key in page else "MISSING")

print("Text spans:", len(page.get("text_spans", [])))
print("Dimension candidates:", len(page.get("dimension_candidates", [])))
print("Level candidates:", len(page.get("level_candidates", [])))
print("Quality:", page.get("quality"))