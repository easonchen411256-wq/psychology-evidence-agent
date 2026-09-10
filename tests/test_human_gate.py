import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    OpenAccessStatus,
    RunStage,
    RunStatus,
    ScreeningEvidenceLevel,
)
from psychology_evidence_agent.domain.errors import (
    BlockingHumanActionRemainingError,
    HumanActionAlreadyResolvedError,
    HumanActionNotFoundError,
    InvalidHumanDecisionError,
    RunNotWaitingForHumanError,
)
from psychology_evidence_agent.domain.fulltext import FullTextCandidate
from psychology_evidence_agent.domain.run import (
    ArtifactReference,
    HumanDecision,
    PendingHumanAction,
    create_research_run,
)
from psychology_evidence_agent.domain.screening import ScreeningDecision
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.state_machine import RunStateMachine


class FakeRunStore:
    def __init__(self):
        self.saved = []

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


class FailingRunStore(FakeRunStore):
    def save(self, run):
        raise OSError("storage unavailable")


def running_fulltext_run():
    run = create_research_run("如何取得合法全文？")
    run.status = RunStatus.RUNNING
    run.stage = RunStage.RETRIEVING_FULLTEXT
    return run


def unavailable_candidate():
    return FullTextCandidate(
        retrieval_source="openalex",
        paper_id="W1",
        is_open_access=False,
        access_status=OpenAccessStatus.MANUAL_ACCESS_NEEDED,
        next_step="Use authorized access.",
    )


class HumanGateTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeRunStore()
        self.gate = HumanGate(RunStateMachine(), self.store)

    def test_fulltext_request_pauses_without_changing_stage(self):
        run = running_fulltext_run()
        action = self.gate.request_fulltext_review(run, unavailable_candidate())
        self.assertEqual(action.action_type, HumanActionType.FULLTEXT_REQUIRED)
        self.assertEqual(
            action.allowed_decisions,
            [HumanDecisionType.PROVIDE_FULLTEXT, HumanDecisionType.SKIP_PAPER],
        )
        self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
        self.assertEqual(run.stage, RunStage.RETRIEVING_FULLTEXT)
        self.assertEqual(self.store.saved[-1].human_actions[0], action)

    def test_valid_decision_resolves_and_resumes_without_executing_stage(self):
        run = running_fulltext_run()
        action = self.gate.request_fulltext_review(run, unavailable_candidate())
        resolved = self.gate.resolve(
            run,
            HumanDecision(action_id=action.action_id, decision_type=HumanDecisionType.SKIP_PAPER),
        )
        self.assertIs(resolved, action)
        self.assertEqual(action.status, HumanActionStatus.RESOLVED)
        self.assertEqual(action.decision.decision_type, HumanDecisionType.SKIP_PAPER)
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(run.stage, RunStage.RETRIEVING_FULLTEXT)
        self.assertEqual(len(self.store.saved), 2)

    def test_invalid_and_duplicate_decisions_are_rejected(self):
        run = running_fulltext_run()
        action = self.gate.request_fulltext_review(run, unavailable_candidate())
        with self.assertRaises(InvalidHumanDecisionError):
            self.gate.resolve(
                run,
                HumanDecision(action_id=action.action_id, decision_type=HumanDecisionType.INCLUDE),
            )
        with self.assertRaises(InvalidHumanDecisionError):
            self.gate.resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                ),
            )
        self.gate.resolve(
            run,
            HumanDecision(action_id=action.action_id, decision_type=HumanDecisionType.SKIP_PAPER),
        )
        with self.assertRaises(HumanActionAlreadyResolvedError):
            self.gate.resolve(
                run,
                HumanDecision(
                    action_id=action.action_id, decision_type=HumanDecisionType.SKIP_PAPER
                ),
            )

    def test_resume_rejects_wrong_state_unknown_and_remaining_actions(self):
        run = running_fulltext_run()
        with self.assertRaises(RunNotWaitingForHumanError):
            self.gate.resume(run)
        action = self.gate.request_fulltext_review(run, unavailable_candidate())
        with self.assertRaises(HumanActionNotFoundError):
            self.gate.resolve(
                run,
                HumanDecision(
                    action_id="action_00000000000000000000000000000000",
                    decision_type=HumanDecisionType.SKIP_PAPER,
                ),
            )
        run.human_actions.append(
            PendingHumanAction(
                action_type=HumanActionType.FULLTEXT_REQUIRED,
                reason="Another paper needs review.",
                stage=run.stage,
            )
        )
        with self.assertRaises(BlockingHumanActionRemainingError):
            self.gate.resume(run)
        self.assertEqual(action.status, HumanActionStatus.PENDING)

    def test_provided_fulltext_requires_and_preserves_document_reference(self):
        run = running_fulltext_run()
        action = self.gate.request_fulltext_review(run, unavailable_candidate())
        document = ArtifactReference(
            artifact_id="document-1",
            artifact_type=ArtifactType.FULLTEXT_DOCUMENT,
            logical_key="fulltext/W1.pdf",
        )
        self.gate.resolve(
            run,
            HumanDecision(
                action_id=action.action_id,
                decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                provided_artifact_reference=document,
            ),
        )
        self.assertIn(document, run.artifact_references)
        self.assertEqual(run.status, RunStatus.RUNNING)

    def test_screening_gate_preserves_original_ai_decision(self):
        run = create_research_run("筛选问题")
        run.status = RunStatus.RUNNING
        run.stage = RunStage.SCREENING
        screening = ScreeningDecision(
            paper_id="W2",
            relevance_score=6,
            evidence_level=ScreeningEvidenceLevel.BACKGROUND,
            subtopic="monitoring",
            rationale="AI suggestion",
            human_review_note="Confirm intervention relevance.",
        )
        original = screening.model_dump()
        action = self.gate.request_screening_review(run, screening)
        self.gate.resolve(
            run,
            HumanDecision(action_id=action.action_id, decision_type=HumanDecisionType.EXCLUDE),
        )
        self.assertEqual(screening.model_dump(), original)
        self.assertEqual(action.decision.decision_type, HumanDecisionType.EXCLUDE)
        self.assertEqual(action.related_paper_ids, ["W2"])

    def test_restart_safe_fulltext_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store1 = FileSystemResearchRunStore(root)
            run = running_fulltext_run()
            store1.create(run)
            gate1 = HumanGate(RunStateMachine(), store1)
            action = gate1.request_fulltext_review(run, unavailable_candidate())
            del gate1, store1

            store2 = FileSystemResearchRunStore(root)
            restored = store2.load(run.run_id)
            self.assertEqual(restored.status, RunStatus.WAITING_FOR_HUMAN)
            self.assertEqual(restored.stage, RunStage.RETRIEVING_FULLTEXT)
            self.assertEqual(restored.human_actions[0].allowed_decisions, action.allowed_decisions)
            HumanGate(RunStateMachine(), store2).resolve(
                restored,
                HumanDecision(
                    action_id=restored.human_actions[0].action_id,
                    decision_type=HumanDecisionType.SKIP_PAPER,
                ),
            )

            store3 = FileSystemResearchRunStore(root)
            final = store3.load(run.run_id)
            self.assertEqual(final.status, RunStatus.RUNNING)
            self.assertEqual(final.stage, RunStage.RETRIEVING_FULLTEXT)
            self.assertEqual(final.human_actions[0].status, HumanActionStatus.RESOLVED)
            self.assertEqual(
                final.human_actions[0].decision.decision_type, HumanDecisionType.SKIP_PAPER
            )

    def test_storage_failure_does_not_mutate_in_memory_run(self):
        run = running_fulltext_run()
        gate = HumanGate(RunStateMachine(), FailingRunStore())
        with self.assertRaises(OSError):
            gate.request_fulltext_review(run, unavailable_candidate())
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(run.human_actions, [])


if __name__ == "__main__":
    unittest.main()
