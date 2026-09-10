import unittest

from psychology_evidence_agent.openalex_search import deduplicate_papers, reconstruct_abstract


class OpenAlexSearchTests(unittest.TestCase):
    def test_reconstructs_abstract_from_inverted_index(self):
        self.assertEqual(reconstruct_abstract({"sleep": [1], "Stress": [0]}), "Stress sleep")

    def test_deduplicates_doi_and_merges_queries(self):
        papers = [
            {
                "doi": "https://doi.org/10.1/example",
                "paper_id": "a",
                "title": "One",
                "abstract": "",
                "matched_queries": ["a"],
                "cited_by_count": 1,
            },
            {
                "doi": "https://doi.org/10.1/example",
                "paper_id": "b",
                "title": "One",
                "abstract": "Abstract",
                "matched_queries": ["b"],
                "cited_by_count": 2,
            },
        ]
        result = deduplicate_papers(papers)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].matched_queries, ["a", "b"])
        self.assertEqual(result[0].abstract, "Abstract")
        self.assertEqual(result[0].query_coverage, 2)
