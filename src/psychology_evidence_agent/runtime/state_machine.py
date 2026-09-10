"""Pure deterministic ResearchRun transition policy."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ..domain.enums import RunStage, RunStatus
from ..domain.errors import InvalidStateTransitionError
from ..domain.run import ResearchRun

ALLOWED_STAGE_TRANSITIONS: Mapping[RunStage, frozenset[RunStage]] = MappingProxyType(
    {
        RunStage.INITIALIZING: frozenset({RunStage.SEARCHING}),
        RunStage.SEARCHING: frozenset({RunStage.SCREENING}),
        RunStage.SCREENING: frozenset({RunStage.RETRIEVING_FULLTEXT}),
        RunStage.RETRIEVING_FULLTEXT: frozenset({RunStage.EXTRACTING_EVIDENCE}),
        RunStage.EXTRACTING_EVIDENCE: frozenset({RunStage.SYNTHESIZING}),
        RunStage.SYNTHESIZING: frozenset({RunStage.DRAFTING}),
        RunStage.DRAFTING: frozenset(),
    }
)

ALLOWED_STATUS_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
        RunStatus.RUNNING: frozenset(
            {
                RunStatus.WAITING_FOR_HUMAN,
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
        ),
        RunStatus.WAITING_FOR_HUMAN: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
        RunStatus.COMPLETED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.CANCELLED: frozenset(),
    }
)

TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class RunStateMachine:
    """Validate and apply one legal state change; never calls services or stores."""

    def can_transition_stage(self, current: RunStage, target: RunStage) -> bool:
        return target in ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())

    def transition_stage(self, run: ResearchRun, target: RunStage) -> ResearchRun:
        if run.status is not RunStatus.RUNNING or not self.can_transition_stage(run.stage, target):
            raise InvalidStateTransitionError(
                f"Cannot transition {run.run_id} from {run.stage.value} to {target.value}."
            )
        run.stage = target
        run.touch()
        return run

    def can_transition_status(self, current: RunStatus, target: RunStatus) -> bool:
        return target in ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())

    def transition_status(self, run: ResearchRun, target: RunStatus) -> ResearchRun:
        if not self.can_transition_status(run.status, target):
            raise InvalidStateTransitionError(
                f"Cannot transition {run.run_id} from status {run.status.value} to {target.value}."
            )
        if target is RunStatus.COMPLETED and run.stage is not RunStage.DRAFTING:
            raise InvalidStateTransitionError(
                f"Cannot complete {run.run_id} before reaching {RunStage.DRAFTING.value}."
            )
        if target is RunStatus.FAILED and run.failure is None:
            raise InvalidStateTransitionError(
                f"Cannot fail {run.run_id} without a structured failure."
            )
        run.status = target
        run.touch()
        if target is RunStatus.COMPLETED and run.completed_at is None:
            run.completed_at = run.updated_at
        return run

    def retry(self, run: ResearchRun) -> ResearchRun:
        """Resume a failed run only when its structured failure is retryable."""
        if run.status is not RunStatus.FAILED:
            raise InvalidStateTransitionError(
                f"Cannot retry {run.run_id} from status {run.status.value}."
            )
        if run.failure is None or not run.failure.retryable:
            raise InvalidStateTransitionError(
                f"Run {run.run_id} does not have a retryable failure."
            )
        run.failure = None
        run.status = RunStatus.RUNNING
        run.touch()
        return run
