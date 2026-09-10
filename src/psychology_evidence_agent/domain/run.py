"""Serializable, lightweight state for one research workflow run."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    RunStage,
    RunStatus,
)

_RUN_ID_PATTERN = re.compile(r"^run_[0-9a-f]{32}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    """Create a unique, filesystem-safe, readable run identifier."""
    return f"run_{uuid4().hex}"


def new_human_action_id() -> str:
    return f"action_{uuid4().hex}"


def new_human_decision_id() -> str:
    return f"decision_{uuid4().hex}"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Run timestamps must be timezone-aware.")
    return value.astimezone(UTC)


class ArtifactReference(BaseModel):
    """A logical pointer to an artifact held by RunArtifactStore."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    logical_key: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)

    @field_validator("logical_key")
    @classmethod
    def validate_logical_key(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ":" in value.split("/", 1)[0]:
            raise ValueError("Artifact logical_key must be a relative key.")
        if ".." in value.replace("\\", "/").split("/"):
            raise ValueError("Artifact logical_key must not escape its store root.")
        return value


class SearchRoundSummary(BaseModel):
    """Small summary of one search round; paper lists remain artifacts."""

    model_config = ConfigDict(extra="forbid")

    round_number: int = Field(ge=1)
    query_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    artifact_reference: ArtifactReference | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value)


class RunFailure(BaseModel):
    """Stable failure semantics for API and future runtime recovery."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    stage: RunStage
    retryable: bool
    occurred_at: datetime = Field(default_factory=utc_now)
    provider: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)


ALLOWED_HUMAN_DECISIONS: dict[HumanActionType, tuple[HumanDecisionType, ...]] = {
    HumanActionType.FULLTEXT_REQUIRED: (
        HumanDecisionType.PROVIDE_FULLTEXT,
        HumanDecisionType.SKIP_PAPER,
    ),
    HumanActionType.SCREENING_REVIEW_REQUIRED: (
        HumanDecisionType.INCLUDE,
        HumanDecisionType.EXCLUDE,
        HumanDecisionType.KEEP_UNCERTAIN,
    ),
}


class HumanDecision(BaseModel):
    """A deterministic, user-provided resolution for one human action."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(
        default_factory=new_human_decision_id, pattern=r"^decision_[0-9a-f]{32}$"
    )
    action_id: str = Field(min_length=1)
    decision_type: HumanDecisionType
    note: str = ""
    provided_artifact_reference: ArtifactReference | None = None
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class PendingHumanAction(BaseModel):
    """Lightweight persisted request that blocks a run until a user resolves it."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=new_human_action_id, pattern=r"^action_[0-9a-f]{32}$")
    action_type: HumanActionType
    reason: str = Field(min_length=1)
    stage: RunStage
    related_artifact_references: list[ArtifactReference] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)
    allowed_decisions: list[HumanDecisionType] = Field(default_factory=list)
    status: HumanActionStatus = HumanActionStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    decision: HumanDecision | None = None

    @field_validator("created_at", "resolved_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value)

    @model_validator(mode="after")
    def validate_lifecycle_and_decisions(self) -> PendingHumanAction:
        expected = list(ALLOWED_HUMAN_DECISIONS[self.action_type])
        if not self.allowed_decisions:
            self.allowed_decisions = expected
        elif self.allowed_decisions != expected:
            raise ValueError("Allowed human decisions must match the action-type policy.")
        if self.status is HumanActionStatus.PENDING:
            if self.resolved_at is not None or self.decision is not None:
                raise ValueError(
                    "A pending human action cannot have a decision or resolution time."
                )
        elif self.resolved_at is None or self.decision is None:
            raise ValueError("A resolved human action requires a decision and resolution time.")
        elif self.decision.action_id != self.action_id:
            raise ValueError("A human decision must reference its own action.")
        return self


class ResearchRun(BaseModel):
    """Lightweight persisted state, deliberately excluding large artifacts."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=_RUN_ID_PATTERN.pattern)
    research_question: str = Field(min_length=1)
    status: RunStatus = RunStatus.CREATED
    stage: RunStage = RunStage.INITIALIZING
    revision: int = Field(default=0, ge=0)
    search_rounds: list[SearchRoundSummary] = Field(default_factory=list)
    artifact_references: list[ArtifactReference] = Field(default_factory=list)
    human_actions: list[PendingHumanAction] = Field(default_factory=list)
    failure: RunFailure | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    config_reference: str | None = None

    @field_validator("created_at", "updated_at", "completed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value)

    def touch(self, now: datetime | None = None) -> None:
        """Update persistence metadata without implementing state transitions."""
        self.updated_at = _aware_utc(now or utc_now())

    def add_artifact(self, reference: ArtifactReference, now: datetime | None = None) -> None:
        self.artifact_references.append(reference)
        self.touch(now)

    def record_failure(self, failure: RunFailure, now: datetime | None = None) -> None:
        self.failure = failure
        self.touch(now)

    def add_human_action(self, action: PendingHumanAction, now: datetime | None = None) -> None:
        self.human_actions.append(action)
        self.touch(now)


def create_research_run(
    research_question: str,
    *,
    config_reference: str | None = None,
    now: datetime | None = None,
) -> ResearchRun:
    """Create a new run in the initial, not-yet-running state."""
    timestamp = _aware_utc(now or utc_now())
    return ResearchRun(
        run_id=new_run_id(),
        research_question=research_question,
        created_at=timestamp,
        updated_at=timestamp,
        config_reference=config_reference,
    )
