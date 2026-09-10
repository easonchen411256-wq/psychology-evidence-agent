import unittest

import httpx

from psychology_evidence_agent.adapters.fulltext.europe_pmc import EuropePmcAdapter
from psychology_evidence_agent.adapters.fulltext.unpaywall import UnpaywallAdapter
from psychology_evidence_agent.adapters.http.client import RetryPolicy
from psychology_evidence_agent.domain.errors import (
    ExternalRateLimitError,
    ExternalResponseError,
    ExternalTimeoutError,
)
from psychology_evidence_agent.domain.paper import Paper


class FullTextAdapterTests(unittest.TestCase):
    def test_unpaywall_maps_best_location(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "best_oa_location": {
                        "url_for_pdf": "https://oa.test/paper.pdf",
                        "license": "cc-by",
                    }
                },
            )
        )
        with httpx.Client(transport=transport) as client:
            result = UnpaywallAdapter(client).lookup(
                paper=Paper(paper_id="W1", doi="10/example", title="Paper"), email="a@example.org"
            )
        self.assertEqual(result[0].access_status, "open_pdf_available")
        self.assertEqual(result[0].open_access_license, "cc-by")

    def test_unpaywall_missing_doi_does_not_request(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
            self.assertEqual(
                UnpaywallAdapter(client).lookup(paper=Paper(paper_id="W1"), email="x"), []
            )

    def test_europe_pmc_maps_open_pdf_and_empty_result(self):
        payload = {
            "resultList": {"result": [{"pmcid": "PMC123", "isOpenAccess": "Y", "hasPDF": "Y"}]}
        }
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            result = EuropePmcAdapter(client).lookup(paper=Paper(paper_id="W1", doi="10/example"))
        self.assertEqual(result[0].retrieval_source, "europe_pmc")
        self.assertIn("pmc123", result[0].open_access_pdf_url)

        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"resultList": {"result": []}})
            )
        ) as client:
            self.assertEqual(
                EuropePmcAdapter(client).lookup(paper=Paper(paper_id="W1", doi="10/example")), []
            )

    def test_provider_http_errors_are_project_errors(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
            with self.assertRaises(ExternalRateLimitError):
                UnpaywallAdapter(client).lookup(
                    paper=Paper(paper_id="W1", doi="10/example"), email="x"
                )
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad"))
        ) as client:
            with self.assertRaises(ExternalResponseError):
                EuropePmcAdapter(client).lookup(paper=Paper(paper_id="W1", doi="10/example"))

    def test_unpaywall_timeout_is_retried_and_mapped(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ExternalTimeoutError):
                UnpaywallAdapter(client, retry_policy=RetryPolicy(2), sleep=lambda _: None).lookup(
                    paper=Paper(paper_id="W1", doi="10/example"), email="x"
                )
        self.assertEqual(calls, 2)

    def test_europe_pmc_503_is_retried_and_404_is_not(self):
        calls = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                503 if calls == 1 else 200,
                json={"resultList": {"result": []}},
            )

        with httpx.Client(transport=httpx.MockTransport(retry_handler)) as client:
            self.assertEqual(
                EuropePmcAdapter(client, retry_policy=RetryPolicy(2), sleep=lambda _: None).lookup(
                    paper=Paper(paper_id="W1", doi="10/example")
                ),
                [],
            )
        self.assertEqual(calls, 2)

        calls = 0

        def not_found_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(not_found_handler)) as client:
            with self.assertRaises(ExternalResponseError):
                EuropePmcAdapter(client, sleep=lambda _: None).lookup(
                    paper=Paper(paper_id="W1", doi="10/example")
                )
        self.assertEqual(calls, 1)
