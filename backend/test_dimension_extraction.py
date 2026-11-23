import unittest

from app.services.drawing_geometry_extractor import _text_candidates


class TestDimensionExtraction(unittest.TestCase):

    def extract(self, source_text):
        span = {"text": source_text}
        result = _text_candidates([span])
        return result["dimension_candidates"], span

    def test_decimal_measurement_is_not_truncated(self):
        candidates, _ = self.extract("11KV CABLE TRENCH DEPTH 1.50M")

        values = [item["value_text"] for item in candidates]

        self.assertEqual(values, ["1.50M"])

    def test_multiple_measurements_in_one_text_span(self):
        candidates, _ = self.extract("(Ø30m & 26.5m HIGH)")

        values = [item["value_text"] for item in candidates]

        self.assertEqual(values, ["30m", "26.5m"])

    def test_height_annotation(self):
        candidates, _ = self.extract("600mm HIGH")

        values = [item["value_text"] for item in candidates]

        self.assertEqual(values, ["600mm"])

    def test_wall_thickness_annotation(self):
        candidates, _ = self.extract(
            "250mm THERMAL INSULATED BLOCK WALL"
        )

        values = [item["value_text"] for item in candidates]

        self.assertEqual(values, ["250mm"])

    def test_original_source_span_is_preserved(self):
        candidates, span = self.extract("600mm HIGH")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_span"], span)
        self.assertTrue(candidates[0]["verification_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
