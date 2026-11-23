
import json
import tempfile
import unittest

from pathlib import Path

from app.services.dimension_evidence_project_runner import (
    build_project_dimension_evidence,
    run_project_dimension_evidence,
)


class TestDimensionEvidenceProjectRunner(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

        self.project = Path(self.temp.name) / "project-1"

        self.artifacts = self.project / "artifacts"

        self.artifacts.mkdir(parents=True)

        self.index_path = (
            self.artifacts
            / "drawing_geometry_stage5_metadata_index.json"
        )

        index = {
            "schema_version": "0.1.0",
            "source_artifact": "drawing_geometry_stage5.json",
            "generated_at": "2026-11-15T00:00:00+00:00",

            "drawings": [
                {
                    "drawing_id": "d1",
                    "filename": "plan.pdf",

                    "source": {
                        "sha256": "source-hash-1"
                    },

                    "pages": [
                        {
                            "page_number": 1,

                            "text_spans": [
                                {
                                    "text": "600mm HIGH",
                                    "bbox": [1, 2, 3, 4],
                                },
                                {
                                    "text": (
                                        "DO NOT SCALE FROM THIS DRAWING"
                                    )
                                },
                                {
                                    "text": "SCALE: 1:200"
                                },
                            ],
                        }
                    ],
                },

                {
                    "drawing_id": "d2",
                    "filename": "section.pdf",

                    "source": {
                        "sha256": "source-hash-2"
                    },

                    "pages": [
                        {
                            "page_number": 1,

                            "text_spans": [
                                {
                                    "text": (
                                        "250mm THERMAL INSULATED "
                                        "BLOCK WALL"
                                    )
                                },
                            ],
                        }
                    ],
                },
            ],
        }

        self.index_path.write_text(
            json.dumps(index),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_report_from_metadata_index(self):

        report = build_project_dimension_evidence(
            self.project,
            "project-1",
        )

        self.assertEqual(
            report["summary"]["indexed_drawing_count"],
            2,
        )

        self.assertEqual(
            report["summary"]["processed_drawing_count"],
            2,
        )

        self.assertEqual(
            report["summary"]["processed_page_count"],
            2,
        )

        self.assertEqual(
            report["summary"]["dimension_evidence_count"],
            2,
        )

        self.assertEqual(
            report["summary"]["pages_with_scale_restriction"],
            1,
        )

        self.assertEqual(
            report["summary"]["calibration_eligible_count"],
            0,
        )

    def test_preserves_drawing_source_hashes(self):

        report = build_project_dimension_evidence(
            self.project,
            "project-1",
        )

        hashes = [
            drawing["source_sha256"]
            for drawing in report["drawings"]
        ]

        self.assertEqual(
            hashes,
            [
                "source-hash-1",
                "source-hash-2",
            ],
        )

    def test_run_writes_separate_artifact(self):

        receipt = run_project_dimension_evidence(
            self.project,
            "project-1",
        )

        output = (
            self.artifacts
            / "dimension_evidence_stage5_2_4.json"
        )

        self.assertTrue(output.is_file())

        saved = json.loads(
            output.read_text(encoding="utf-8")
        )

        self.assertEqual(
            saved["stage"],
            "5.2.5",
        )

        self.assertEqual(
            receipt["status"],
            "completed",
        )

    def test_missing_index_fails_without_creating_output(self):

        self.index_path.unlink()

        with self.assertRaises(FileNotFoundError):

            run_project_dimension_evidence(
                self.project,
                "project-1",
            )

        self.assertFalse(
            (
                self.artifacts
                / "dimension_evidence_stage5_2_4.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
