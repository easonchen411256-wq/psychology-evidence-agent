"""Planner boundary for proposing, but not authorizing, agent actions."""

from __future__ import annotations

from typing import Protocol

from ..domain.agent import AgentGoal, AgentObservation, AgentPlan, ToolDescriptor


class AgentPlanner(Protocol):
    def plan(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        tools: list[ToolDescriptor],
    ) -> AgentPlan: ...
