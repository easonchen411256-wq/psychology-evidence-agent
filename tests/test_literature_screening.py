import unittest

from psychology_evidence_agent.literature_screening import screen_papers


class LiteratureScreeningTests(unittest.TestCase):
    def test_sends_all_candidate_ids_and_sorts_scores(self):
        papers = [
            {
                "paper_id": "p1",
                "title": "First",
                "abstract": "A",
                "year": 2020,
                "venue": "J",
                "doi": "",
            },
            {
                "paper_id": "p2",
                "title": "Second",
                "abstract": "B",
                "year": 2021,
                "venue": "J",
                "doi": "",
            },
        ]

        def fake_runner(**kwargs):
            self.assertIn("First", kwargs["stdin_payload"])
            return {
                "screened_papers": [
                    {
                        "paper_id": "p1",
                        "relevance_score": 6,
                        "evidence_level": "adjacent",
                        "subtopic": "步态",
                        "rationale": "r",
                        "human_review_note": "h",
                    },
                    {
                        "paper_id": "p2",
                        "relevance_score": 9,
                        "evidence_level": "direct",
                        "subtopic": "HRV",
                        "rationale": "r",
                        "human_review_note": "h",
                    },
                ]
            }

        result = screen_papers(papers, "question", structured_runner=fake_runner)
        self.assertEqual([item.paper_id for item in result], ["p2", "p1"])
