import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.run import (
    ArtifactReference,
    HumanDecision,
    PendingHumanAction,
    ResearchRun,
    RunFailure,
    SearchRoundSummary,
    create_research_run,
    new_run_id,
)


class ResearchRunModelTests(unittest.TestCase):
    def test_factory_creates_valid_initial_run(self):
        run = create_research_run("如何监测跌倒恐惧？")
        self.assertRegex(run.run_id, r"^run_[0-9a-f]{32}$")
        self.assertEqual(run.status, RunStatus.CREATED)
        self.assertEqual(run.stage, RunStage.INITIALIZING)
        self.assertIsNotNone(run.created_at.tzinfo)
        self.assertIsNotNone(run.updated_at.tzinfo)

    def test_status_and_stage_are_orthogonal(self):
        run = create_research_run("问题")
        run.status = RunStatus.WAITING_FOR_HUMAN
        run.stage = RunStage.RETRIEVING_FULLTEXT
        self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
        self.assertEqual(run.stage, RunStage.RETRIEVING_FULLTEXT)

    def test_nested_contracts_round_trip(self):
        timestamp = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        reference = ArtifactReference(
            artifact_id="draft-1",
            artifact_type=ArtifactType.REVIEW_DRAFT,
            logical_key="draft/review.md",
            created_at=timestamp,
        )
        run = ResearchRun(
            run_id=new_run_id(),
            research_question="问题",
            status=RunStatus.RUNNING,
            stage=RunStage.DRAFTING,
            search_rounds=[
                SearchRoundSummary(
                    round_number=1,
                    query_count=2,
                    candidate_count=4,
                    included_count=3,
                    artifact_reference=reference,
                    started_at=timestamp,
                )
            ],
            artifact_references=[reference],
            failure=RunFailure(
                error_code="temporary",
                message="retry",
                stage=RunStage.DRAFTING,
                retryable=True,
                occurred_at=timestamp,
            ),
            created_at=timestamp,
            updated_at=timestamp,
        )
        restored = ResearchRun.model_validate(run.model_dump(mode="json"))
        self.assertEqual(restored, run)

    def test_invalid_enum_missing_id_and_naive_time_are_rejected(self):
        with self.assertRaises(ValidationError):
            ResearchRun(run_id=new_run_id(), research_question="问题", status="unknown")
        with self.assertRaises(ValidationError):
            ResearchRun(run_id=new_run_id(), research_question="问题", stage="unknown")
        with self.assertRaises(ValidationError):
            ResearchRun(run_id=new_run_id(), research_question="问题", created_at=datetime.now())
        with self.assertRaises(ValidationError):
            ResearchRun(research_question="问题")

    def test_reference_rejects_absolute_or_escape_key(self):
        for key in ("/absolute.json", "C:/absolute.json", "nested/../escape.json"):
            with self.assertRaises(ValidationError):
                ArtifactReference(
                    artifact_id="a",
                    artifact_type=ArtifactType.EVIDENCE_CARD,
                    logical_key=key,
                )

    def test_human_action_and_decision_round_trip_and_policy(self):
        action = PendingHumanAction(
            action_type=HumanActionType.FULLTEXT_REQUIRED,
            reason="No lawful full text.",
            stage=RunStage.RETRIEVING_FULLTEXT,
        )
        self.assertEqual(
            action.allowed_decisions,
            [HumanDecisionType.PROVIDE_FULLTEXT, HumanDecisionType.SKIP_PAPER],
        )
        decision = HumanDecision(
            action_id=action.action_id,
            decision_type=HumanDecisionType.SKIP_PAPER,
        )
        action.status = HumanActionStatus.RESOLVED
        action.decision = decision
        action.resolved_at = decision.decided_at
        restored = PendingHumanAction.model_validate(action.model_dump(mode="json"))
        self.assertEqual(restored, action)
        with self.assertRaises(ValidationError):
            PendingHumanAction(
                action_type=HumanActionType.FULLTEXT_REQUIRED,
                reason="No lawful full text.",
                stage=RunStage.RETRIEVING_FULLTEXT,
                allowed_decisions=[HumanDecisionType.INCLUDE],
            )


if __name__ == "__main__":
    unittest.main()
