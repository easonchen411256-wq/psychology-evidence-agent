from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain.fulltext import FullTextCandidate
from ..domain.paper import Paper


class OpenAccessDiscoveryPort(Protocol):
    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate: ...

    def discover(self, *, paper_id: str, unpaywall_email: str) -> FullTextCandidate: ...


class UnpaywallPort(Protocol):
    def lookup(self, *, paper: Paper, email: str) -> list[FullTextCandidate]: ...


class FullTextRepositoryPort(Protocol):
    def lookup(self, *, paper: Paper) -> list[FullTextCandidate]: ...


class OpenPdfDownloaderPort(Protocol):
    """Download an explicitly confirmed public PDF into an injected directory."""

    def download(
        self,
        candidate: FullTextCandidate,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> Path: ...
