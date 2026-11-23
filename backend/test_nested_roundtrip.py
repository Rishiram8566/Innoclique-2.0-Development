
import json
from pathlib import Path

from app.services.nested_geometry_reader import (
    iter_drawing_pages_with_metadata,
)
from app.services.geometry_stream_writer import write_json_atomic


source = Path("data/validation_stage5/drawing_1002_complete_test.json")
output = Path("data/validation_stage5/drawing_1002_roundtrip_test.json")

metadata, pages = iter_drawing_pages_with_metadata(source)

reconstructed = {
    **metadata,
    "pages": pages,
}

result = write_json_atomic(output, reconstructed)

print("Output created:", result["write_complete"])
print("Output size:", result["size_bytes"])

# Compare original and reconstructed JSON.
with source.open("r", encoding="utf-8") as stream:
    original = json.load(stream)

with output.open("r", encoding="utf-8") as stream:
    restored = json.load(stream)

print("Drawing ID preserved:",
      original.get("drawing_id") == restored.get("drawing_id"))

print("Page count preserved:",
      len(original["pages"]) == len(restored["pages"]))

original_count = sum(
    len(page["primitives"]) for page in original["pages"]
)

restored_count = sum(
    len(page["primitives"]) for page in restored["pages"]
)

print("Original primitives:", original_count)
print("Restored primitives:", restored_count)
print("Primitive count preserved:",
      original_count == restored_count)

print("Complete JSON equality:", original == restored)

print(
    "ROUNDTRIP TEST:",
    "PASS" if original == restored else "FAIL"
)
