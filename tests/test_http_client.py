import unittest

import httpx

from psychology_evidence_agent.adapters.http.client import HttpJsonClient, RetryPolicy
from psychology_evidence_agent.domain.errors import (
    ExternalRateLimitError,
    ExternalResponseError,
    ExternalUnavailableError,
)


class HttpClientTests(unittest.TestCase):
    def test_retries_503_then_succeeds_without_sleeping(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503 if calls < 2 else 200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = HttpJsonClient(
                client, retry_policy=RetryPolicy(3, 1), sleep=lambda _: None
            ).get_json("https://example.test")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, 2)

    def test_404_is_not_retried(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ExternalResponseError):
                HttpJsonClient(client, sleep=lambda _: None).get_json("https://example.test")
        self.assertEqual(calls, 1)

    def test_retry_budget_is_bounded(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            with self.assertRaises(ExternalUnavailableError):
                HttpJsonClient(client, retry_policy=RetryPolicy(3), sleep=lambda _: None).get_json(
                    "https://example.test"
                )

    def test_rate_limit_honors_retry_after_and_fails_at_budget(self):
        delays: list[float] = []
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "2"})
            )
        ) as client:
            with self.assertRaises(ExternalRateLimitError):
                HttpJsonClient(client, retry_policy=RetryPolicy(2), sleep=delays.append).get_json(
                    "https://example.test"
                )
        self.assertEqual(delays, [2.0])
