"""Public metadata retrieval and deduplication using the OpenAlex works API."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from .domain.paper import Paper

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions = {
        position: token for token, indexes in inverted_index.items() for position in indexes
    }
    return " ".join(positions[position] for position in sorted(positions))


def normalize_title(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold())


def paper_from_openalex(work: dict[str, Any], query_id: str) -> Paper:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    authors = [
        item.get("author", {}).get("display_name", "")
        for item in work.get("authorships", [])
        if item.get("author", {}).get("display_name")
    ]
    return Paper(
        paper_id=work.get("id", ""),
        doi=work.get("doi") or "",
        title=work.get("title") or "",
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        year=work.get("publication_year") or "",
        venue=source.get("display_name") or "",
        authors=authors,
        cited_by_count=work.get("cited_by_count") or 0,
        openalex_url=work.get("id", ""),
        is_open_access=bool((work.get("open_access") or {}).get("is_oa")),
        open_access_url=(
            (work.get("best_oa_location") or {}).get("pdf_url")
            or (work.get("best_oa_location") or {}).get("landing_page_url")
            or ""
        ),
        open_access_pdf_url=(work.get("best_oa_location") or {}).get("pdf_url") or "",
        open_access_license=(work.get("best_oa_location") or {}).get("license") or "",
        matched_queries=[query_id],
        query_coverage=1,
    )


def search_openalex(
    query_id: str,
    query: str,
    year_from: int,
    per_page: int,
    opener: Callable[..., Any] = urlopen,
) -> list[Paper]:
    params = urlencode(
        {
            "search": query,
            "filter": f"from_publication_date:{year_from}-01-01,type:article",
            "per-page": per_page,
            "select": "id,doi,title,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,open_access,best_oa_location",
        }
    )
    with opener(f"{OPENALEX_WORKS_URL}?{params}", timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [
        paper_from_openalex(work, query_id)
        for work in payload.get("results", [])
        if work.get("title")
    ]


def deduplicate_papers(papers: Sequence[Paper | dict[str, Any]]) -> list[Paper]:
    """Merge the same record across queries, preferring DOI, then OpenAlex ID, then title."""
    deduplicated: dict[str, Paper] = {}
    for item in papers:
        paper = item if isinstance(item, Paper) else Paper.model_validate(item)
        key = paper.doi.casefold() or paper.paper_id or normalize_title(paper.title)
        if not key:
            continue
        if key not in deduplicated:
            deduplicated[key] = paper.model_copy(deep=True)
            continue
        existing = deduplicated[key]
        existing.matched_queries = sorted(set(existing.matched_queries + paper.matched_queries))
        existing.query_coverage = len(existing.matched_queries)
        if not existing.abstract and paper.abstract:
            existing.abstract = paper.abstract
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            -item.query_coverage,
            not bool(item.abstract),
            -item.cited_by_count,
            item.title.casefold(),
        ),
    )
