import unittest

from psychology_evidence_agent.evidence_card import validate_evidence_card


def valid_card() -> dict:
    return {
        "source": {
            "title": "Example title",
            "authors": ["Example Author"],
            "year": "2026",
            "journal": "Example Journal",
            "doi_or_url": "未报告",
        },
        "material_completeness": "complete",
        "study": {
            "research_question": "Example question",
            "design": "cross-sectional",
            "sample": "100 adults",
            "measures": ["stress", "sleep"],
            "analysis": "regression",
        },
        "findings": [],
        "limitations": [],
        "claim_boundaries": {"supported_claims": [], "unsupported_claims": []},
        "human_review_items": [],
    }


class EvidenceCardValidationTests(unittest.TestCase):
    def test_allows_association_for_cross_sectional_study(self):
        card = valid_card()
        card["claim_boundaries"]["supported_claims"] = ["Stress was associated with sleep quality."]
        self.assertEqual(validate_evidence_card(card), [])

    def test_rejects_causal_claim_for_cross_sectional_study(self):
        card = valid_card()
        card["claim_boundaries"]["supported_claims"] = ["Stress causes poor sleep quality."]
        errors = validate_evidence_card(card)
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot support", errors[0])

    def test_partial_material_requires_human_review_item(self):
        card = valid_card()
        card["material_completeness"] = "partial"
        self.assertEqual(len(validate_evidence_card(card)), 1)

    def test_rejects_missing_nested_schema_field(self):
        card = valid_card()
        del card["study"]["analysis"]
        errors = validate_evidence_card(card)
        self.assertEqual(len(errors), 1)
        self.assertIn("study", errors[0])
        self.assertIn("analysis", errors[0])

    def test_rejects_unknown_schema_field(self):
        card = valid_card()
        card["source"]["citation_status"] = "example only"
        errors = validate_evidence_card(card)
        self.assertEqual(len(errors), 1)
        self.assertIn("source", errors[0])
        self.assertIn("additional properties", errors[0].lower())

    def test_rejects_wrong_schema_type(self):
        card = valid_card()
        card["source"]["authors"] = "Example Author"
        errors = validate_evidence_card(card)
        self.assertEqual(len(errors), 1)
        self.assertIn("authors", errors[0])
        self.assertIn("array", errors[0])

    def test_rejects_causal_finding_for_cross_sectional_study(self):
        card = valid_card()
        card["findings"] = [
            {
                "finding": "Stress causes poor sleep quality.",
                "inference_strength": "causal",
                "evidence_location": "Results",
            }
        ]
        errors = validate_evidence_card(card)
        self.assertEqual(len(errors), 1)
        self.assertIn("inference strength", errors[0])


if __name__ == "__main__":
    unittest.main()
