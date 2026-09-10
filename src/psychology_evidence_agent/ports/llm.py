from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class StructuredOutputPort(Protocol):
    def generate(
        self, *, task_instruction: str, stdin_payload: str, schema_path: Path
    ) -> dict[str, Any]: ...
