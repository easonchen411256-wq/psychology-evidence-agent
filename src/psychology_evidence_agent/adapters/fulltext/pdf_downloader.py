"""Concrete adapter for downloading explicitly confirmed open PDFs."""

from __future__ import annotations

from pathlib import Path

from ...domain.fulltext import FullTextCandidate
from ...open_access_fulltext import download_open_pdf


class OpenPdfDownloaderAdapter:
    def download(
        self,
        candidate: FullTextCandidate,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        return download_open_pdf(candidate.model_dump(mode="json"), output_dir, overwrite=overwrite)
