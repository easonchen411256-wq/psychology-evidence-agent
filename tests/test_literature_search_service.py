import unittest

from psychology_evidence_agent.domain.errors import ExternalUnavailableError, LiteratureSearchError
from psychology_evidence_agent.domain.fulltext import FullTextCandidate
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.services.fulltext import FullTextService
from psychology_evidence_agent.services.literature_search import LiteratureSearchService


class FakeLiteratureSource:
    def __init__(self, papers: list[Paper] | None = None, error: Exception | None = None) -> None:
        self.papers = papers or []
        self.error = error

    def search(self, **_kwargs) -> list[Paper]:
        if self.error:
            raise self.error
        return self.papers


class LiteratureSearchServiceTests(unittest.TestCase):
    def test_deduplicates_provider_results_without_provider_payloads(self):
        service = LiteratureSearchService(
            FakeLiteratureSource(
                [
                    Paper(paper_id="W1", doi="10/example", matched_queries=["a"], query_coverage=1),
                    Paper(
                        paper_id="W2",
                        doi="10/example",
                        abstract="Abstract",
                        matched_queries=["b"],
                        query_coverage=1,
                    ),
                ]
            )
        )
        papers = service.search(query_id="a", query="test", year_from=2020, per_page=5)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].matched_queries, ["a", "b"])
        self.assertEqual(papers[0].abstract, "Abstract")

    def test_maps_external_error_to_service_error(self):
        service = LiteratureSearchService(FakeLiteratureSource(error=ExternalUnavailableError()))
        with self.assertRaises(LiteratureSearchError):
            service.search(query_id="a", query="test", year_from=2020, per_page=5)


class FakeOpenAccessDiscovery:
    def discover(self, **_kwargs) -> FullTextCandidate:
        raise AssertionError("This test exercises caller-supplied metadata only.")

    def discover_for_paper(self, *, paper: Paper, **_kwargs) -> FullTextCandidate:
        return FullTextCandidate(
            retrieval_source="openalex",
            paper_id=paper.paper_id,
            is_open_access=paper.is_open_access,
            access_status="manual_access_needed",
            next_step="Use an authorized access route.",
        )


class FullTextServiceTests(unittest.TestCase):
    def test_uses_typed_paper_without_provider_or_network_details(self):
        service = FullTextService(FakeOpenAccessDiscovery())
        candidate = service.discover_for_paper(
            paper=Paper(paper_id="W1", title="A paper"), unpaywall_email=""
        )
        self.assertEqual(candidate.paper_id, "W1")
