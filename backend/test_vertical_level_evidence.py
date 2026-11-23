
import unittest

from app.services.vertical_level_evidence import (
    extract_vertical_level_evidence,
)


class TestVerticalLevelEvidence(unittest.TestCase):

    def make_metadata(self, spans):
        return {
            "drawings": [
                {
                    "drawing_id": "drawing-001",
                    "filename": "ARC-2001_SECTION A-A.pdf",
                    "pages": [
                        {
                            "page_number": 1,
                            "text_spans": spans,
                        }
                    ],
                }
            ]
        }

    def test_extracts_combined_level_annotation(self):
        metadata = self.make_metadata([
            {
                "text": "T.R.L +28.30",
                "bbox": [100, 100, 150, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 1)

        candidate = report["candidates"][0]

        self.assertEqual(candidate["semantic_type"], "top_roof_level")
        self.assertEqual(candidate["value_text"], "+28.30")
        self.assertIsNone(candidate["unit"])

    def test_pairs_nearby_explicit_mezzanine_level_label(self):
        metadata = self.make_metadata([
            {
                "text": "MEZZANINE FLOOR LEVEL",
                "bbox": [100, 100, 180, 110],
                "block_index": 1,
                "line_index": 0,
            },
            {
                "text": "+6.05",
                "bbox": [120, 112, 150, 122],
                "block_index": 2,
                "line_index": 0,
            },
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 1)

        candidate = report["candidates"][0]

        self.assertEqual(candidate["semantic_type"], "mezzanine_level")
        self.assertEqual(candidate["value_text"], "+6.05")
        self.assertEqual(
            candidate["source"]["label_text"],
            "MEZZANINE FLOOR LEVEL",
        )

    def test_unpaired_numeric_value_is_not_promoted(self):
        metadata = self.make_metadata([
            {
                "text": "+15.75",
                "bbox": [100, 100, 150, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(
            report["summary"]["skipped_unpaired_numeric_count"], 1
        )

    def test_preserves_unapproved_safety_status(self):
        metadata = self.make_metadata([
            {
                "text": "GROUND FLOOR LEVEL +0.45",
                "bbox": [100, 100, 200, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 1)

        candidate = report["candidates"][0]

        self.assertTrue(candidate["verification_required"])
        self.assertEqual(candidate["approval_status"], "unapproved")
        self.assertFalse(candidate["calibration_eligible"])
        self.assertFalse(candidate["geometry_generation_allowed"])

    def test_preserves_source_drawing_and_bbox(self):
        metadata = self.make_metadata([
            {
                "text": "ROOF FLOOR (LOWER) +11.55",
                "bbox": [150, 200, 250, 210],
                "block_index": 7,
                "line_index": 1,
                "span_index": 4,
            }
        ])

        report = extract_vertical_level_evidence(
            metadata,
            project_id="validation-project",
        )

        self.assertEqual(report["summary"]["candidate_count"], 1)

        candidate = report["candidates"][0]

        self.assertEqual(candidate["project_id"], "validation-project")
        self.assertEqual(candidate["drawing_id"], "drawing-001")
        self.assertEqual(candidate["page_number"], 1)
        self.assertEqual(candidate["source"]["bbox"], [150, 200, 250, 210])
        self.assertEqual(candidate["source"]["span_index"], 4)

    def test_rejects_area_calculation_as_level(self):
        metadata = self.make_metadata([
            {
                "text": "GROUND FLOOR AREA = 30,736.02 SQ.FT",
                "bbox": [100, 100, 250, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_mezzanine_area_as_level(self):
        metadata = self.make_metadata([
            {
                "text": "MEZZANINE FLOOR AREA = 13,475.87 SQ.FT",
                "bbox": [100, 100, 250, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_basement_area_as_level(self):
        metadata = self.make_metadata([
            {
                "text": "BASEMENT FLOOR AREA = 44,077.42 SQ.FT",
                "bbox": [100, 100, 250, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_stair_identifier(self):
        metadata = self.make_metadata([
            {
                "text": "STAIR - 01",
                "bbox": [100, 100, 150, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_battery_rack_identifier(self):
        metadata = self.make_metadata([
            {
                "text": "BATTERY RACK - 01",
                "bbox": [100, 100, 180, 110],
                "block_index": 1,
                "line_index": 0,
            }
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_generic_ground_floor_label(self):
        metadata = self.make_metadata([
            {
                "text": "GROUND FLOOR",
                "bbox": [100, 100, 180, 110],
                "block_index": 1,
                "line_index": 0,
            },
            {
                "text": "+0.45",
                "bbox": [120, 112, 150, 122],
                "block_index": 2,
                "line_index": 0,
            },
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_integer_only_identifier(self):
        metadata = self.make_metadata([
            {
                "text": "STAIR - 01",
                "bbox": [100, 100, 150, 110],
                "block_index": 1,
                "line_index": 0,
            },
            {
                "text": "F.F.L",
                "bbox": [110, 112, 150, 122],
                "block_index": 2,
                "line_index": 0,
            },
        ])

        report = extract_vertical_level_evidence(metadata)

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_rejects_invalid_metadata(self):
        with self.assertRaises(TypeError):
            extract_vertical_level_evidence([])

    def test_rejects_negative_label_distance(self):
        with self.assertRaises(ValueError):
            extract_vertical_level_evidence(
                {"drawings": []},
                max_label_distance_pdf_points=-1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
