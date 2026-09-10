"""Compatibility adapter around existing OpenAlex, Unpaywall, and Europe PMC lookups."""

from __future__ import annotations

from ...domain.errors import ExternalServiceError, ExternalUnavailableError
from ...domain.fulltext import FullTextCandidate
from ...domain.paper import Paper
from ...open_access_fulltext import (
    classify_access,
    select_best_access_option,
)
from ..literature.openalex import OpenAlexAdapter
from .europe_pmc import EuropePmcAdapter
from .unpaywall import UnpaywallAdapter


class OpenAccessAdapter:
    """Deprecated compatibility facade over the independent OA provider adapters."""

    def __init__(self, unpaywall=None, europe_pmc=None, metadata=None) -> None:
        self._unpaywall = unpaywall or UnpaywallAdapter()
        self._europe_pmc = europe_pmc or EuropePmcAdapter()
        self._metadata = metadata or OpenAlexAdapter()

    def discover(self, *, paper_id: str, unpaywall_email: str) -> FullTextCandidate:
        try:
            return self.discover_for_paper(
                paper=self._metadata.get_work(paper_id), unpaywall_email=unpaywall_email
            )
        except (OSError, ValueError) as error:
            raise ExternalUnavailableError("Open-access metadata lookup failed.") from error
        except Exception as error:
            raise ExternalUnavailableError("Open-access metadata lookup failed.") from error

    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate:
        try:
            options = [classify_access(paper.model_dump(mode="json"))]
            failures: list[dict[str, str]] = []
            for source, lookup in (
                ("unpaywall", lambda: self._unpaywall.lookup(paper=paper, email=unpaywall_email)),
                ("europe_pmc", lambda: self._europe_pmc.lookup(paper=paper)),
            ):
                try:
                    options.extend(item.model_dump(mode="json") for item in lookup())
                except ExternalServiceError as error:
                    failures.append({"source": source, "error": str(error)[:300]})
            selected = dict(select_best_access_option(options))
            selected["access_options"] = options
            selected["source_failures"] = failures
            return FullTextCandidate.model_validate(selected)
        except (OSError, ValueError) as error:
            raise ExternalUnavailableError("Open-access metadata lookup failed.") from error
        except Exception as error:
            raise ExternalUnavailableError("Open-access metadata lookup failed.") from error
