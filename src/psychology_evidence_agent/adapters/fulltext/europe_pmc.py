"""Europe PMC full-text metadata adapter."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from ...domain.fulltext import FullTextCandidate
from ...domain.paper import Paper
from ..http.client import HttpJsonClient, RetryPolicy
from ._mapping import candidate

EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcAdapter:
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

    def lookup(self, *, paper: Paper) -> list[FullTextCandidate]:
        if not paper.doi:
            return []
        payload = self._http.get_json(
            EUROPE_PMC_SEARCH_URL,
            params={"query": f"DOI:{paper.doi}", "format": "json", "pageSize": 1},
        )
        results = payload.get("resultList", {}).get("result", [])
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return []
        result = results[0]
        pmcid = str(result.get("pmcid") or "").upper()
        if not pmcid:
            return []
        is_oa = str(result.get("isOpenAccess") or "").upper() in {"Y", "TRUE"}
        has_pdf = str(result.get("hasPDF") or "").upper() in {"Y", "TRUE"}
        pdf_url = (
            f"https://europepmc.org/articles/{pmcid.lower()}?pdf=render"
            if is_oa and has_pdf
            else ""
        )
        return [
            candidate(
                paper,
                source="europe_pmc",
                pdf_url=pdf_url,
                landing_url=f"https://europepmc.org/article/PMC/{pmcid}",
                license_name=str(result.get("license") or ""),
                note=f"PMCID {pmcid}; Europe PMC open-access metadata={is_oa}, PDF metadata={has_pdf}",
            )
        ]
