"""Capability interfaces exposed to the bounded agent controller."""

from __future__ import annotations

from typing import Protocol

from ..domain.agent import PlanArgumentValue, ToolDescriptor, ToolResult
from ..domain.run import ResearchRun


class AgentTool(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult: ...


class AgentToolRegistry(Protocol):
    def descriptors(self) -> list[ToolDescriptor]: ...

    def get(self, name: str) -> AgentTool: ...
