from pathlib import Path
from itertools import zip_longest
import ijson


def iter_drawing_metadata(path):
    path = Path(path)

    if not path.is_file():
        return

    def values(prefix):
        with path.open("rb") as stream:
            yield from ijson.items(stream, prefix)

    ids = values("drawings.item.drawing_id")
    hashes = values("drawings.item.source_sha256")
    summaries = values("drawings.item.summary")

    for drawing_id, source_hash, summary in zip_longest(
        ids, hashes, summaries
    ):
        if drawing_id is None:
            raise ValueError("Drawing metadata is incomplete.")

        yield {
            "drawing_id": str(drawing_id),
            "source_sha256": str(source_hash or ""),
            "summary": summary or {},
        }


if __name__ == "__main__":
    report = Path(
        "data/validation_stage5/stage50_pipeline_test/"
        "artifacts/drawing_geometry_stage5.json"
    )

    for item in iter_drawing_metadata(report):
        print(item)
