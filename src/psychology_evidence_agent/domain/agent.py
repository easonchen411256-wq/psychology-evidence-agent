"""Stable contracts for the bounded research-agent runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import RunStage, RunStatus
from .run import ArtifactReference


class AgentEventType(StrEnum):
    GOAL_CREATED = "goal_created"
    PLAN_CREATED = "plan_created"
    PLAN_REJECTED = "plan_rejected"
    STEP_STARTED = "step_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    CHECKPOINT_REUSED = "checkpoint_reused"
    HUMAN_ACTION_REQUESTED = "human_action_requested"
    HUMAN_DECISION_RECEIVED = "human_decision_received"
    REPLAN_STARTED = "replan_started"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AgentDecisionType(StrEnum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    WAIT = "wait"
    RETRY = "retry"
    REPLAN = "replan"
    FAIL = "fail"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolEffect(StrEnum):
    LOCAL = "local"
    NETWORK = "network"
    MODEL = "model"
    HUMAN_GATE = "human_gate"


class ToolArgumentType(StrEnum):
    """Small, JSON-compatible type vocabulary exposed to the planner."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


class ToolArgumentSpec(BaseModel):
    """One declared input accepted by a registered Agent tool."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value_type: ToolArgumentType
    required: bool = False
    description: str = Field(default="", max_length=300)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SearchPreferences(BaseModel):
    """User-visible search controls that are persisted with an Agent goal."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["automatic", "manual"] = "automatic"
    manual_query: str = Field(default="", max_length=500)
    candidate_limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def validate_manual_query(self) -> SearchPreferences:
        if self.mode == "manual" and not self.manual_query.strip():
            raise ValueError("manual search mode requires a query")
        self.manual_query = self.manual_query.strip()
        return self


class ResearchBrief(BaseModel):
    """Explicit research scope used to plan and audit one Agent run."""

    model_config = ConfigDict(extra="forbid")

    research_question: str = Field(min_length=1, max_length=4000)
    population: str = Field(default="", max_length=500)
    intervention_or_exposure: str = Field(default="", max_length=500)
    comparison: str = Field(default="", max_length=500)
    outcomes: list[str] = Field(default_factory=list, max_length=20)
    inclusion_criteria: list[str] = Field(default_factory=list, max_length=20)
    exclusion_criteria: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=10)
    study_types: list[str] = Field(default_factory=list, max_length=20)
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)

    @model_validator(mode="after")
    def validate_year_range(self) -> ResearchBrief:
        if self.year_from is not None and self.year_to is not None:
            if self.year_from > self.year_to:
                raise ValueError("year_from must not be later than year_to.")
        return self


class AgentGoal(BaseModel):
    """A user objective normalized for one persisted agent run."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(default_factory=lambda: _id("goal"), pattern=r"^goal_[0-9a-f]{32}$")
    objective: str = Field(min_length=1, max_length=4000)
    research_question: str = Field(min_length=1, max_length=4000)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    brief: ResearchBrief | None = None
    search_preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def ensure_brief_matches_question(self) -> AgentGoal:
        if self.brief is None:
            self.brief = ResearchBrief(research_question=self.research_question)
        elif self.brief.research_question != self.research_question:
            raise ValueError("Research brief question must match the Agent goal question.")
        return self


PlanArgumentValue: TypeAlias = str | int | float | bool | list[str]


class ToolDescriptor(BaseModel):
    """The only capability information exposed to a planner."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]{1,79}$")
    description: str = Field(min_length=1, max_length=400)
    output_description: str = Field(default="", max_length=300)
    stage: RunStage | None = None
    effect: ToolEffect
    argument_names: list[str] = Field(default_factory=list, max_length=20)
    argument_specs: list[ToolArgumentSpec] = Field(default_factory=list, max_length=20)
    may_request_human: bool = False
    retryable: bool = False


class PlanStep(BaseModel):
    """One planned call; status is persisted to make interruption recoverable."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(
        default_factory=lambda: _id("step"), pattern=r"^step_[0-9A-Za-z_.-]{1,79}$"
    )
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, PlanArgumentValue] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    success_criteria: str = Field(min_length=1, max_length=400)
    requires_human_approval: bool = False
    max_attempts: int = Field(default=1, ge=1, le=5)
    status: PlanStepStatus = PlanStepStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    result_summary: str = Field(default="", max_length=1000)
    stage_complete: bool = True


class AgentPlan(BaseModel):
    """A bounded, inspectable plan proposed by a planner."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: _id("plan"), pattern=r"^plan_[0-9a-f]{32}$")
    goal_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    rationale: str = Field(default="", max_length=1000)
    created_at: datetime = Field(default_factory=_now)


class ExecutionBudget(BaseModel):
    """Hard limits preventing an autonomous loop from running unbounded."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=40, ge=1, le=500)
    max_replans: int = Field(default=10, ge=0, le=100)
    max_model_calls: int = Field(default=20, ge=0, le=100)
    max_attempts_per_step: int = Field(default=2, ge=1, le=5)
    used_steps: int = Field(default=0, ge=0)
    used_replans: int = Field(default=0, ge=0)
    used_model_calls: int = Field(default=0, ge=0)


class AgentObservation(BaseModel):
    """Planner input containing state summaries, never raw paper content."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str
    current_stage: RunStage
    run_status: RunStatus
    artifact_types: list[str] = Field(default_factory=list, max_length=100)
    completed_steps: list[str] = Field(default_factory=list, max_length=100)
    pending_human_actions: list[str] = Field(default_factory=list, max_length=20)
    last_failure_code: str = ""
    last_tool_summary: str = Field(default="", max_length=1000)
    plan_revision: int = Field(default=0, ge=0)
    budget: ExecutionBudget
    available_tools: list[str] = Field(default_factory=list, max_length=100)


class ToolResult(BaseModel):
    """Small tool outcome; detailed outputs remain in artifacts."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    summary: str = Field(default="", max_length=1000)
    artifact_references: list[ArtifactReference] = Field(default_factory=list)
    blocked: bool = False
    human_action_id: str | None = None
    retryable: bool = False
    stage_complete: bool = True
    replan_required: bool = False

    @model_validator(mode="after")
    def validate_replan_contract(self) -> ToolResult:
        if self.replan_required and not self.success:
            raise ValueError("A re-plan request must be attached to a successful tool result.")
        return self


class CompletionAssessment(BaseModel):
    """Deterministic decision about what the controller does next."""

    model_config = ConfigDict(extra="forbid")

    decision: AgentDecisionType
    reason: str = Field(min_length=1, max_length=500)


class AgentEvent(BaseModel):
    """Append-only operational trace without prompts, paper text, or model thoughts."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: _id("event"), pattern=r"^event_[0-9a-f]{32}$")
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: AgentEventType
    summary: str = Field(min_length=1, max_length=1000)
    plan_id: str | None = None
    step_id: str | None = None
    tool_name: str | None = None
    input_fingerprint: str | None = None
    artifact_references: list[ArtifactReference] = Field(default_factory=list, max_length=100)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)
    occurred_at: datetime = Field(default_factory=_now)
