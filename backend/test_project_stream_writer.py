
import json
from pathlib import Path

from app.services.project_report_stream_reader import (
    iter_project_drawings_streamed,
)
from app.services.geometry_stream_writer import write_json_atomic

source = Path(
    "data/validation_stage5/project_report_reader_test.json"
)

output = Path(
    "data/validation_stage5/project_report_stream_writer_test.json"
)

original = json.loads(source.read_text(encoding="utf-8"))

report = {
    "schema_version": "0.2.0",
    "stage": "5.0",
    "drawings": iter_project_drawings_streamed(source),
}

result = write_json_atomic(output, report)

restored = json.loads(output.read_text(encoding="utf-8"))

print("Writer completed:", result["write_complete"])
print("Output size:", result["size_bytes"])
print("Drawing count:", len(restored["drawings"]))
print("Exact drawing equality:",
      restored["drawings"] == original["drawings"])

passed = (
    result["write_complete"]
    and restored["drawings"] == original["drawings"]
)

print(
    "PROJECT STREAM WRITER TEST:",
    "PASS" if passed else "FAIL"
)
