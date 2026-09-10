"""Deterministic interpretation of tool outcomes."""

from __future__ import annotations

from ..domain.agent import AgentDecisionType, CompletionAssessment, ToolResult
from ..domain.enums import RunStage
from ..domain.run import ResearchRun


class OutcomeEvaluator:
    def assess(self, result: ToolResult, run: ResearchRun) -> CompletionAssessment:
        if result.blocked:
            return CompletionAssessment(
                decision=AgentDecisionType.WAIT,
                reason="The tool requested a Human Gate decision.",
            )
        if result.success and result.replan_required:
            return CompletionAssessment(
                decision=AgentDecisionType.REPLAN,
                reason="The tool completed its assessment but requested a bounded re-plan.",
            )
        if result.success and run.stage is RunStage.DRAFTING:
            return CompletionAssessment(
                decision=AgentDecisionType.COMPLETE,
                reason="The final review draft stage completed.",
            )
        if result.success:
            return CompletionAssessment(
                decision=AgentDecisionType.CONTINUE,
                reason="The current tool completed; continue with the next stage.",
            )
        if result.retryable:
            return CompletionAssessment(
                decision=AgentDecisionType.RETRY,
                reason="The tool reported a retryable failure.",
            )
        return CompletionAssessment(
            decision=AgentDecisionType.FAIL,
            reason="The tool reported a non-retryable failure.",
        )
