"""Bounded plan/observe/execute/re-plan controller for research runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..domain.agent import (
    AgentDecisionType,
    AgentEvent,
    AgentEventType,
    AgentGoal,
    AgentObservation,
    AgentPlan,
    ExecutionBudget,
    PlanStep,
    PlanStepStatus,
    ToolResult,
)
from ..domain.enums import RunStage, RunStatus
from ..domain.errors import AgentPlanRejectedError, StepExecutionError
from ..domain.run import ResearchRun, RunFailure
from ..ports.agent_tools import AgentToolRegistry
from ..ports.events import AgentEventStore
from ..ports.persistence import ResearchRunStore, RunArtifactStore
from ..ports.planning import AgentPlanner
from .outcome_evaluator import OutcomeEvaluator
from .plan_executor import PlanExecutor
from .policy_guard import PlanPolicyGuard
from .state_machine import ALLOWED_STAGE_TRANSITIONS, RunStateMachine

GOAL_ARTIFACT = "agent/goal.json"
BRIEF_ARTIFACT = "agent/research_brief.json"
PLAN_ARTIFACT = "agent/plan.json"
BUDGET_ARTIFACT = "agent/budget.json"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class AgentController:
    """Run approved tools until completion, Human Gate, budget exhaustion, or failure."""

    def __init__(
        self,
        *,
        run_store: ResearchRunStore,
        artifact_store: RunArtifactStore,
        event_store: AgentEventStore,
        state_machine: RunStateMachine,
        planner: AgentPlanner,
        registry: AgentToolRegistry,
        budget: ExecutionBudget | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._run_store = run_store
        self._artifact_store = artifact_store
        self._event_store = event_store
        self._state_machine = state_machine
        self._planner = planner
        self._registry = registry
        self._plan_executor = PlanExecutor(registry)
        self._policy_guard = PlanPolicyGuard()
        self._outcome_evaluator = OutcomeEvaluator()
        self._budget_template = budget or ExecutionBudget()
        self._cancellation_requested = cancellation_requested or (lambda: False)
        self._next_sequence = max((event.sequence for event in event_store.read()), default=0) + 1

    def run_until_blocked(self, run: ResearchRun, goal: AgentGoal) -> ResearchRun:
        self._ensure_goal(goal)
        if run.status is RunStatus.CREATED:
            self._state_machine.transition_status(run, RunStatus.RUNNING)
            self._run_store.save(run)
        if run.status is not RunStatus.RUNNING:
            return run

        budget = self._load_budget()
        plan = self._load_plan(goal)
        if plan is not None:
            self._normalize_loaded_plan(plan)
            plan = self._reconcile_completed_stage(run, plan)
            self._save_plan(plan)

        if not self._has_event(AgentEventType.GOAL_CREATED):
            self._emit(
                run,
                AgentEventType.GOAL_CREATED,
                "Agent goal accepted for bounded execution.",
                metadata={"goal_id": goal.goal_id},
            )

        while run.status is RunStatus.RUNNING:
            if self._cancel_if_requested(run, budget):
                return run
            if budget.used_steps >= budget.max_steps:
                self._fail_run(
                    run,
                    error_code="agent_budget_exhausted",
                    message="The agent reached its maximum tool-step budget.",
                    event_type=AgentEventType.BUDGET_EXHAUSTED,
                    budget=budget,
                )
                return run

            if plan is None:
                if budget.used_replans >= budget.max_replans:
                    self._fail_run(
                        run,
                        error_code="agent_replan_budget_exhausted",
                        message="The agent reached its maximum re-plan budget.",
                        event_type=AgentEventType.BUDGET_EXHAUSTED,
                        budget=budget,
                    )
                    return run
                if budget.used_model_calls >= budget.max_model_calls:
                    self._fail_run(
                        run,
                        error_code="agent_model_budget_exhausted",
                        message="The agent reached its maximum planner-call budget.",
                        event_type=AgentEventType.BUDGET_EXHAUSTED,
                        budget=budget,
                    )
                    return run
                plan = self._create_plan(run, goal, budget)
            step = self._next_step(plan, run.stage)
            if step is None:
                plan = None
                continue

            step.status = PlanStepStatus.RUNNING
            step.attempt_count += 1
            budget.used_steps += 1
            self._save_budget(budget)
            self._save_plan(plan)
            self._emit(
                run,
                AgentEventType.STEP_STARTED,
                f"Starting approved tool step {step.step_id}.",
                plan=plan,
                step=step,
            )

            try:
                result = self._plan_executor.execute(step, run)
            except StepExecutionError as error:
                result = ToolResult(
                    success=False,
                    summary="The approved tool raised a controlled execution error.",
                    retryable=error.retryable,
                )
                self._handle_failed_step(run, plan, step, result, budget, str(error))
                if run.status is not RunStatus.RUNNING:
                    return run
                continue
            except Exception as error:
                result = ToolResult(
                    success=False,
                    summary="The approved tool raised an unexpected execution error.",
                    retryable=False,
                )
                self._handle_failed_step(run, plan, step, result, budget, str(error))
                return run

            if self._cancel_if_requested(run, budget):
                return run

            assessment = self._outcome_evaluator.assess(result, run)
            if assessment.decision is AgentDecisionType.WAIT:
                step.status = PlanStepStatus.WAITING
                step.result_summary = result.summary
                self._save_plan(plan)
                self._emit(
                    run,
                    AgentEventType.HUMAN_ACTION_REQUESTED,
                    "The tool paused the run for a Human Gate decision.",
                    plan=plan,
                    step=step,
                    tool_result=result,
                )
                return run

            if assessment.decision in {AgentDecisionType.RETRY, AgentDecisionType.FAIL}:
                self._handle_failed_step(run, plan, step, result, budget, result.summary)
                if run.status is not RunStatus.RUNNING:
                    return run
                continue

            step.status = PlanStepStatus.COMPLETED
            step.result_summary = result.summary
            step.stage_complete = result.stage_complete
            self._add_artifacts(run, result)
            self._save_plan(plan)
            self._emit(
                run,
                AgentEventType.TOOL_COMPLETED,
                result.summary or "Approved tool completed.",
                plan=plan,
                step=step,
                tool_result=result,
            )
            if assessment.decision is AgentDecisionType.REPLAN:
                self._run_store.save(run)
                plan = None
                continue
            if assessment.decision is AgentDecisionType.COMPLETE:
                self._state_machine.transition_status(run, RunStatus.COMPLETED)
                self._run_store.save(run)
                self._emit(
                    run,
                    AgentEventType.RUN_COMPLETED,
                    "The agent reached the final review-draft stage.",
                    plan=plan,
                )
                return run

            if result.stage_complete:
                self._advance_stage(run)
            self._run_store.save(run)
            if self._next_step(plan, run.stage) is None:
                plan = None

        return run

    def _cancel_if_requested(self, run: ResearchRun, budget: ExecutionBudget) -> bool:
        if not self._cancellation_requested() or run.status is not RunStatus.RUNNING:
            return False
        self._state_machine.transition_status(run, RunStatus.CANCELLED)
        self._save_budget(budget)
        self._run_store.save(run)
        self._emit(
            run,
            AgentEventType.RUN_CANCELLED,
            "The Agent stopped at a safe execution boundary after a user request.",
        )
        return True

    def _create_plan(self, run: ResearchRun, goal: AgentGoal, budget: ExecutionBudget) -> AgentPlan:
        if budget.used_replans >= budget.max_replans:
            self._fail_run(
                run,
                error_code="agent_replan_budget_exhausted",
                message="The agent reached its maximum re-plan budget.",
                event_type=AgentEventType.BUDGET_EXHAUSTED,
                budget=budget,
            )
            raise AgentPlanRejectedError("Agent re-plan budget exhausted.")
        if budget.used_model_calls >= budget.max_model_calls:
            self._fail_run(
                run,
                error_code="agent_model_budget_exhausted",
                message="The agent reached its maximum planner-call budget.",
                event_type=AgentEventType.BUDGET_EXHAUSTED,
                budget=budget,
            )
            raise AgentPlanRejectedError("Agent planner-call budget exhausted.")

        budget.used_replans += 1
        budget.used_model_calls += 1
        self._save_budget(budget)
        observation = self._observe(run, goal, budget)
        self._emit(
            run,
            AgentEventType.REPLAN_STARTED,
            "Planning the next bounded workflow actions.",
            metadata={"plan_revision": str(observation.plan_revision + 1)},
        )
        try:
            proposed = self._planner.plan(goal, observation, self._registry.descriptors())
        except StepExecutionError as error:
            self._fail_run(
                run,
                error_code="agent_planner_failed",
                message="The agent planner failed before producing an executable plan.",
                event_type=AgentEventType.RUN_FAILED,
                budget=budget,
                retryable=error.retryable,
            )
            raise
        except Exception as error:
            self._fail_run(
                run,
                error_code="agent_planner_failed",
                message="The agent planner failed before producing an executable plan.",
                event_type=AgentEventType.RUN_FAILED,
                budget=budget,
            )
            raise StepExecutionError("Agent planner failed.") from error

        plan = proposed.model_copy(deep=True, update={"revision": budget.used_replans})
        for step in plan.steps:
            step.status = PlanStepStatus.PENDING
            step.attempt_count = 0
            step.result_summary = ""
        try:
            self._policy_guard.validate(plan, goal, observation, self._registry)
        except AgentPlanRejectedError as error:
            self._emit(
                run,
                AgentEventType.PLAN_REJECTED,
                str(error),
                plan=plan,
            )
            self._fail_run(
                run,
                error_code="agent_plan_rejected",
                message="The planner proposal failed the local execution policy.",
                event_type=AgentEventType.RUN_FAILED,
                budget=budget,
            )
            raise
        self._save_plan(plan)
        self._emit(
            run,
            AgentEventType.PLAN_CREATED,
            "A planner proposal passed local policy validation.",
            plan=plan,
            metadata={"step_count": str(len(plan.steps))},
        )
        return plan

    def _handle_failed_step(
        self,
        run: ResearchRun,
        plan: AgentPlan,
        step: PlanStep,
        result: ToolResult,
        budget: ExecutionBudget,
        error_summary: str,
    ) -> None:
        step.status = PlanStepStatus.FAILED
        step.result_summary = result.summary or "Tool execution failed."
        self._save_plan(plan)
        self._emit(
            run,
            AgentEventType.TOOL_FAILED,
            "An approved tool step failed.",
            plan=plan,
            step=step,
            tool_result=result,
            metadata={"error": error_summary[:400]},
        )
        if result.retryable and step.attempt_count < min(
            step.max_attempts, budget.max_attempts_per_step
        ):
            step.status = PlanStepStatus.PENDING
            self._save_plan(plan)
            return
        self._fail_run(
            run,
            error_code="agent_tool_failed",
            message="An approved tool failed and cannot be retried within policy.",
            event_type=AgentEventType.RUN_FAILED,
            budget=budget,
            retryable=result.retryable,
        )

    def _ensure_goal(self, goal: AgentGoal) -> None:
        brief = goal.brief
        if brief is None:
            raise AgentPlanRejectedError("The Agent goal is missing its research brief.")
        existing = self._load_artifact(GOAL_ARTIFACT, AgentGoal)
        if existing is None:
            self._artifact_store.save_json(GOAL_ARTIFACT, goal.model_dump(mode="json"))
            self._artifact_store.save_json(BRIEF_ARTIFACT, brief.model_dump(mode="json"))
            return
        if (
            existing.goal_id != goal.goal_id
            or existing.objective != goal.objective
            or existing.research_question != goal.research_question
            or existing.constraints != goal.constraints
            or existing.brief != brief
        ):
            raise AgentPlanRejectedError(
                "The persisted agent goal does not match the requested goal."
            )
        if self._load_artifact(BRIEF_ARTIFACT, type(brief)) is None:
            self._artifact_store.save_json(BRIEF_ARTIFACT, brief.model_dump(mode="json"))

    def _load_budget(self) -> ExecutionBudget:
        loaded = self._load_artifact(BUDGET_ARTIFACT, ExecutionBudget)
        if loaded is not None:
            return loaded
        self._save_budget(self._budget_template)
        return self._budget_template.model_copy(deep=True)

    def _save_budget(self, budget: ExecutionBudget) -> None:
        self._artifact_store.save_json(BUDGET_ARTIFACT, budget.model_dump(mode="json"))

    def _load_plan(self, goal: AgentGoal) -> AgentPlan | None:
        plan = self._load_artifact(PLAN_ARTIFACT, AgentPlan)
        if plan is None or plan.goal_id != goal.goal_id:
            return None
        return plan

    def _save_plan(self, plan: AgentPlan) -> None:
        self._artifact_store.save_json(PLAN_ARTIFACT, plan.model_dump(mode="json"))

    def _load_artifact(self, name: str, model: type[_ModelT]) -> _ModelT | None:
        try:
            return model.model_validate(self._artifact_store.load_json(name))
        except (FileNotFoundError, OSError, ValueError, TypeError, ValidationError):
            return None

    def _observe(
        self, run: ResearchRun, goal: AgentGoal, budget: ExecutionBudget
    ) -> AgentObservation:
        plan = self._load_plan(goal)
        return AgentObservation(
            goal_id=goal.goal_id,
            current_stage=run.stage,
            run_status=run.status,
            artifact_types=sorted(
                reference.artifact_type.value for reference in run.artifact_references
            ),
            completed_steps=(
                [step.step_id for step in plan.steps if step.status is PlanStepStatus.COMPLETED]
                if plan is not None
                else []
            ),
            pending_human_actions=[
                action.action_id for action in run.human_actions if action.decision is None
            ],
            last_failure_code=run.failure.error_code if run.failure is not None else "",
            last_tool_summary=(
                next(
                    (step.result_summary for step in reversed(plan.steps) if step.result_summary),
                    "",
                )
                if plan is not None
                else ""
            ),
            plan_revision=plan.revision if plan is not None else 0,
            budget=budget,
            available_tools=[item.name for item in self._registry.descriptors()],
        )

    def _next_step(self, plan: AgentPlan, current_stage: RunStage) -> PlanStep | None:
        descriptors = {item.name: item for item in self._registry.descriptors()}
        for step in plan.steps:
            descriptor = descriptors.get(step.tool_name)
            if (
                descriptor is None
                or descriptor.stage is not current_stage
                or step.status is not PlanStepStatus.PENDING
            ):
                continue
            if descriptor.stage is None:
                continue
            if all(
                any(
                    dependency.step_id == dependency_id
                    and dependency.status is PlanStepStatus.COMPLETED
                    for dependency in plan.steps
                )
                for dependency_id in step.depends_on
            ):
                return step
        return None

    @staticmethod
    def _normalize_loaded_plan(plan: AgentPlan) -> None:
        for step in plan.steps:
            if step.status in {
                PlanStepStatus.RUNNING,
                PlanStepStatus.WAITING,
                PlanStepStatus.FAILED,
            }:
                step.status = PlanStepStatus.PENDING

    def _reconcile_completed_stage(self, run: ResearchRun, plan: AgentPlan) -> AgentPlan:
        descriptors = {item.name: item for item in self._registry.descriptors()}
        completed_current = next(
            (
                step
                for step in plan.steps
                if step.status is PlanStepStatus.COMPLETED
                and step.stage_complete
                and descriptors.get(step.tool_name) is not None
                and descriptors[step.tool_name].stage is run.stage
            ),
            None,
        )
        if completed_current is None:
            return plan
        if run.stage is RunStage.DRAFTING:
            self._state_machine.transition_status(run, RunStatus.COMPLETED)
        else:
            self._advance_stage(run)
        self._run_store.save(run)
        return plan

    def _advance_stage(self, run: ResearchRun) -> None:
        next_stages = ALLOWED_STAGE_TRANSITIONS.get(run.stage, frozenset())
        if len(next_stages) != 1:
            raise AgentPlanRejectedError(
                f"No deterministic next stage exists for {run.stage.value}."
            )
        self._state_machine.transition_stage(run, next(iter(next_stages)))

    def _fail_run(
        self,
        run: ResearchRun,
        *,
        error_code: str,
        message: str,
        event_type: AgentEventType,
        budget: ExecutionBudget,
        retryable: bool = False,
    ) -> None:
        if run.status is RunStatus.RUNNING:
            run.record_failure(
                RunFailure(
                    error_code=error_code,
                    message=message,
                    stage=run.stage,
                    retryable=retryable,
                )
            )
            self._state_machine.transition_status(run, RunStatus.FAILED)
            self._run_store.save(run)
        self._save_budget(budget)
        self._emit(run, event_type, message, metadata={"error_code": error_code})

    def _add_artifacts(self, run: ResearchRun, result: ToolResult) -> None:
        existing_ids = {reference.artifact_id for reference in run.artifact_references}
        for reference in result.artifact_references:
            if reference.artifact_id not in existing_ids:
                run.add_artifact(reference)
                existing_ids.add(reference.artifact_id)

    def _emit(
        self,
        run: ResearchRun,
        event_type: AgentEventType,
        summary: str,
        *,
        plan: AgentPlan | None = None,
        step: PlanStep | None = None,
        tool_result: ToolResult | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        event = AgentEvent(
            run_id=run.run_id,
            sequence=self._next_sequence,
            event_type=event_type,
            summary=summary[:1000],
            plan_id=plan.plan_id if plan is not None else None,
            step_id=step.step_id if step is not None else None,
            tool_name=step.tool_name if step is not None else None,
            artifact_references=(
                tool_result.artifact_references if tool_result is not None else []
            ),
            metadata=metadata or {},
        )
        self._event_store.append(event)
        self._next_sequence += 1

    def _has_event(self, event_type: AgentEventType) -> bool:
        return any(event.event_type is event_type for event in self._event_store.read())
