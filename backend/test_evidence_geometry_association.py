
import unittest

from app.services.evidence_geometry_association import (
    analyze_evidence_geometry_associations,
)


PROJECT_ID = "test-project"


def make_recognition(candidates):
    return {
        "stage": "5.1.6",
        "project_id": PROJECT_ID,
        "drawings": [
            {
                "drawing_id": "drawing-1",
                "pages": [
                    {
                        "page_number": 1,
                        "candidates": candidates,
                    }
                ],
            }
        ],
    }


def make_evidence(
    evidence_id="evidence-1",
    semantic_type="wall_thickness_dimension",
    drawing_id="drawing-1",
    page_number=1,
):
    return {
        "stage": "5.2.12",
        "project_id": PROJECT_ID,
        "candidates": [
            {
                "project_id": PROJECT_ID,
                "evidence_id": evidence_id,
                "drawing_id": drawing_id,
                "page_number": page_number,
                "semantic_type": semantic_type,
                "value_text": "200",
                "source": {"text": "WALL THK = 200"},
            }
        ],
    }


def make_wall_candidate(evidence_ids=None):
    candidate = {
        "element_type": "wall_geometry_supported_candidate",
        "geometry_type": "parallel_horizontal_lines",
        "bbox_pdf_points": [100, 100, 300, 110],
        "source": {"line_a": [10, 0], "line_b": [11, 0]},
    }

    if evidence_ids is not None:
        candidate["evidence_ids"] = evidence_ids

    return candidate


class TestEvidenceGeometryAssociation(unittest.TestCase):

    def test_explicit_compatible_reference_associates(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"])
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(),
        )

        association = result["evidence_associations"][0]

        self.assertEqual(
            association["association_status"],
            "direct_association",
        )
        self.assertEqual(
            result["summary"]["direct_association_count"],
            1,
        )
        self.assertFalse(association["geometry_generation_allowed"])

    def test_no_explicit_reference_remains_unmatched(self):
        recognition = make_recognition([
            make_wall_candidate()
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(),
        )

        self.assertEqual(
            result["evidence_associations"][0]["association_status"],
            "unmatched",
        )

    def test_incompatible_semantic_type_is_not_associated(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"])
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(semantic_type="structural_slab_level"),
        )

        association = result["evidence_associations"][0]

        self.assertEqual(association["association_status"], "unmatched")
        self.assertEqual(
            len(association["explicit_incompatible_candidate_ids"]),
            1,
        )

    def test_multiple_compatible_candidates_are_ambiguous(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"]),
            make_wall_candidate(["evidence-1"]),
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(),
        )

        self.assertEqual(
            result["evidence_associations"][0]["association_status"],
            "ambiguous",
        )

    def test_wrong_drawing_does_not_associate(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"])
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(drawing_id="another-drawing"),
        )

        self.assertEqual(
            result["evidence_associations"][0]["association_status"],
            "unmatched",
        )

    def test_wrong_page_does_not_associate(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"])
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(page_number=2),
        )

        self.assertEqual(
            result["evidence_associations"][0]["association_status"],
            "unmatched",
        )

    def test_project_mismatch_is_rejected(self):
        recognition = make_recognition([])
        evidence = make_evidence()
        evidence["project_id"] = "different-project"

        with self.assertRaises(ValueError):
            analyze_evidence_geometry_associations(
                recognition,
                evidence,
            )

    def test_duplicate_evidence_ids_are_rejected(self):
        recognition = make_recognition([])
        evidence = make_evidence()

        evidence["candidates"].append(
            dict(evidence["candidates"][0])
        )

        with self.assertRaises(ValueError):
            analyze_evidence_geometry_associations(
                recognition,
                evidence,
            )

    def test_unsupported_evidence_does_not_generate_geometry(self):
        recognition = make_recognition([
            make_wall_candidate(["evidence-1"])
        ])

        result = analyze_evidence_geometry_associations(
            recognition,
            make_evidence(),
        )

        self.assertEqual(
            result["summary"]["geometry_generation_allowed_count"],
            0,
        )
        self.assertEqual(
            result["summary"]["approved_association_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
