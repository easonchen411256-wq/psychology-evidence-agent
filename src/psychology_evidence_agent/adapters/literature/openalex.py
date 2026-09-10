"""OpenAlex HTTP adapter; raw provider payloads stop in this module."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ...domain.errors import ExternalResponseError
from ...domain.paper import Paper
from ..http.client import HttpJsonClient, RetryPolicy

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions = {
        position: token for token, indexes in inverted_index.items() for position in indexes
    }
    return " ".join(positions[position] for position in sorted(positions))


def _paper_from_work(work: dict[str, Any], query_id: str) -> Paper:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    authors = [
        item.get("author", {}).get("display_name", "")
        for item in work.get("authorships", [])
        if item.get("author", {}).get("display_name")
    ]
    best_oa = work.get("best_oa_location") or {}
    return Paper(
        paper_id=work.get("id", ""),
        doi=work.get("doi") or "",
        title=work.get("title") or "",
        abstract=_abstract(work.get("abstract_inverted_index")),
        year=work.get("publication_year") or "",
        venue=source.get("display_name") or "",
        authors=authors,
        cited_by_count=work.get("cited_by_count") or 0,
        openalex_url=work.get("id", ""),
        is_open_access=bool((work.get("open_access") or {}).get("is_oa")),
        open_access_url=best_oa.get("pdf_url") or best_oa.get("landing_page_url") or "",
        open_access_pdf_url=best_oa.get("pdf_url") or "",
        open_access_license=best_oa.get("license") or "",
        matched_queries=[query_id],
        query_coverage=1,
    )


class OpenAlexAdapter:
    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=timeout_seconds, headers={"User-Agent": "psychology-evidence-agent/0.1"}
        )
        self._http = HttpJsonClient(self._client, retry_policy=retry_policy)

    def search(
        self,
        *,
        query_id: str,
        query: str,
        year_from: int,
        per_page: int,
        year_to: int | None = None,
    ) -> list[Paper]:
        date_filters = [f"from_publication_date:{year_from}-01-01"]
        if year_to is not None:
            date_filters.append(f"to_publication_date:{year_to}-12-31")
        date_filters.append("type:article")
        params: dict[str, str | int] = {
            "search": query,
            "filter": ",".join(date_filters),
            "per-page": per_page,
            "select": "id,doi,title,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,open_access,best_oa_location",
        }
        payload = self._http.get_json(OPENALEX_WORKS_URL, params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ExternalResponseError("OpenAlex returned an unexpected JSON shape.")
        return [
            _paper_from_work(work, query_id)
            for work in payload["results"]
            if isinstance(work, dict) and work.get("title")
        ]

    def get_work(self, paper_id: str) -> Paper:
        identifier = quote(paper_id.rstrip("/").rsplit("/", 1)[-1], safe="")
        payload = self._http.get_json(f"{OPENALEX_WORKS_URL}/{identifier}")
        if not payload.get("id"):
            raise ExternalResponseError("OpenAlex returned an invalid work.")
        return _paper_from_work(payload, "open_access_lookup")
