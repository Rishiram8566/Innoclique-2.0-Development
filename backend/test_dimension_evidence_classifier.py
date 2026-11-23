import unittest

from app.services.dimension_evidence_classifier import (
    classify_drawing_evidence,
    classify_page_evidence,
)


class TestDimensionEvidenceClassifier(unittest.TestCase):
    def page(self, *texts):
        return {
            "page_number": 1,
            "text_spans": [
                {"text": text, "page_number": 1, "bbox": [1, 2, 3, 4]}
                for text in texts
            ],
        }

    def test_decimal_token_is_complete(self):
        result = classify_page_evidence(
            self.page("11KV CABLE TRENCH DEPTH 1.50M")
        )
        values = [item["value_text"] for item in result["dimension_evidence"]]
        self.assertEqual(values, ["1.50M"])
        self.assertEqual(
            result["dimension_evidence"][0]["semantic_type"], "depth"
        )

    def test_diameter_and_height_context(self):
        result = classify_page_evidence(self.page("Ø30m & 26.5m HIGH"))
        items = result["dimension_evidence"]
        self.assertEqual([x["value_text"] for x in items], ["30m", "26.5m"])
        self.assertEqual([x["semantic_type"] for x in items],
                         ["diameter", "height"])

    def test_height_and_wall_thickness(self):
        result = classify_page_evidence(self.page(
            "600mm HIGH",
            "250mm THERMAL INSULATED BLOCK WALL",
        ))
        items = result["dimension_evidence"]
        self.assertEqual([x["semantic_type"] for x in items],
                         ["height", "wall_thickness"])

    def test_no_scale_note_blocks_printed_scale_use(self):
        result = classify_page_evidence(self.page(
            "SCALE: 1:200",
            "DO NOT SCALE FROM THIS DRAWING",
        ))
        restriction = result["scale_restriction"]
        self.assertTrue(restriction["present"])
        self.assertFalse(restriction["printed_scale_usable"])
        self.assertEqual(
            restriction["status"], "scale_prohibited_by_drawing_note"
        )

    def test_all_dimensions_remain_unapproved_and_not_calibration_eligible(self):
        result = classify_page_evidence(self.page("600mm HIGH"))
        item = result["dimension_evidence"][0]
        self.assertTrue(item["verification_required"])
        self.assertFalse(item["calibration_eligible"])
        self.assertEqual(item["approval_status"], "unapproved")

    def test_source_offsets_and_text_are_preserved(self):
        source = "Wall thickness 250mm THERMAL BLOCK WALL"
        result = classify_page_evidence(self.page(source))
        item = result["dimension_evidence"][0]
        source_info = item["source"]
        self.assertEqual(source_info["text"], source)
        start, end = source_info["char_start"], source_info["char_end"]
        self.assertEqual(source[start:end], "250mm")

    def test_drawing_report_preserves_source_hash(self):
        drawing = {
            "drawing_id": "drawing-1",
            "filename": "plan.pdf",
            "source": {"sha256": "abc123"},
            "pages": [self.page("600mm HIGH")],
        }
        result = classify_drawing_evidence(drawing)
        self.assertEqual(result["source_sha256"], "abc123")
        self.assertEqual(result["summary"]["dimension_evidence_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
