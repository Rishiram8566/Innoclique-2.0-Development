
import json
import tempfile
import unittest
from pathlib import Path

from run_stage5_2_11_vertical_levels import run_project


class TestVerticalLevelProjectRunnerStage5211(unittest.TestCase):

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
            self.artifacts
            / "drawing_geometry_stage5_metadata_index.json"
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
                                {
                                    "text": (
                                        "GROUND FLOOR AREA = "
                                        "30,736.02 SQ.FT"
                                    ),
                                    "bbox": [500, 100, 700, 110],
                                    "block_index": 3,
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

        self.output_path = (
            self.artifacts
            / "vertical_level_evidence_stage5_2_11.json"
        )

        self.old_report_path = (
            self.artifacts
            / "vertical_level_evidence_stage5_2_10.json"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generates_separate_stage_5211_report(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        self.assertTrue(self.output_path.is_file())

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["stage"], "5.2.11")
        self.assertEqual(
            report["summary"]["candidate_count"],
            2,
        )

        saved_report = json.loads(
            self.output_path.read_text(encoding="utf-8")
        )

        self.assertEqual(saved_report["stage"], "5.2.11")

    def test_rejects_area_false_positive(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        candidates = report["candidates"]

        self.assertFalse(
            any(
                "AREA" in candidate["source"]["text"].upper()
                for candidate in candidates
            )
        )

    def test_preserves_source_hash(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        source_hash = report["source"]["metadata_index_sha256"]

        self.assertEqual(len(source_hash), 64)

        self.assertEqual(
            report["source"]["metadata_index_filename"],
            self.metadata_path.name,
        )

    def test_does_not_approve_or_generate_geometry(self):
        report = run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        self.assertEqual(
            report["summary"]["approved_count"],
            0,
        )

        self.assertEqual(
            report["summary"]["calibration_eligible_count"],
            0,
        )

        self.assertEqual(
            report["summary"]["geometry_generation_allowed_count"],
            0,
        )

        self.assertFalse(
            report["runner"]["evidence_approved"]
        )

        self.assertFalse(
            report["runner"]["geometry_generated"]
        )

    def test_preserves_existing_stage_5210_report(self):
        original_content = '{"stage":"5.2.10","preserved":true}'

        self.old_report_path.write_text(
            original_content,
            encoding="utf-8",
        )

        run_project(
            project_id=self.project_id,
            backend_root=self.root,
        )

        self.assertEqual(
            self.old_report_path.read_text(encoding="utf-8"),
            original_content,
        )

        self.assertTrue(self.output_path.is_file())

    def test_does_not_overwrite_existing_stage_5211_report(self):
        original_content = '{"existing":"report"}'

        self.output_path.write_text(
            original_content,
            encoding="utf-8",
        )

        with self.assertRaises(FileExistsError):
            run_project(
                project_id=self.project_id,
                backend_root=self.root,
            )

        self.assertEqual(
            self.output_path.read_text(encoding="utf-8"),
            original_content,
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
