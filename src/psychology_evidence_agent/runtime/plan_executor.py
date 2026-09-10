"""Execute one policy-approved agent plan step."""

from __future__ import annotations

from ..domain.agent import PlanStep, ToolResult
from ..domain.run import ResearchRun
from ..ports.agent_tools import AgentToolRegistry


class PlanExecutor:
    def __init__(self, registry: AgentToolRegistry) -> None:
        self._registry = registry

    def execute(self, step: PlanStep, run: ResearchRun) -> ToolResult:
        return self._registry.get(step.tool_name).execute(run, step.arguments)
