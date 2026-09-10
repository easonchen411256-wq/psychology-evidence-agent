import unittest
from typing import Any

from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanActionType,
    HumanDecisionType,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.errors import InvalidStateTransitionError, StepExecutionError
from psychology_evidence_agent.domain.run import (
    ArtifactReference,
    HumanDecision,
    create_research_run,
)
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.orchestrator import EvidenceAgent
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.runtime.step_executor import STAGE_OUTPUT_ARTIFACTS, StepExecutor
from psychology_evidence_agent.runtime.step_result import StepResult


class InMemoryRunStore:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def create(self, run):
        self.save(run)

    def save(self, run):
        self.saved.append(run.model_copy(deep=True))

    def load(self, run_id):
        for run in reversed(self.saved):
            if run.run_id == run_id:
                return run.model_copy(deep=True)
        raise AssertionError("run not saved")

    def exists(self, run_id):
        return any(run.run_id == run_id for run in self.saved)


def reference(artifact_type: ArtifactType, suffix: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"{artifact_type.value}:{suffix}",
        artifact_type=artifact_type,
        logical_key=f"{artifact_type.value}/{suffix}.json",
    )


def successful_handlers() -> dict[RunStage, Any]:
    return {
        stage: (
            lambda _run, current=stage: StepResult(
                stage=current,
                success=True,
                artifact_references=[
                    reference(item, current.value) for item in STAGE_OUTPUT_ARTIFACTS[current]
                ],
            )
        )
        for stage in RunStage
    }


class EvidenceAgentTests(unittest.TestCase):
    def test_runs_all_stages_to_completion_without_network_or_model(self):
        store = InMemoryRunStore()
        agent = EvidenceAgent(
            run_store=store,
            state_machine=RunStateMachine(),
            step_executor=StepExecutor(successful_handlers()),
        )
        run = create_research_run("研究问题")

        final = agent.run_until_blocked(run)

        self.assertIs(final, run)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.stage, RunStage.DRAFTING)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(
            {reference.artifact_type for reference in run.artifact_references},
            {
                ArtifactType.SEARCH_RESULTS,
                ArtifactType.SCREENING_RESULTS,
                ArtifactType.FULLTEXT_METADATA,
                ArtifactType.FULLTEXT_DOCUMENT,
                ArtifactType.EVIDENCE_CARD,
                ArtifactType.EVIDENCE_SYNTHESIS,
                ArtifactType.REVIEW_DRAFT,
            },
        )

    def test_missing_step_input_is_persisted_as_structured_failure(self):
        store = InMemoryRunStore()
        agent = EvidenceAgent(
            run_store=store,
            state_machine=RunStateMachine(),
            step_executor=StepExecutor(
                {
                    RunStage.INITIALIZING: lambda _: StepResult(
                        stage=RunStage.INITIALIZING, success=True
                    )
                }
            ),
        )
        run = create_research_run("研究问题")

        agent.run_until_blocked(run)

        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.failure.error_code, "step_execution_failed")
        self.assertEqual(store.load(run.run_id).status, RunStatus.FAILED)

    def test_human_gate_pauses_then_resume_continues_same_stage(self):
        store = InMemoryRunStore()
        machine = RunStateMachine()
        gate = HumanGate(machine, store)
        retrieve_calls = 0

        def retrieve(run):
            nonlocal retrieve_calls
            retrieve_calls += 1
            if retrieve_calls == 1:
                action = gate.request_action(
                    run,
                    action_type=HumanActionType.FULLTEXT_REQUIRED,
                    reason="需要人工提供合法全文。",
                )
                return StepResult(
                    stage=RunStage.RETRIEVING_FULLTEXT,
                    success=False,
                    blocked=True,
                    human_action_id=action.action_id,
                )
            return StepResult(
                stage=RunStage.RETRIEVING_FULLTEXT,
                success=True,
                artifact_references=[
                    reference(ArtifactType.FULLTEXT_METADATA, "after-human"),
                    reference(ArtifactType.FULLTEXT_DOCUMENT, "after-human"),
                ],
            )

        handlers = successful_handlers()
        handlers[RunStage.RETRIEVING_FULLTEXT] = retrieve
        agent = EvidenceAgent(
            run_store=store,
            state_machine=machine,
            step_executor=StepExecutor(handlers),
        )
        run = create_research_run("研究问题")

        agent.run_until_blocked(run)
        self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
        action = next(action for action in run.human_actions if not action.decision)
        self.assertEqual(action.action_type, HumanActionType.FULLTEXT_REQUIRED)

        document = reference(ArtifactType.FULLTEXT_DOCUMENT, "provided")
        gate.resolve(
            run,
            HumanDecision(
                action_id=action.action_id,
                decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                provided_artifact_reference=document,
            ),
        )
        agent.run_until_blocked(run)

        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.stage, RunStage.DRAFTING)
        self.assertEqual(retrieve_calls, 2)

    def test_start_rejects_non_created_run(self):
        store = InMemoryRunStore()
        agent = EvidenceAgent(
            run_store=store,
            state_machine=RunStateMachine(),
            step_executor=StepExecutor({}),
        )
        run = create_research_run("研究问题")
        run.status = RunStatus.RUNNING
        with self.assertRaises(InvalidStateTransitionError):
            agent.start(run)

    def test_retryable_step_failure_is_persisted_as_retryable(self):
        store = InMemoryRunStore()

        def temporary_failure(_run):
            raise StepExecutionError("temporary provider failure", retryable=True)

        agent = EvidenceAgent(
            run_store=store,
            state_machine=RunStateMachine(),
            step_executor=StepExecutor({RunStage.INITIALIZING: temporary_failure}),
        )
        run = create_research_run("研究问题")

        agent.run_until_blocked(run)

        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertTrue(run.failure.retryable)


if __name__ == "__main__":
    unittest.main()
