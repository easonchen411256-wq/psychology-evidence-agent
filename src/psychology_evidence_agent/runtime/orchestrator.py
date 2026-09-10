"""Small, restartable workflow orchestrator built on runtime contracts."""

from __future__ import annotations

from ..domain.enums import RunStage, RunStatus
from ..domain.errors import StepExecutionError
from ..domain.run import ResearchRun, RunFailure
from ..ports.persistence import ResearchRunStore
from .state_machine import ALLOWED_STAGE_TRANSITIONS, RunStateMachine
from .step_executor import StepExecutor
from .step_result import StepResult


class EvidenceAgent:
    """Execute one injected workflow step at a time until pause or termination.

    The orchestrator owns lifecycle coordination only. It does not know provider
    APIs, prompt text, artifact contents, or domain-specific service algorithms.
    """

    def __init__(
        self,
        *,
        run_store: ResearchRunStore,
        state_machine: RunStateMachine,
        step_executor: StepExecutor,
    ) -> None:
        self._run_store = run_store
        self._state_machine = state_machine
        self._step_executor = step_executor

    def start(self, run: ResearchRun) -> ResearchRun:
        """Move a newly created run to RUNNING and persist that lifecycle event."""
        self._state_machine.transition_status(run, RunStatus.RUNNING)
        self._run_store.save(run)
        return run

    def run_until_blocked(self, run: ResearchRun) -> ResearchRun:
        """Run successive stages until human input, failure, or completion."""
        if run.status is RunStatus.CREATED:
            self.start(run)
        if run.status is not RunStatus.RUNNING:
            return run

        while run.status is RunStatus.RUNNING:
            try:
                result = self._step_executor.execute_one_step(run)
            except StepExecutionError as error:
                self._fail_run(
                    run,
                    error_code="step_execution_failed",
                    retryable=error.retryable,
                )
                return run

            if result.blocked:
                self._run_store.save(run)
                return run
            if not result.success:
                self._fail_run(
                    run,
                    error_code=(
                        result.failure.error_code
                        if result.failure is not None
                        else "step_execution_failed"
                    ),
                    retryable=result.failure.retryable if result.failure is not None else False,
                    failure=result.failure,
                )
                return run

            self._add_new_artifacts(run, result)
            if run.stage is RunStage.DRAFTING:
                self._state_machine.transition_status(run, RunStatus.COMPLETED)
                self._run_store.save(run)
                return run

            next_stages = ALLOWED_STAGE_TRANSITIONS.get(run.stage, frozenset())
            if len(next_stages) != 1:
                self._fail_run(run, error_code="invalid_stage_graph", retryable=False)
                return run
            self._state_machine.transition_stage(run, next(iter(next_stages)))
            self._run_store.save(run)

        return run

    @staticmethod
    def _add_new_artifacts(run: ResearchRun, result: StepResult) -> None:
        existing_ids = {reference.artifact_id for reference in run.artifact_references}
        for reference in result.artifact_references:
            if reference.artifact_id not in existing_ids:
                run.add_artifact(reference)
                existing_ids.add(reference.artifact_id)

    def _fail_run(
        self,
        run: ResearchRun,
        *,
        error_code: str,
        retryable: bool,
        failure: RunFailure | None = None,
    ) -> None:
        run.record_failure(
            failure
            or RunFailure(
                error_code=error_code,
                message="The workflow step failed; inspect the structured run failure and retry policy.",
                stage=run.stage,
                retryable=retryable,
            )
        )
        self._state_machine.transition_status(run, RunStatus.FAILED)
        self._run_store.save(run)
