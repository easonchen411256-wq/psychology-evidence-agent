"""Append-only event trace boundary."""

from __future__ import annotations

from typing import Protocol

from ..domain.agent import AgentEvent


class AgentEventStore(Protocol):
    def append(self, event: AgentEvent) -> None: ...

    def read(self) -> list[AgentEvent]: ...
