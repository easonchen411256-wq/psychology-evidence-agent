"""Project-level errors that hide transport and provider implementation details."""


class ExternalServiceError(RuntimeError):
    """Base error for a dependency outside the application process."""


class ExternalTimeoutError(ExternalServiceError):
    pass


class ExternalRateLimitError(ExternalServiceError):
    pass


class ExternalUnavailableError(ExternalServiceError):
    pass


class ExternalResponseError(ExternalServiceError):
    pass


class LiteratureSearchError(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    pass


class RunPersistenceError(RuntimeError):
    """Base error for persisted ResearchRun state."""


class RunNotFoundError(RunPersistenceError):
    pass


class RunAlreadyExistsError(RunPersistenceError):
    pass


class RunBusyError(RunPersistenceError):
    """Raised when another process currently owns a research-run lock."""


class RunConcurrencyError(RunPersistenceError):
    """Raised when a stale ResearchRun snapshot attempts to overwrite newer state."""


class InvalidStateTransitionError(RuntimeError):
    """Raised when a deterministic state transition is not allowed."""


class StepExecutionError(RuntimeError):
    """Base error for one-stage execution failures."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class AgentPlanRejectedError(StepExecutionError):
    """Raised when a planner proposal violates the deterministic tool policy."""


class MissingStageInputError(StepExecutionError):
    pass


class UnsupportedStageError(StepExecutionError):
    pass


class RunNotRunningError(RuntimeError):
    pass


class RunNotWaitingForHumanError(RuntimeError):
    pass


class HumanActionNotFoundError(RuntimeError):
    pass


class HumanActionAlreadyResolvedError(RuntimeError):
    pass


class InvalidHumanDecisionError(RuntimeError):
    pass


class BlockingHumanActionRemainingError(RuntimeError):
    pass
