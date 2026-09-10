import unittest

import httpx

from psychology_evidence_agent.adapters.literature.openalex import OpenAlexAdapter
from psychology_evidence_agent.domain.errors import ExternalRateLimitError, ExternalResponseError


class OpenAlexAdapterTests(unittest.TestCase):
    def test_maps_raw_work_to_canonical_paper(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "title": "Example",
                            "publication_year": 2024,
                            "authorships": [{"author": {"display_name": "Author"}}],
                        }
                    ]
                },
            )
        )
        with httpx.Client(transport=transport) as client:
            papers = OpenAlexAdapter(client).search(
                query_id="q", query="example", year_from=2020, per_page=5
            )
        self.assertEqual(papers[0].paper_id, "https://openalex.org/W1")
        self.assertEqual(papers[0].authors, ["Author"])

    def test_search_includes_bounded_publication_year_filter(self):
        captured: dict[str, str] = {}

        def handler(request):
            captured["filter"] = request.url.params["filter"]
            return httpx.Response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            OpenAlexAdapter(client).search(
                query_id="q", query="example", year_from=2015, year_to=2022, per_page=5
            )

        self.assertEqual(
            captured["filter"],
            "from_publication_date:2015-01-01,to_publication_date:2022-12-31,type:article",
        )

    def test_maps_rate_limit_and_malformed_json(self):
        limited = httpx.MockTransport(lambda request: httpx.Response(429))
        with httpx.Client(transport=limited) as client:
            with self.assertRaises(ExternalRateLimitError):
                OpenAlexAdapter(client).search(
                    query_id="q", query="example", year_from=2020, per_page=5
                )
        malformed = httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
        with httpx.Client(transport=malformed) as client:
            with self.assertRaises(ExternalResponseError):
                OpenAlexAdapter(client).search(
                    query_id="q", query="example", year_from=2020, per_page=5
                )
