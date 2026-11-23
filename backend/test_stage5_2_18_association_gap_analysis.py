
import unittest

from app.services.association_gap_analysis import analyze_association_gaps


def make_input():
    return {
        "stage": "5.2.17",
        "project_id": "test-project",
        "geometry_candidates": [
            {
                "candidate_id": "wall-1",
                "drawing_id": "drawing-1",
                "page_number": 1,
                "element_type": "wall_geometry_supported_candidate",
            },
            {
                "candidate_id": "stair-1",
                "drawing_id": "drawing-1",
                "page_number": 2,
                "element_type": "stair_boundary_candidate",
            },
        ],
        "evidence_associations": [
            {
                "evidence_id": "evidence-1",
                "drawing_id": "drawing-1",
                "page_number": 1,
                "semantic_type": "wall_thickness_dimension",
                "value_text": "200",
                "evidence_source": {"text": "WALL THK = 200"},
                "explicit_incompatible_candidate_ids": [],
            },
            {
                "evidence_id": "evidence-2",
                "drawing_id": "drawing-1",
                "page_number": 2,
                "semantic_type": "finished_floor_level",
                "value_text": "+0.45",
                "evidence_source": {"text": "FFL +0.45"},
                "explicit_incompatible_candidate_ids": [],
            },
            {
                "evidence_id": "evidence-3",
                "drawing_id": "drawing-2",
                "page_number": 1,
                "semantic_type": "finished_floor_level",
                "value_text": "+0.30",
                "evidence_source": {"text": "FFL +0.30"},
                "explicit_incompatible_candidate_ids": [],
            },
        ],
    }


class TestAssociationGapAnalysis(unittest.TestCase):

    def test_classifies_all_gap_types(self):
        result = analyze_association_gaps(make_input())

        statuses = {
            item["evidence_id"]: item["gap_status"]
            for item in result["evidence_gaps"]
        }

        self.assertEqual(
            statuses["evidence-1"],
            "compatible_geometry_missing_explicit_reference",
        )
        self.assertEqual(
            statuses["evidence-2"],
            "no_semantically_compatible_geometry",
        )
        self.assertEqual(
            statuses["evidence-3"],
            "no_geometry_on_page",
        )

    def test_preserves_safety_restrictions(self):
        result = analyze_association_gaps(make_input())

        self.assertEqual(result["summary"]["approved_count"], 0)
        self.assertEqual(
            result["summary"]["geometry_generation_allowed_count"], 0
        )

        for item in result["evidence_gaps"]:
            self.assertTrue(item["verification_required"])
            self.assertEqual(item["approval_status"], "unapproved")
            self.assertFalse(item["geometry_generation_allowed"])

    def test_counts_pages_with_and_without_geometry(self):
        result = analyze_association_gaps(make_input())

        self.assertEqual(
            result["summary"]["evidence_pages_with_geometry"], 2
        )
        self.assertEqual(
            result["summary"]["evidence_pages_without_geometry"], 1
        )

    def test_rejects_wrong_input_stage(self):
        data = make_input()
        data["stage"] = "5.2.16"

        with self.assertRaises(ValueError):
            analyze_association_gaps(data)

    def test_rejects_duplicate_evidence_ids(self):
        data = make_input()
        data["evidence_associations"].append(
            dict(data["evidence_associations"][0])
        )

        with self.assertRaises(ValueError):
            analyze_association_gaps(data)

    def test_rejects_invalid_geometry_page(self):
        data = make_input()
        data["geometry_candidates"][0]["page_number"] = 0

        with self.assertRaises(ValueError):
            analyze_association_gaps(data)

    def test_explicit_incompatible_reference_is_reported(self):
        data = make_input()
        data["evidence_associations"][0][
            "explicit_incompatible_candidate_ids"
        ] = ["wall-1"]

        result = analyze_association_gaps(data)

        self.assertEqual(
            result["evidence_gaps"][0]["gap_status"],
            "explicit_reference_incompatible",
        )


if __name__ == "__main__":
    unittest.main()
