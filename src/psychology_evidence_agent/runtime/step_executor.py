"""Execute one injected application-service handler for the current stage."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..domain.enums import ArtifactType, HumanActionStatus, RunStage, RunStatus
from ..domain.errors import MissingStageInputError, StepExecutionError, UnsupportedStageError
from ..domain.run import ResearchRun
from .step_result import StepResult

StepHandler = Callable[[ResearchRun], StepResult]

STAGE_REQUIRED_ARTIFACTS: Mapping[RunStage, frozenset[ArtifactType]] = {
    RunStage.INITIALIZING: frozenset(),
    RunStage.SEARCHING: frozenset(),
    RunStage.SCREENING: frozenset({ArtifactType.SEARCH_RESULTS}),
    RunStage.RETRIEVING_FULLTEXT: frozenset({ArtifactType.SEARCH_RESULTS}),
    RunStage.EXTRACTING_EVIDENCE: frozenset({ArtifactType.FULLTEXT_DOCUMENT}),
    RunStage.SYNTHESIZING: frozenset({ArtifactType.EVIDENCE_CARD}),
    RunStage.DRAFTING: frozenset({ArtifactType.EVIDENCE_SYNTHESIS}),
}

STAGE_OUTPUT_ARTIFACTS: Mapping[RunStage, frozenset[ArtifactType]] = {
    RunStage.INITIALIZING: frozenset(),
    RunStage.SEARCHING: frozenset({ArtifactType.SEARCH_RESULTS}),
    RunStage.SCREENING: frozenset({ArtifactType.SCREENING_RESULTS}),
    RunStage.RETRIEVING_FULLTEXT: frozenset(
        {ArtifactType.FULLTEXT_METADATA, ArtifactType.FULLTEXT_DOCUMENT}
    ),
    RunStage.EXTRACTING_EVIDENCE: frozenset({ArtifactType.EVIDENCE_CARD}),
    RunStage.SYNTHESIZING: frozenset({ArtifactType.EVIDENCE_SYNTHESIS}),
    RunStage.DRAFTING: frozenset({ArtifactType.REVIEW_DRAFT}),
}


class StepExecutor:
    """Run one stage handler without changing stage or automatically continuing."""

    def __init__(self, handlers: Mapping[RunStage, StepHandler]) -> None:
        self._handlers = dict(handlers)

    def execute_one_step(self, run: ResearchRun) -> StepResult:
        if run.status is not RunStatus.RUNNING:
            raise StepExecutionError(
                f"Run {run.run_id} must be running before executing {run.stage.value}."
            )
        handler = self._handlers.get(run.stage)
        if handler is None:
            raise UnsupportedStageError(
                f"No step handler is registered for {run.stage.value} ({run.run_id})."
            )
        available = {reference.artifact_type for reference in run.artifact_references}
        missing = STAGE_REQUIRED_ARTIFACTS[run.stage] - available
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise MissingStageInputError(
                f"Run {run.run_id} at {run.stage.value} is missing required artifact(s): {names}."
            )
        try:
            result = handler(run)
        except StepExecutionError:
            raise
        except Exception as error:
            raise StepExecutionError(f"Stage {run.stage.value} failed for {run.run_id}.") from error
        if result.stage is not run.stage:
            raise StepExecutionError(
                f"Step handler returned {result.stage.value} for current stage {run.stage.value}."
            )
        if result.blocked:
            if result.success:
                raise StepExecutionError(
                    f"Blocked stage {run.stage.value} returned success unexpectedly."
                )
            if run.status is not RunStatus.WAITING_FOR_HUMAN or not any(
                action.action_id == result.human_action_id
                and action.status is HumanActionStatus.PENDING
                for action in run.human_actions
            ):
                raise StepExecutionError(
                    f"Blocked stage {run.stage.value} did not create a pending human action."
                )
            return result
        if run.status is not RunStatus.RUNNING:
            raise StepExecutionError(
                f"Stage {run.stage.value} changed run status without returning a blocked result."
            )
        expected = STAGE_OUTPUT_ARTIFACTS[run.stage]
        actual = {reference.artifact_type for reference in result.artifact_references}
        unexpected = actual - expected
        if unexpected:
            names = ", ".join(sorted(item.value for item in unexpected))
            raise StepExecutionError(
                f"Stage {run.stage.value} returned unexpected artifact type(s): {names}."
            )
        if result.success:
            missing_outputs = expected - actual
            if missing_outputs:
                names = ", ".join(sorted(item.value for item in missing_outputs))
                raise StepExecutionError(
                    f"Stage {run.stage.value} completed without required artifact(s): {names}."
                )
        return result
