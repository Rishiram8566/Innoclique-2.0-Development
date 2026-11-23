from pathlib import Path
import ijson
from ijson.common import ObjectBuilder


def iter_drawing_metadata(path):
    path = Path(path)
    current = None
    summary_builder = None

    with path.open("rb") as stream:
        for prefix, event, value in ijson.parse(stream):

            if prefix == "drawings.item" and event == "start_map":
                current = {}
                continue

            if current is None:
                continue

            if summary_builder is not None:
                summary_builder.event(event, value)

                if prefix == "drawings.item.summary" and event == "end_map":
                    current["summary"] = summary_builder.value
                    summary_builder = None

                continue

            if prefix == "drawings.item.drawing_id":
                if event in ("string", "number"):
                    current["drawing_id"] = str(value)

            elif prefix == "drawings.item.source_sha256":
                if event == "string":
                    current["source_sha256"] = value

            elif prefix == "drawings.item.summary" and event == "start_map":
                summary_builder = ObjectBuilder()
                summary_builder.event(event, value)

            elif prefix == "drawings.item" and event == "end_map":
                yield current
                current = None


if __name__ == "__main__":
    report = Path(
        "data/validation_stage5/stage50_pipeline_test/"
        "artifacts/drawing_geometry_stage5.json"
    )

    print("========== SINGLE-PASS METADATA TEST ==========")

    for item in iter_drawing_metadata(report):
        print("Drawing ID:", item.get("drawing_id"))
        print("Source SHA256:", item.get("source_sha256"))
        print("Summary:", item.get("summary"))
        print(
            "Primitive count:",
            item.get("summary", {}).get("vector_primitive_count")
        )
