import tempfile
import unittest
from pathlib import Path
from typing import Any

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.event_store import FileSystemAgentEventStore
from psychology_evidence_agent.domain.agent import (
    AgentEventType,
    AgentGoal,
    AgentObservation,
    AgentPlan,
    ExecutionBudget,
    PlanStep,
    ToolDescriptor,
    ToolEffect,
    ToolResult,
)
from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanActionType,
    HumanDecisionType,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.errors import AgentPlanRejectedError
from psychology_evidence_agent.domain.run import (
    ArtifactReference,
    HumanDecision,
    create_research_run,
)
from psychology_evidence_agent.runtime.agent_controller import BRIEF_ARTIFACT, AgentController
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.services.planning import CodexPlanner, DeterministicPlanner


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Any] = {}

    def create(self, run: Any) -> None:
        self._runs[run.run_id] = run.model_copy(deep=True)

    def save(self, run: Any) -> None:
        self._runs[run.run_id] = run.model_copy(deep=True)

    def load(self, run_id: str) -> Any:
        return self._runs[run_id].model_copy(deep=True)

    def exists(self, run_id: str) -> bool:
        return run_id in self._runs


def _reference(artifact_type: ArtifactType, stage: RunStage) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"{artifact_type.value}:{stage.value}",
        artifact_type=artifact_type,
        logical_key=f"{artifact_type.value}/{stage.value}.json",
    )


class FakeTool:
    def __init__(
        self,
        stage: RunStage,
        *,
        gate: HumanGate | None = None,
        block_once: bool = False,
        fail_once: bool = False,
        replan_once: bool = False,
    ) -> None:
        self.stage = stage
        self.gate = gate
        self.block_once = block_once
        self.fail_once = fail_once
        self.replan_once = replan_once
        self.calls = 0
        self._descriptor = ToolDescriptor(
            name=f"workflow.{stage.value}",
            description=f"Test tool for {stage.value}.",
            stage=stage,
            effect=ToolEffect.LOCAL,
            may_request_human=gate is not None,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: Any, arguments: dict[str, str]) -> ToolResult:
        self.calls += 1
        if self.block_once and self.calls == 1:
            if self.gate is None:
                raise AssertionError("A blocking test tool needs a HumanGate.")
            action = self.gate.request_action(
                run,
                action_type=HumanActionType.FULLTEXT_REQUIRED,
                reason="Test full text decision required.",
            )
            return ToolResult(success=False, blocked=True, human_action_id=action.action_id)
        if self.fail_once and self.calls == 1:
            return ToolResult(success=False, summary="temporary test failure", retryable=True)
        if self.replan_once and self.calls == 1:
            return ToolResult(
                success=True,
                summary="coverage requires another bounded search plan",
                stage_complete=False,
                replan_required=True,
            )
        artifact_types = {
            RunStage.SEARCHING: (ArtifactType.SEARCH_RESULTS,),
            RunStage.SCREENING: (ArtifactType.SCREENING_RESULTS,),
            RunStage.RETRIEVING_FULLTEXT: (
                ArtifactType.FULLTEXT_METADATA,
                ArtifactType.FULLTEXT_DOCUMENT,
            ),
            RunStage.EXTRACTING_EVIDENCE: (ArtifactType.EVIDENCE_CARD,),
            RunStage.SYNTHESIZING: (ArtifactType.EVIDENCE_SYNTHESIS,),
            RunStage.DRAFTING: (ArtifactType.REVIEW_DRAFT,),
        }.get(self.stage, ())
        return ToolResult(
            success=True,
            summary=f"Completed {self.stage.value}.",
            artifact_references=[_reference(item, self.stage) for item in artifact_types],
        )


class FakeRegistry:
    def __init__(self, tools: list[FakeTool]) -> None:
        self._tools = {tool.descriptor.name: tool for tool in tools}

    def descriptors(self) -> list[ToolDescriptor]:
        return [tool.descriptor for tool in self._tools.values()]

    def get(self, name: str) -> FakeTool:
        return self._tools[name]


class CurrentStagePlanner:
    def __init__(self, *, max_attempts: int = 1) -> None:
        self.max_attempts = max_attempts

    def plan(self, goal, observation, tools) -> AgentPlan:
        return AgentPlan(
            goal_id=goal.goal_id,
            steps=[
                PlanStep(
                    tool_name=f"workflow.{observation.current_stage.value}",
                    success_criteria="Complete the current stage.",
                    max_attempts=self.max_attempts,
                )
            ],
        )


class InvalidPlanner:
    def plan(self, goal, observation, tools) -> AgentPlan:
        return AgentPlan(
            goal_id=goal.goal_id,
            steps=[
                PlanStep(
                    tool_name="workflow.unregistered",
                    success_criteria="This must be rejected.",
                )
            ],
        )


class AgentRuntimeTests(unittest.TestCase):
    def _controller(
        self,
        root: Path,
        run: Any,
        planner: Any,
        registry: FakeRegistry,
        store: InMemoryRunStore,
        *,
        budget: Any = None,
        cancellation_requested: Any = None,
    ) -> AgentController:
        artifacts = FileSystemArtifactStore(root / "artifacts")
        return AgentController(
            run_store=store,
            artifact_store=artifacts,
            event_store=FileSystemAgentEventStore(root / "events.jsonl"),
            state_machine=RunStateMachine(),
            planner=planner,
            registry=registry,
            budget=budget,
            cancellation_requested=cancellation_requested,
        )

    def test_agent_cancels_at_safe_boundary_after_user_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            registry = FakeRegistry([FakeTool(stage) for stage in RunStage])

            def cancel_requested() -> bool:
                return registry._tools["workflow.initializing"].calls > 0

            controller = self._controller(
                root,
                run,
                DeterministicPlanner(),
                registry,
                store,
                cancellation_requested=cancel_requested,
            )

            controller.run_until_blocked(
                run, AgentGoal(objective="研究问题", research_question="研究问题")
            )

            self.assertEqual(run.status, RunStatus.CANCELLED)
            self.assertEqual(registry._tools["workflow.initializing"].calls, 1)
            self.assertEqual(registry._tools["workflow.searching"].calls, 0)
            events = FileSystemAgentEventStore(root / "events.jsonl").read()
            self.assertIn(AgentEventType.RUN_CANCELLED, {event.event_type for event in events})

    def test_bounded_agent_completes_with_plan_budget_and_event_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            registry = FakeRegistry([FakeTool(stage) for stage in RunStage])
            controller = self._controller(root, run, DeterministicPlanner(), registry, store)

            controller.run_until_blocked(
                run, AgentGoal(objective="研究问题", research_question="研究问题")
            )

            self.assertEqual(run.status, RunStatus.COMPLETED)
            events = FileSystemAgentEventStore(root / "events.jsonl").read()
            event_types = {event.event_type for event in events}
            self.assertIn(AgentEventType.PLAN_CREATED, event_types)
            self.assertIn(AgentEventType.TOOL_COMPLETED, event_types)
            self.assertIn(AgentEventType.RUN_COMPLETED, event_types)
            self.assertEqual([event.sequence for event in events], list(range(1, len(events) + 1)))
            budget = FileSystemArtifactStore(root / "artifacts").load_json("agent/budget.json")
            self.assertEqual(budget["used_steps"], len(RunStage))
            brief = FileSystemArtifactStore(root / "artifacts").load_json(BRIEF_ARTIFACT)
            self.assertEqual(brief["research_question"], "研究问题")

    def test_invalid_plan_is_rejected_before_tool_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            registry = FakeRegistry([FakeTool(stage) for stage in RunStage])
            controller = self._controller(root, run, InvalidPlanner(), registry, store)

            with self.assertRaises(AgentPlanRejectedError):
                controller.run_until_blocked(
                    run, AgentGoal(objective="研究问题", research_question="研究问题")
                )

            self.assertEqual(run.status, RunStatus.FAILED)
            self.assertEqual(run.failure.error_code, "agent_plan_rejected")
            self.assertEqual(sum(tool.calls for tool in registry._tools.values()), 0)

    def test_retryable_tool_failure_retries_within_step_attempt_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            tools = [
                FakeTool(stage, fail_once=stage is RunStage.INITIALIZING) for stage in RunStage
            ]
            registry = FakeRegistry(tools)
            controller = self._controller(
                root, run, CurrentStagePlanner(max_attempts=2), registry, store
            )

            controller.run_until_blocked(
                run, AgentGoal(objective="研究问题", research_question="研究问题")
            )

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertEqual(tools[0].calls, 2)

    def test_successful_tool_can_request_a_bounded_replan_without_advancing_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            tools = [FakeTool(stage, replan_once=stage is RunStage.SEARCHING) for stage in RunStage]
            registry = FakeRegistry(tools)
            controller = self._controller(root, run, CurrentStagePlanner(), registry, store)

            controller.run_until_blocked(
                run, AgentGoal(objective="研究问题", research_question="研究问题")
            )

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertEqual(tools[1].calls, 2)

    def test_human_gate_pauses_and_resolved_run_continues_existing_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            machine = RunStateMachine()
            gate = HumanGate(machine, store)
            run = create_research_run("研究问题")
            store.create(run)
            tools = [
                FakeTool(
                    stage,
                    gate=gate if stage is RunStage.RETRIEVING_FULLTEXT else None,
                    block_once=stage is RunStage.RETRIEVING_FULLTEXT,
                )
                for stage in RunStage
            ]
            registry = FakeRegistry(tools)
            controller = self._controller(root, run, DeterministicPlanner(), registry, store)
            goal = AgentGoal(objective="研究问题", research_question="研究问题")

            controller.run_until_blocked(run, goal)

            self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
            action = next(item for item in run.human_actions if item.decision is None)
            gate.resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.SKIP_PAPER,
                ),
            )
            controller.run_until_blocked(run, goal)

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertEqual(tools[3].calls, 2)

    def test_step_budget_exhaustion_is_persisted_as_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = InMemoryRunStore()
            run = create_research_run("研究问题")
            store.create(run)
            registry = FakeRegistry([FakeTool(stage) for stage in RunStage])
            controller = self._controller(
                root,
                run,
                DeterministicPlanner(),
                registry,
                store,
                budget=ExecutionBudget(max_steps=1),
            )

            controller.run_until_blocked(
                run, AgentGoal(objective="研究问题", research_question="研究问题")
            )

            self.assertEqual(run.status, RunStatus.FAILED)
            self.assertEqual(run.failure.error_code, "agent_budget_exhausted")

    def test_codex_planner_validates_structured_plan_and_does_not_receive_paper_text(self):
        class FakeStructuredOutput:
            def __init__(self) -> None:
                self.payload = ""

            def generate(self, *, task_instruction, stdin_payload, schema_path):
                self.payload = stdin_payload
                return {
                    "goal_id": "goal_" + "a" * 32,
                    "steps": [
                        {
                            "tool_name": "workflow.searching",
                            "success_criteria": "Complete search.",
                        }
                    ],
                }

        structured = FakeStructuredOutput()
        planner = CodexPlanner(structured)
        goal = AgentGoal(
            goal_id="goal_" + "a" * 32,
            objective="研究问题",
            research_question="研究问题",
        )
        observation = {
            "goal_id": goal.goal_id,
            "current_stage": RunStage.SEARCHING,
            "run_status": RunStatus.RUNNING,
            "budget": ExecutionBudget(),
        }
        plan = planner.plan(
            goal,
            AgentObservation.model_validate(observation),
            [
                ToolDescriptor(
                    name="workflow.searching",
                    description="Search.",
                    stage=RunStage.SEARCHING,
                    effect=ToolEffect.NETWORK,
                )
            ],
        )

        self.assertEqual(plan.goal_id, goal.goal_id)
        self.assertIn("available_tools", structured.payload)
        self.assertNotIn("SECRET-PAPER-CONTENT", structured.payload)

    def test_codex_planner_converts_strict_argument_entries_to_mapping(self):
        class FakeStructuredOutput:
            def generate(self, *, task_instruction, stdin_payload, schema_path):
                return {
                    "goal_id": "goal_" + "b" * 32,
                    "steps": [
                        {
                            "tool_name": "search.execute_query",
                            "arguments": [
                                {"name": "query", "value": "older adults"},
                                {"name": "max_results", "value": 5},
                            ],
                            "success_criteria": "Complete search.",
                        }
                    ],
                }

        goal = AgentGoal(
            goal_id="goal_" + "b" * 32,
            objective="研究问题",
            research_question="研究问题",
        )
        plan = CodexPlanner(FakeStructuredOutput()).plan(
            goal,
            AgentObservation(
                goal_id=goal.goal_id,
                current_stage=RunStage.SEARCHING,
                run_status=RunStatus.RUNNING,
                budget=ExecutionBudget(),
            ),
            [],
        )

        self.assertEqual(plan.steps[0].arguments, {"query": "older adults", "max_results": 5})


if __name__ == "__main__":
    unittest.main()
