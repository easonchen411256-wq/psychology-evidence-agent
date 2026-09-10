"""Persisted, deterministic human-in-the-loop pause and resolution control."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    OpenAccessStatus,
    RunStatus,
)
from ..domain.errors import (
    BlockingHumanActionRemainingError,
    HumanActionAlreadyResolvedError,
    HumanActionNotFoundError,
    InvalidHumanDecisionError,
    RunNotRunningError,
    RunNotWaitingForHumanError,
)
from ..domain.fulltext import FullTextCandidate
from ..domain.run import (
    ALLOWED_HUMAN_DECISIONS,
    ArtifactReference,
    HumanDecision,
    PendingHumanAction,
    ResearchRun,
    utc_now,
)
from ..domain.screening import ScreeningDecision
from ..ports.persistence import ResearchRunStore
from .state_machine import RunStateMachine


class HumanGate:
    """Pause a run for one blocking human action, then resolve and persist it."""

    def __init__(self, state_machine: RunStateMachine, run_store: ResearchRunStore) -> None:
        self._state_machine = state_machine
        self._run_store = run_store

    def request_action(
        self,
        run: ResearchRun,
        *,
        action_type: HumanActionType,
        reason: str,
        related_artifact_references: Sequence[ArtifactReference] = (),
        related_paper_ids: Sequence[str] = (),
    ) -> PendingHumanAction:
        if run.status is not RunStatus.RUNNING:
            raise RunNotRunningError(
                f"Run {run.run_id} must be running before requesting human action."
            )
        self._ensure_no_blocking_actions(run)
        candidate = run.model_copy(deep=True)
        action = PendingHumanAction(
            action_type=action_type,
            reason=reason,
            stage=candidate.stage,
            related_artifact_references=list(related_artifact_references),
            related_paper_ids=list(related_paper_ids),
        )
        candidate.add_human_action(action)
        self._state_machine.transition_status(candidate, RunStatus.WAITING_FOR_HUMAN)
        self._persist_and_commit(run, candidate)
        return action

    def request_fulltext_review(
        self, run: ResearchRun, candidate: FullTextCandidate
    ) -> PendingHumanAction:
        if candidate.access_status is not OpenAccessStatus.MANUAL_ACCESS_NEEDED:
            raise InvalidHumanDecisionError(
                "A full-text gate is only required when no lawful full text is available."
            )
        return self.request_action(
            run,
            action_type=HumanActionType.FULLTEXT_REQUIRED,
            reason=f"No legal full text was available for paper {candidate.paper_id}.",
            related_paper_ids=[candidate.paper_id],
        )

    def request_screening_review(
        self,
        run: ResearchRun,
        screening: ScreeningDecision,
        *,
        related_artifact_references: Sequence[ArtifactReference] = (),
    ) -> PendingHumanAction:
        if not screening.human_review_note.strip():
            raise InvalidHumanDecisionError(
                "A screening gate requires a non-empty human_review_note."
            )
        return self.request_action(
            run,
            action_type=HumanActionType.SCREENING_REVIEW_REQUIRED,
            reason=screening.human_review_note,
            related_artifact_references=related_artifact_references,
            related_paper_ids=[screening.paper_id],
        )

    def resolve(self, run: ResearchRun, decision: HumanDecision) -> PendingHumanAction:
        candidate = run.model_copy(deep=True)
        action = self._action_for(candidate, decision.action_id)
        if action.status is HumanActionStatus.RESOLVED:
            raise HumanActionAlreadyResolvedError(
                f"Human action {action.action_id} is already resolved."
            )
        if candidate.status is not RunStatus.WAITING_FOR_HUMAN:
            raise RunNotWaitingForHumanError(
                f"Run {candidate.run_id} is not waiting for a human decision."
            )
        self._validate_decision(action, decision)
        action.status = HumanActionStatus.RESOLVED
        action.decision = decision
        action.resolved_at = utc_now()
        if decision.provided_artifact_reference is not None:
            candidate.add_artifact(decision.provided_artifact_reference)
        candidate.touch()
        self._state_machine.transition_status(candidate, RunStatus.RUNNING)
        self._persist_and_commit(run, candidate)
        return self._action_for(run, decision.action_id)

    def resume(self, run: ResearchRun) -> ResearchRun:
        if run.status is not RunStatus.WAITING_FOR_HUMAN:
            raise RunNotWaitingForHumanError(
                f"Run {run.run_id} is not waiting for a human decision."
            )
        candidate = run.model_copy(deep=True)
        self._ensure_no_blocking_actions(candidate)
        self._state_machine.transition_status(candidate, RunStatus.RUNNING)
        self._persist_and_commit(run, candidate)
        return run

    def _persist_and_commit(self, run: ResearchRun, candidate: ResearchRun) -> None:
        """Persist first so a storage failure cannot mutate the caller's run."""
        self._run_store.save(candidate)
        existing_actions = {action.action_id: action for action in run.human_actions}
        committed_actions: list[PendingHumanAction] = []
        for candidate_action in candidate.human_actions:
            existing_action = existing_actions.get(candidate_action.action_id)
            if existing_action is None:
                committed_actions.append(candidate_action)
                continue
            existing_action.__dict__.clear()
            existing_action.__dict__.update(candidate_action.__dict__)
            committed_actions.append(existing_action)
        committed_values = dict(candidate.__dict__)
        committed_values["human_actions"] = committed_actions
        run.__dict__.clear()
        run.__dict__.update(committed_values)

    @staticmethod
    def _action_for(run: ResearchRun, action_id: str) -> PendingHumanAction:
        for action in run.human_actions:
            if action.action_id == action_id:
                return action
        raise HumanActionNotFoundError(f"Human action {action_id} was not found in {run.run_id}.")

    @staticmethod
    def _ensure_no_blocking_actions(run: ResearchRun) -> None:
        pending = [
            action.action_id
            for action in run.human_actions
            if action.status is HumanActionStatus.PENDING
        ]
        if pending:
            raise BlockingHumanActionRemainingError(
                f"Run {run.run_id} still has blocking human action(s): {', '.join(pending)}."
            )

    @staticmethod
    def _validate_decision(action: PendingHumanAction, decision: HumanDecision) -> None:
        if decision.action_id != action.action_id:
            raise InvalidHumanDecisionError(
                "Human decision action_id does not match the pending action."
            )
        if decision.decision_type not in ALLOWED_HUMAN_DECISIONS[action.action_type]:
            raise InvalidHumanDecisionError(
                f"Decision {decision.decision_type.value} is not valid for {action.action_type.value}."
            )
        if decision.decision_type is HumanDecisionType.PROVIDE_FULLTEXT:
            reference = decision.provided_artifact_reference
            if reference is None or reference.artifact_type is not ArtifactType.FULLTEXT_DOCUMENT:
                raise InvalidHumanDecisionError(
                    "PROVIDE_FULLTEXT requires a FULLTEXT_DOCUMENT artifact reference."
                )
        elif decision.provided_artifact_reference is not None:
            raise InvalidHumanDecisionError(
                "Only PROVIDE_FULLTEXT may include a provided artifact reference."
            )
