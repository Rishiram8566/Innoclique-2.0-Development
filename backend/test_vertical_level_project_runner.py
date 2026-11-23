
import json
import tempfile
import unittest
from pathlib import Path

from run_stage5_2_10_vertical_levels import run_project


class TestVerticalLevelProjectRunner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        self.project_id = "test-project"
        self.project_root = (
            self.root / "data" / "projects" / self.project_id
        )
        self.artifacts = self.project_root / "artifacts"
        self.artifacts.mkdir(parents=True)

        self.metadata_path = (
            self.artifacts / "drawing_geometry_stage5_metadata_index.json"
        )

        metadata = {
            "drawings": [
                {
                    "drawing_id": "drawing-001",
                    "filename": "ARC-2001_SECTION A-A.pdf",
                    "pages": [
                        {
                            "page_number": 1,
                            "text_spans": [
                                {
                                   "text": "GROUND FLOOR LEVEL +0.45",
                                    "bbox": [100, 100, 200, 110],
                                    "block_index": 1,
                                    "line_index": 0,
                                },
                                {
                                    "text": "T.R.L +28.30",
                                    "bbox": [300, 100, 400, 110],
                                    "block_index": 2,
                                    "line_index": 0,
                                },
                            ],
                        }
                    ],
                }
            ]
        }

        self.metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generates_separate_report(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        output = self.artifacts / (
            "vertical_level_evidence_stage5_2_10.json"
        )

        self.assertTrue(output.is_file())
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["summary"]["candidate_count"], 2)

    def test_preserves_source_hash(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        self.assertEqual(
            len(report["source"]["metadata_index_sha256"]),
            64,
        )

    def test_does_not_approve_or_generate_geometry(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        self.assertEqual(report["summary"]["approved_count"], 0)
        self.assertEqual(
            report["summary"]["geometry_generation_allowed_count"],
            0,
        )
        self.assertFalse(report["runner"]["geometry_generated"])

    def test_existing_report_is_not_overwritten_by_default(self):
        output = self.artifacts / (
            "vertical_level_evidence_stage5_2_10.json"
        )
        output.write_text("existing report", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            run_project(
                project_id=self.project_id,
                backend_root=self.root,
            )

        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "existing report",
        )

    def test_missing_metadata_fails(self):
        self.metadata_path.unlink()

        with self.assertRaises(FileNotFoundError):
            run_project(
                project_id=self.project_id,
                backend_root=self.root,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
