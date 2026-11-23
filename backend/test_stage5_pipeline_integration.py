
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.services import geometry_pipeline


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_drawing(drawing_id, source_hash, value):
    return {
        "drawing_id": drawing_id,
        "filename": f"{drawing_id}.pdf",
        "source_sha256": source_hash,
        "source": {"sha256": source_hash},
        "summary": {
            "page_count": 1,
            "vector_path_count": 1,
            "vector_primitive_count": 1,
            "text_span_count": 0,
            "dimension_candidate_count": 0,
            "level_candidate_count": 0,
        },
        "pages": [{
            "page_number": 0,
            "primitives": [{
                "type": "line",
                "value": value,
                "page_number": 0,
            }],
        }],
    }


def write_report(path, drawings):
    report = {
        "schema_version": "0.2.0",
        "stage": "5.0",
        "status": "completed",
        "project_id": "isolated-test",
        "project_name": "Stage 5 Integration Test",
        "summary": {
            "page_count": len(drawings),
            "vector_path_count": len(drawings),
            "vector_primitive_count": len(drawings),
            "text_span_count": 0,
            "dimension_candidate_count": 0,
            "level_candidate_count": 0,
        },
        "drawings": drawings,
        "failed": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")


def fake_extractor(source, output, *, drawing_id=None,
                   filename=None, batch_size=10000):
    source_hash = sha256(Path(source))
    drawing = make_drawing(drawing_id, source_hash, "NEW")

    Path(output).write_text(
        json.dumps(drawing),
        encoding="utf-8",
    )

    return {"source_sha256": source_hash}


def run_test():
    with tempfile.TemporaryDirectory(prefix="stage5_pipeline_test_") as temp:
        root = Path(temp)
        drawings_dir = root / "drawings"
        artifacts_dir = root / "artifacts"
        drawings_dir.mkdir()
        artifacts_dir.mkdir()

        # Existing drawing A remains unchanged.
        old_a = drawings_dir / "a.pdf"
        old_a.write_bytes(b"PDF-A")

        # Drawing B has a changed PDF and must be replaced.
        new_b = drawings_dir / "b.pdf"
        new_b.write_bytes(b"PDF-B-UPDATED")

        # Drawing C has a changed PDF, but extraction will fail.
        new_c = drawings_dir / "c.pdf"
        new_c.write_bytes(b"PDF-C-UPDATED")

        old_drawings = [
            make_drawing("A", sha256(old_a), "OLD-A"),
            make_drawing("B", "old-b-hash", "OLD-B"),
            make_drawing("C", "old-c-hash", "OLD-C"),
        ]

        target = artifacts_dir / "drawing_geometry_stage5.json"
        write_report(target, old_drawings)

        project = {
            "id": "isolated-test",
            "name": "Stage 5 Integration Test",
            "drawings": [
                {"id": "A", "filename": "a.pdf",
                 "stored_filename": "a.pdf"},
                {"id": "B", "filename": "b.pdf",
                 "stored_filename": "b.pdf"},
                {"id": "C", "filename": "c.pdf",
                 "stored_filename": "c.pdf"},
            ],
        }

        original_extractor = geometry_pipeline.stream_pdf_geometry_to_json

        def controlled_extractor(source, output, **kwargs):
            if Path(source).name == "c.pdf":
                raise RuntimeError("SIMULATED EXTRACTION FAILURE")
            return fake_extractor(source, output, **kwargs)

        with patch.object(
            geometry_pipeline,
            "stream_pdf_geometry_to_json",
            side_effect=controlled_extractor,
        ):
            # Process B only. C remains pending for the next batch.
            result_b = geometry_pipeline.extract_project_geometry(
                root, project, batch_size=1, filename="b.pdf"
            )

        report_b = json.loads(target.read_text(encoding="utf-8"))
        by_id_b = {
            item["drawing_id"]: item
            for item in report_b["drawings"]
        }

        assert by_id_b["A"]["pages"][0]["primitives"][0]["value"] == "OLD-A"
        assert by_id_b["B"]["pages"][0]["primitives"][0]["value"] == "NEW"
        assert by_id_b["C"]["pages"][0]["primitives"][0]["value"] == "OLD-C"

        print("SUCCESSFUL REPLACEMENT: PASS")
        print("UNCHANGED DRAWING PRESERVATION: PASS")

        with patch.object(
            geometry_pipeline,
            "stream_pdf_geometry_to_json",
            side_effect=controlled_extractor,
        ):
            # Process C; the simulated extractor fails.
            result_c = geometry_pipeline.extract_project_geometry(
                root, project, batch_size=1, filename="c.pdf"
            )

        report_c = json.loads(target.read_text(encoding="utf-8"))
        by_id_c = {
            item["drawing_id"]: item
            for item in report_c["drawings"]
        }

        assert by_id_c["C"]["pages"][0]["primitives"][0]["value"] == "OLD-C"
        assert any(
            item.get("drawing_id") == "C"
            and "SIMULATED EXTRACTION FAILURE" in item.get("reason", "")
            for item in report_c["failed"]
        )

        print("FAILED EXTRACTION PRESERVATION: PASS")
        print("FAILURE RECORDING: PASS")
        print("\nALL ISOLATED PIPELINE TESTS: PASS")


if __name__ == "__main__":
    run_test()
