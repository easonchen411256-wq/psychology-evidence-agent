"""Append-only JSONL persistence for operational agent events."""

from __future__ import annotations

import json
from pathlib import Path

from ...domain.agent import AgentEvent


class FileSystemAgentEventStore:
    """Persist only validated, redacted ``AgentEvent`` records."""

    def __init__(self, path: Path) -> None:
        self._path = path.absolute()

    def append(self, event: AgentEvent) -> None:
        existing = self.read()
        if existing and event.sequence <= existing[-1].sequence:
            raise ValueError("Agent event sequence must increase monotonically.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(event.model_dump_json() + "\n")

    def read(self) -> list[AgentEvent]:
        if not self._path.is_file():
            return []
        events: list[AgentEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(AgentEvent.model_validate(json.loads(line)))
        return events
