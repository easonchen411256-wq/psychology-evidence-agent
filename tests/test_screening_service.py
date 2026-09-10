import unittest
from typing import Any

from psychology_evidence_agent.domain.errors import StructuredOutputError
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.services.screening import ScreeningService


class FakeStructuredOutput:
    def generate(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "screened_papers": [
                {
                    "paper_id": "W1",
                    "relevance_score": 8,
                    "evidence_level": "direct",
                    "subtopic": "gait",
                    "rationale": "Relevant",
                    "human_review_note": "",
                }
            ]
        }


class ScreeningServiceTests(unittest.TestCase):
    def test_uses_structured_output_port_without_knowing_codex(self):
        result = ScreeningService(FakeStructuredOutput()).screen(
            [Paper(paper_id="W1", title="A paper")], "research question"
        )
        self.assertEqual(result[0].paper_id, "W1")

    def test_rejects_invalid_batch_size_and_duplicate_input_ids(self):
        service = ScreeningService(FakeStructuredOutput())
        with self.assertRaises(ValueError):
            service.screen([], "research question", batch_size=0)
        with self.assertRaises(StructuredOutputError):
            service.screen(
                [Paper(paper_id="W1", title="A"), Paper(paper_id="W1", title="Duplicate")],
                "research question",
            )

    def test_rejects_duplicate_model_output(self):
        class DuplicateOutput:
            def generate(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "screened_papers": [
                        {
                            "paper_id": "W1",
                            "relevance_score": 8,
                            "evidence_level": "direct",
                            "subtopic": "gait",
                            "rationale": "Relevant",
                            "human_review_note": "",
                        },
                        {
                            "paper_id": "W1",
                            "relevance_score": 7,
                            "evidence_level": "direct",
                            "subtopic": "gait",
                            "rationale": "Duplicate",
                            "human_review_note": "",
                        },
                    ]
                }

        with self.assertRaises(StructuredOutputError):
            ScreeningService(DuplicateOutput()).screen(
                [Paper(paper_id="W1", title="A")], "research question"
            )
