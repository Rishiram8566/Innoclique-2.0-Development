
import unittest

from app.services.dimension_evidence_classifier_v2 import (
    classify_page_evidence,
)


class TestDimensionEvidenceClassifierV2(unittest.TestCase):

    def classify(self, text):
        page = {
            "page_number": 1,
            "text_spans": [
                {
                    "text": text,
                    "bbox": [10, 20, 100, 40],
                }
            ],
        }

        result = classify_page_evidence(
            page,
            drawing_id="test-drawing",
        )

        return result["dimension_evidence"]

    def test_waterproofing_thickness(self):
        result = self.classify("25MM WATERPROOF")
        self.assertEqual(
            result[0]["semantic_type"],
            "waterproofing_thickness",
        )

    def test_plaster_thickness(self):
        result = self.classify(
            "15 MM THICK SMOOTH CEMENT/SAND PLASTER"
        )
        self.assertEqual(
            result[0]["semantic_type"],
            "plaster_thickness",
        )

    def test_ceiling_thickness(self):
        result = self.classify(
            "12mm THICK GYPSUM CEILING BOARD"
        )
        self.assertEqual(
            result[0]["semantic_type"],
            "ceiling_thickness",
        )

    def test_concrete_thickness(self):
        result = self.classify("100MM THK P.C.C")
        self.assertEqual(
            result[0]["semantic_type"],
            "concrete_thickness",
        )

    def test_floor_elevation(self):
        result = self.classify("FFL +6.15 m")
        self.assertEqual(
            result[0]["semantic_type"],
            "elevation",
        )

    def test_diameter_symbol(self):
        result = self.classify("Ø30m")
        self.assertEqual(
            result[0]["semantic_type"],
            "diameter",
        )

    def test_parapet_height(self):
        result = self.classify("PARAPET 1.2M HIGH")
        self.assertEqual(
            result[0]["semantic_type"],
            "height",
        )

    def test_original_unit_is_preserved(self):
        result = self.classify("25MM WATERPROOF")

        self.assertEqual(result[0]["unit"], "MM")
        self.assertEqual(result[0]["normalized_unit"], "mm")

    def test_verification_safeguards(self):
        result = self.classify("100MM THK P.C.C")

        self.assertTrue(result[0]["verification_required"])
        self.assertFalse(result[0]["calibration_eligible"])
        self.assertEqual(result[0]["approval_status"], "unapproved")

    def test_source_text_and_bbox_preserved(self):
        result = self.classify("20mm granite vanity top")

        self.assertEqual(
            result[0]["source"]["text"],
            "20mm granite vanity top",
        )

        self.assertEqual(
            result[0]["source"]["bbox"],
            [10, 20, 100, 40],
        )

    def test_no_scale_note_detected(self):
        page = {
            "page_number": 1,
            "text_spans": [
                {"text": "DO NOT SCALE FROM THIS DRAWING"}
            ],
        }

        result = classify_page_evidence(page)

        self.assertTrue(
            result["scale_restriction"]["present"]
        )

    def test_component_size_not_overclassified(self):
        result = self.classify("1000 X 2200 mm")

        self.assertEqual(
            result[0]["semantic_type"],
            "dimension_unspecified",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
