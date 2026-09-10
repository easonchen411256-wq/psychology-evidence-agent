import unittest

from psychology_evidence_agent.domain.enums import RunStage, RunStatus
from psychology_evidence_agent.domain.errors import InvalidStateTransitionError
from psychology_evidence_agent.domain.run import RunFailure, create_research_run
from psychology_evidence_agent.runtime.state_machine import (
    ALLOWED_STAGE_TRANSITIONS,
    ALLOWED_STATUS_TRANSITIONS,
    TERMINAL_STATUSES,
    RunStateMachine,
)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.machine = RunStateMachine()

    def test_canonical_stage_graph_and_completeness(self):
        expected = {
            RunStage.INITIALIZING: {RunStage.SEARCHING},
            RunStage.SEARCHING: {RunStage.SCREENING},
            RunStage.SCREENING: {RunStage.RETRIEVING_FULLTEXT},
            RunStage.RETRIEVING_FULLTEXT: {RunStage.EXTRACTING_EVIDENCE},
            RunStage.EXTRACTING_EVIDENCE: {RunStage.SYNTHESIZING},
            RunStage.SYNTHESIZING: {RunStage.DRAFTING},
            RunStage.DRAFTING: set(),
        }
        self.assertEqual(
            {stage: set(targets) for stage, targets in ALLOWED_STAGE_TRANSITIONS.items()},
            expected,
        )
        for current, targets in expected.items():
            for target in targets:
                self.assertTrue(self.machine.can_transition_stage(current, target))
        for stage in RunStage:
            if stage is not RunStage.DRAFTING:
                self.assertTrue(ALLOWED_STAGE_TRANSITIONS[stage])

    def test_legal_stage_transition_updates_only_stage_metadata(self):
        run = create_research_run("问题")
        self.machine.transition_status(run, RunStatus.RUNNING)
        before = run.updated_at
        result = self.machine.transition_stage(run, RunStage.SEARCHING)
        self.assertIs(result, run)
        self.assertEqual(run.stage, RunStage.SEARCHING)
        self.assertGreaterEqual(run.updated_at, before)
        self.assertEqual(run.status, RunStatus.RUNNING)

    def test_illegal_stage_transition_is_explicit(self):
        run = create_research_run("问题")
        self.machine.transition_status(run, RunStatus.RUNNING)
        self.machine.transition_stage(run, RunStage.SEARCHING)
        with self.assertRaisesRegex(
            InvalidStateTransitionError,
            rf"{run.run_id}.*searching.*drafting",
        ):
            self.machine.transition_stage(run, RunStage.DRAFTING)

    def test_status_rules_and_terminal_statuses(self):
        self.assertEqual(
            ALLOWED_STATUS_TRANSITIONS[RunStatus.CREATED],
            {RunStatus.RUNNING, RunStatus.CANCELLED},
        )
        self.assertEqual(
            TERMINAL_STATUSES,
            {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
        )
        run = create_research_run("问题")
        self.machine.transition_status(run, RunStatus.RUNNING)
        self.machine.transition_status(run, RunStatus.WAITING_FOR_HUMAN)
        self.machine.transition_status(run, RunStatus.RUNNING)
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_status(run, RunStatus.COMPLETED)
        run.stage = RunStage.DRAFTING
        self.machine.transition_status(run, RunStatus.COMPLETED)
        self.assertIsNotNone(run.completed_at)
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_status(run, RunStatus.RUNNING)

    def test_failed_status_requires_structured_failure(self):
        run = create_research_run("问题")
        self.machine.transition_status(run, RunStatus.RUNNING)
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_status(run, RunStatus.FAILED)
        run.record_failure(
            RunFailure(
                error_code="temporary",
                message="retry",
                stage=run.stage,
                retryable=True,
            )
        )
        self.machine.transition_status(run, RunStatus.FAILED)

    def test_stage_transition_rejected_after_terminal_status(self):
        run = create_research_run("问题")
        self.machine.transition_status(run, RunStatus.CANCELLED)
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_stage(run, RunStage.SEARCHING)

    def test_stage_transition_requires_running_status(self):
        run = create_research_run("问题")
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_stage(run, RunStage.SEARCHING)
        self.machine.transition_status(run, RunStatus.RUNNING)
        self.machine.transition_stage(run, RunStage.SEARCHING)
        self.machine.transition_status(run, RunStatus.WAITING_FOR_HUMAN)
        with self.assertRaises(InvalidStateTransitionError):
            self.machine.transition_stage(run, RunStage.SCREENING)

    def test_retry_clears_only_retryable_failure(self):
        run = create_research_run("问题")
        run.record_failure(
            RunFailure(
                error_code="temporary",
                message="retry",
                stage=run.stage,
                retryable=True,
            )
        )
        run.status = RunStatus.FAILED

        self.machine.retry(run)

        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertIsNone(run.failure)

    def test_retry_rejects_non_retryable_failure(self):
        run = create_research_run("问题")
        run.record_failure(
            RunFailure(
                error_code="invalid_output",
                message="do not retry automatically",
                stage=run.stage,
                retryable=False,
            )
        )
        run.status = RunStatus.FAILED

        with self.assertRaises(InvalidStateTransitionError):
            self.machine.retry(run)
