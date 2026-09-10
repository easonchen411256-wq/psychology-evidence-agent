from __future__ import annotations

from pathlib import Path

from ...paper_input import read_paper_text


class PdfReaderAdapter:
    def read_text(self, path: Path, *, max_chars: int, allow_large_input: bool) -> str:
        return read_paper_text(path, max_chars=max_chars, allow_large_input=allow_large_input)
