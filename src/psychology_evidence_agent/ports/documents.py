from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DocumentReaderPort(Protocol):
    def read_text(self, path: Path, *, max_chars: int, allow_large_input: bool) -> str: ...
