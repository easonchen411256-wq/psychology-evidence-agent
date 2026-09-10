import unittest

from psychology_evidence_agent.review_draft import validate_review_draft


def draft_with_sources(sources, finding="Finding", location="Results"):
    return {
        "title": "Draft",
        "research_question": "Question",
        "evidence_scope": "Scope",
        "sections": [
            {
                "heading": "A",
                "paragraph": "Text",
                "supporting_card_files": sources,
                "claims": (
                    [
                        {
                            "claim": "Claim",
                            "evidence": [
                                {
                                    "evidence_card_file": source,
                                    "finding": finding,
                                    "evidence_location": location,
                                }
                                for source in sources
                            ],
                        }
                    ]
                    if sources
                    else []
                ),
                "caveat": "Caution",
            },
            {
                "heading": "B",
                "paragraph": "Text",
                "supporting_card_files": [],
                "claims": [],
                "caveat": "Caution",
            },
            {
                "heading": "C",
                "paragraph": "Text",
                "supporting_card_files": [],
                "claims": [],
                "caveat": "Caution",
            },
        ],
        "evidence_gaps": ["Gap"],
        "human_review_items": ["Review"],
    }


class ReviewDraftTests(unittest.TestCase):
    def test_allows_sources_from_input_set(self):
        self.assertEqual(
            validate_review_draft(
                draft_with_sources(["card.json"]), {"card.json": {("Finding", "Results")}}
            ),
            [],
        )

    def test_rejects_unknown_evidence_card_source(self):
        errors = validate_review_draft(
            draft_with_sources(["other.json"]), {"card.json": {("Finding", "Results")}}
        )
        self.assertTrue(any("outside the input" in error for error in errors))

    def test_rejects_finding_not_present_in_cited_card(self):
        errors = validate_review_draft(
            draft_with_sources(["card.json"], finding="Invented"),
            {"card.json": {("Finding", "Results")}},
        )
        self.assertTrue(any("does not match" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
