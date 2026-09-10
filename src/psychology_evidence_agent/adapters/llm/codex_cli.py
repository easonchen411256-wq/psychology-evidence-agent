"""Read-only Codex CLI adapter preserving existing command safety controls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...codex_cli_client import run_structured_json
from ...domain.errors import StructuredOutputError


class CodexCliAdapter:
    def generate(
        self, *, task_instruction: str, stdin_payload: str, schema_path: Path
    ) -> dict[str, Any]:
        try:
            return run_structured_json(
                task_instruction=task_instruction,
                stdin_payload=stdin_payload,
                schema_path=schema_path,
            )
        except Exception as error:
            raise StructuredOutputError("Structured-output provider failed.") from error
