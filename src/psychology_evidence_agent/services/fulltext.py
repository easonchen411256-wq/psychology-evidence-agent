from __future__ import annotations

from ..domain.fulltext import FullTextCandidate
from ..domain.paper import Paper
from ..ports.fulltext import OpenAccessDiscoveryPort


class FullTextService:
    """Apply the existing fixed legal-OA provider strategy through one port."""

    def __init__(self, discovery: OpenAccessDiscoveryPort) -> None:
        self._discovery = discovery

    def discover(self, *, paper_id: str, unpaywall_email: str) -> FullTextCandidate:
        return self._discovery.discover(paper_id=paper_id, unpaywall_email=unpaywall_email)

    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate:
        """Apply the fixed public-source strategy to metadata supplied by the caller."""
        return self._discovery.discover_for_paper(paper=paper, unpaywall_email=unpaywall_email)
