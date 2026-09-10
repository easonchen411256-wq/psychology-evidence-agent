"""Quality contracts for bounded research-agent decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import RunStage


class QualityStatus(StrEnum):
    """Deterministic interpretation of the currently available search coverage."""

    SUFFICIENT = "sufficient"
    NEEDS_MORE_SEARCH = "needs_more_search"
    LIMIT_REACHED = "limit_reached"


class QualityAction(StrEnum):
    """The bounded action that the runtime should take after an assessment."""

    PROCEED = "proceed"
    REPLAN_SEARCH = "replan_search"
    STOP = "stop"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ResearchQualityReport(BaseModel):
    """Metadata-only report used to control the next agent decision."""

    model_config = ConfigDict(extra="forbid")

    report_version: str = Field(default="1", pattern=r"^1$")
    stage: RunStage
    query_count: int = Field(ge=0)
    raw_result_count: int = Field(ge=0)
    deduplicated_candidate_count: int = Field(ge=0)
    new_candidate_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    new_candidate_ratio: float = Field(ge=0, le=1)
    duplicate_ratio: float = Field(ge=0, le=1)
    multi_query_candidate_count: int = Field(ge=0)
    max_query_coverage: int = Field(ge=0)
    scope_signal_count: int = Field(ge=0)
    candidate_target: int = Field(ge=1)
    minimum_new_candidate_ratio: float = Field(ge=0, le=1)
    max_search_rounds: int = Field(ge=1)
    candidate_paper_ids: list[str] = Field(default_factory=list, max_length=5000)
    fulltext_count: int = Field(default=0, ge=0)
    evidence_card_count: int = Field(default=0, ge=0)
    unresolved_human_gate_count: int = Field(default=0, ge=0)
    unsupported_claim_count: int = Field(default=0, ge=0)
    status: QualityStatus
    recommended_action: QualityAction
    reason: str = Field(min_length=1, max_length=1000)
    evaluated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_counts(self) -> ResearchQualityReport:
        if self.deduplicated_candidate_count > self.raw_result_count:
            raise ValueError("deduplicated candidates cannot exceed raw results")
        if self.new_candidate_count > self.deduplicated_candidate_count:
            raise ValueError("new candidates cannot exceed deduplicated candidates")
        if self.duplicate_count > self.raw_result_count:
            raise ValueError("duplicates cannot exceed raw results")
        if (
            self.status is QualityStatus.SUFFICIENT
            and self.recommended_action is not QualityAction.PROCEED
        ):
            raise ValueError("sufficient quality must recommend proceeding")
        if (
            self.status is QualityStatus.NEEDS_MORE_SEARCH
            and self.recommended_action is not QualityAction.REPLAN_SEARCH
        ):
            raise ValueError("insufficient quality must recommend another search plan")
        if (
            self.status is QualityStatus.LIMIT_REACHED
            and self.recommended_action is not QualityAction.STOP
        ):
            raise ValueError("a reached search limit must recommend stopping")
        return self
