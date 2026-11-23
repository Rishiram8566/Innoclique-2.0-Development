import unittest

from app.services.engineering_element_recognition import (
    _classify_pair_context,
    _near_text_labels,
)


class TestContextualElementRecognition(unittest.TestCase):

    def test_tank_label_is_not_equipment(self):
        result = _classify_pair_context([
            {"text": "BLOWDOWN TANK", "distance_pdf_points": 30}
        ])
        self.assertEqual(result[0], "tank_boundary_candidate")

    def test_reservoir_label_is_tank_boundary(self):
        result = _classify_pair_context([
            {"text": "WATER RESERVOIR", "distance_pdf_points": 20}
        ])
        self.assertEqual(result[0], "tank_boundary_candidate")

    def test_mechanical_equipment_remains_separate(self):
        result = _classify_pair_context([
            {"text": "BLOWDOWN PUMP", "distance_pdf_points": 15}
        ])
        self.assertEqual(result[0], "equipment_outline_candidate")

    def test_explicit_wall_label_is_wall_edge_candidate(self):
        result = _classify_pair_context([
            {"text": "REINFORCED CONCRETE WALL", "distance_pdf_points": 12}
        ])
        self.assertEqual(result[0], "wall_edge_pair_candidate")

    def test_unlabelled_pair_stays_unclassified(self):
        result = _classify_pair_context([])
        self.assertEqual(result[0], "unclassified_parallel_line_pair_candidate")

    def test_semantic_lookup_can_retrieve_text_beyond_24_points(self):
        labels = _near_text_labels(
            [("TANK-01", [50, 0, 60, 10])],
            [0, 0, 10, 10],
            max_distance=96,
            max_labels=12,
        )
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["text"], "TANK-01")


if __name__ == "__main__":
    unittest.main()
