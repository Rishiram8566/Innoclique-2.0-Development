
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.dimension_evidence_project_runner_v2 import (
    OUTPUT_FILENAME,
    build_project_dimension_evidence_report,
    run_project_dimension_evidence,
    DimensionEvidenceRunnerError,
)


class TestDimensionEvidenceProjectRunnerV2(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.artifacts = self.project / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.project_id = "test-project"

    def tearDown(self):
        self.temp.cleanup()

    def write_index(self, drawings):
        index = {
            "schema_version": "test",
            "source_artifact": "drawing_geometry_stage5.json",
            "drawings": drawings,
        }
        path = self.artifacts / "drawing_geometry_stage5_metadata_index.json"
        path.write_text(json.dumps(index), encoding="utf-8")
        return path

    @staticmethod
    def page(text):
        return {
            "page_number": 1,
            "text_spans": [
                {
                    "text": text,
                    "bbox": [1, 2, 3, 4],
                    "page_number": 1,
                }
            ],
        }

    def test_builds_report_from_metadata_index(self):
        self.write_index([{
            "drawing_id": "drawing-1",
            "filename": "plan.pdf",
            "source": {"sha256": "abc123"},
            "pages": [self.page("25MM WATERPROOF")],
        }])

        report = build_project_dimension_evidence_report(
            self.project,
            self.project_id,
        )

        self.assertEqual(report["summary"]["indexed_drawing_count"], 1)
        self.assertEqual(report["summary"]["processed_drawing_count"], 1)
        evidence = report["drawings"][0]["pages"][0]["dimension_evidence"][0]
        self.assertEqual(evidence["semantic_type"], "waterproofing_thickness")
        self.assertEqual(report["classification_stage"], "5.2.8")

    def test_missing_index_fails_without_creating_output(self):
        with self.assertRaises(DimensionEvidenceRunnerError):
            run_project_dimension_evidence(self.project, self.project_id)

        self.assertFalse((self.artifacts / OUTPUT_FILENAME).exists())

    def test_writes_separate_artifact(self):
        self.write_index([{
            "drawing_id": "drawing-1",
            "filename": "plan.pdf",
            "pages": [self.page("12mm THICK GYPSUM CEILING BOARD")],
        }])

        receipt = run_project_dimension_evidence(
            self.project,
            self.project_id,
        )

        output = self.artifacts / OUTPUT_FILENAME
        self.assertTrue(output.exists())
        self.assertEqual(receipt["status"], "completed")

        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["drawings"][0]["drawing_id"], "drawing-1")

    def test_preserves_source_hash_and_evidence_safeguards(self):
        self.write_index([{
            "drawing_id": "drawing-2",
            "filename": "section.pdf",
            "source": {"sha256": "source-hash-456"},
            "pages": [self.page("FFL +6.15 m")],
        }])

        report = build_project_dimension_evidence_report(
            self.project,
            self.project_id,
        )

        evidence = report["drawings"][0]["pages"][0]["dimension_evidence"][0]
        self.assertEqual(report["drawings"][0]["source_sha256"], "source-hash-456")
        self.assertEqual(evidence["semantic_type"], "elevation")
        self.assertTrue(evidence["verification_required"])
        self.assertFalse(evidence["calibration_eligible"])
        self.assertEqual(evidence["approval_status"], "unapproved")

    def test_skips_invalid_drawing_record_and_reports_it(self):
        self.write_index([None, {
            "drawing_id": "drawing-3",
            "filename": "plan.pdf",
            "pages": [self.page("Ø30m")],
        }])

        report = build_project_dimension_evidence_report(
            self.project,
            self.project_id,
        )

        self.assertEqual(report["summary"]["skipped_drawing_count"], 1)
        self.assertEqual(report["summary"]["processed_drawing_count"], 1)
        self.assertEqual(report["status"], "completed_with_skips")


if __name__ == "__main__":
    unittest.main(verbosity=2)
