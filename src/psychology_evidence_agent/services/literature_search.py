from __future__ import annotations

from collections.abc import Sequence

from ..domain.errors import ExternalServiceError, LiteratureSearchError
from ..domain.paper import Paper
from ..ports.literature import LiteratureSearchPort


class LiteratureSearchService:
    def __init__(self, source: LiteratureSearchPort) -> None:
        self._source = source

    def search(
        self,
        *,
        query_id: str,
        query: str,
        year_from: int,
        per_page: int,
        year_to: int | None = None,
    ) -> list[Paper]:
        try:
            if year_to is not None:
                papers = self._source.search(
                    query_id=query_id,
                    query=query,
                    year_from=year_from,
                    per_page=per_page,
                    year_to=year_to,
                )
            else:
                papers = self._source.search(
                    query_id=query_id,
                    query=query,
                    year_from=year_from,
                    per_page=per_page,
                )
            return deduplicate_papers(papers)
        except ExternalServiceError as error:
            raise LiteratureSearchError("Literature metadata search failed.") from error


def select_screening_candidates(
    candidates: list[Paper], max_screen: int, screen_all: bool
) -> tuple[list[Paper], list[Paper]]:
    """Use multi-query coverage ordering and retain an explicit unscreened remainder."""
    ranked = sorted(
        candidates,
        key=lambda item: (
            -item.query_coverage,
            not bool(item.abstract),
            -item.cited_by_count,
            item.title.casefold(),
        ),
    )
    return (ranked, []) if screen_all else (ranked[:max_screen], ranked[max_screen:])


def deduplicate_papers(papers: Sequence[Paper]) -> list[Paper]:
    """Provider-neutral duplicate merge, preserving existing ranking semantics."""
    by_key: dict[str, Paper] = {}
    for paper in papers:
        key = (
            paper.doi.casefold()
            or paper.paper_id
            or "".join(character for character in paper.title.casefold() if character.isalnum())
        )
        if not key:
            continue
        if key not in by_key:
            by_key[key] = paper.model_copy(deep=True)
            continue
        existing = by_key[key]
        existing.matched_queries = sorted(set(existing.matched_queries + paper.matched_queries))
        existing.query_coverage = len(existing.matched_queries)
        if not existing.abstract and paper.abstract:
            existing.abstract = paper.abstract
    return sorted(
        by_key.values(),
        key=lambda item: (
            -item.query_coverage,
            not bool(item.abstract),
            -item.cited_by_count,
            item.title.casefold(),
        ),
    )
