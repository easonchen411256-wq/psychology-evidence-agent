"""Unpaywall-specific public-access metadata adapter."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

import httpx

from ...domain.fulltext import FullTextCandidate
from ...domain.paper import Paper
from ..http.client import HttpJsonClient, RetryPolicy
from ._mapping import candidate

UNPAYWALL_URL = "https://api.unpaywall.org/v2"


class UnpaywallAdapter:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        timeout_seconds: float = 30,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=timeout_seconds, headers={"User-Agent": "psychology-evidence-agent/0.1"}
        )
        if sleep is None:
            self._http = HttpJsonClient(self._client, retry_policy=retry_policy)
        else:
            self._http = HttpJsonClient(self._client, retry_policy=retry_policy, sleep=sleep)

    def lookup(self, *, paper: Paper, email: str) -> list[FullTextCandidate]:
        if not paper.doi or not email.strip():
            return []
        payload = self._http.get_json(
            f"{UNPAYWALL_URL}/{quote(paper.doi, safe='')}?email={quote(email.strip(), safe='')}"
        )
        locations = [payload.get("best_oa_location"), *(payload.get("oa_locations") or [])]
        results: list[FullTextCandidate] = []
        seen: set[tuple[str, str]] = set()
        for location in locations:
            if not isinstance(location, dict):
                continue
            pdf_url = str(location.get("url_for_pdf") or "")
            landing_url = str(location.get("url") or "")
            if not (pdf_url or landing_url) or (pdf_url, landing_url) in seen:
                continue
            seen.add((pdf_url, landing_url))
            results.append(
                candidate(
                    paper,
                    source="unpaywall",
                    pdf_url=pdf_url,
                    landing_url=landing_url,
                    license_name=str(location.get("license") or ""),
                    note=str(
                        location.get("host_type") or "legal open location reported by Unpaywall"
                    ),
                )
            )
        return results
