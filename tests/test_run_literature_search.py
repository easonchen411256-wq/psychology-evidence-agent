import unittest

from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.run_literature_search import select_screening_candidates


class LiteratureSearchSelectionTests(unittest.TestCase):
    def test_prioritizes_multi_query_coverage_and_reports_remainder(self):
        candidates = [
            Paper.model_validate(
                {
                    "paper_id": "one",
                    "title": "One",
                    "matched_queries": ["a"],
                    "query_coverage": 1,
                    "abstract": "yes",
                    "cited_by_count": 100,
                }
            ),
            Paper.model_validate(
                {
                    "paper_id": "two",
                    "title": "Two",
                    "matched_queries": ["a", "b"],
                    "query_coverage": 2,
                    "abstract": "yes",
                    "cited_by_count": 1,
                }
            ),
            Paper.model_validate(
                {
                    "paper_id": "three",
                    "title": "Three",
                    "matched_queries": ["a"],
                    "query_coverage": 1,
                    "abstract": "yes",
                    "cited_by_count": 50,
                }
            ),
        ]
        shortlist, unscreened = select_screening_candidates(candidates, 2, False)
        self.assertEqual([item.paper_id for item in shortlist], ["two", "one"])
        self.assertEqual([item.paper_id for item in unscreened], ["three"])

    def test_screen_all_has_no_unscreened_remainder(self):
        candidates = [
            Paper.model_validate(
                {
                    "paper_id": "one",
                    "title": "One",
                    "matched_queries": ["a"],
                    "abstract": "",
                    "cited_by_count": 0,
                }
            )
        ]
        shortlist, unscreened = select_screening_candidates(candidates, 1, True)
        self.assertEqual(len(shortlist), 1)
        self.assertEqual(unscreened, [])


if __name__ == "__main__":
    unittest.main()
