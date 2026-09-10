import unittest
from typing import Any

from psychology_evidence_agent.domain.synthesis import EvidenceSynthesis
from psychology_evidence_agent.services.review_draft import ReviewDraftService


def synthesis_fixture() -> EvidenceSynthesis:
    return EvidenceSynthesis.model_validate(
        {
            "summary": {
                "input_count": 1,
                "included_count": 1,
                "skipped_count": 0,
                "role_counts": {"review_required": 1},
            },
            "cards": [
                {
                    "file": "card.json",
                    "source": {"title": "Paper"},
                    "material_completeness": "complete",
                    "study": {},
                    "signals": {},
                    "evidence_role_candidate": "review_required",
                    "findings": [{"finding": "Finding", "evidence_location": "Results"}],
                    "limitations": [],
                    "claim_boundaries": {},
                    "human_review_items": [],
                }
            ],
            "skipped_files": [],
        }
    )


class FakeStructuredOutput:
    def generate(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "title": "Draft",
            "research_question": "Question",
            "evidence_scope": "Scope",
            "sections": [
                {
                    "heading": heading,
                    "paragraph": "Text",
                    "supporting_card_files": ["card.json"] if index == 0 else [],
                    "claims": (
                        [
                            {
                                "claim": "Claim",
                                "evidence": [
                                    {
                                        "evidence_card_file": "card.json",
                                        "finding": "Finding",
                                        "evidence_location": "Results",
                                    }
                                ],
                            }
                        ]
                        if index == 0
                        else []
                    ),
                    "caveat": "Caution",
                }
                for index, heading in enumerate(("A", "B", "C"))
            ],
            "evidence_gaps": [],
            "human_review_items": [],
        }


class ReviewDraftServiceTests(unittest.TestCase):
    def test_preserves_traceable_evidence_reference(self):
        draft = ReviewDraftService(FakeStructuredOutput()).generate("Question", synthesis_fixture())
        self.assertEqual(draft.sections[0].claims[0].evidence[0].evidence_card_file, "card.json")
