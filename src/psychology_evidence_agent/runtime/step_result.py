"""Typed result returned by exactly one stage execution."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.enums import RunStage
from ..domain.run import ArtifactReference, RunFailure


class StepResult(BaseModel):
    """Small execution summary; large service results remain in ArtifactStore."""

    model_config = ConfigDict(extra="forbid")

    stage: RunStage
    success: bool
    artifact_references: list[ArtifactReference] = Field(default_factory=list)
    summary: str = ""
    failure: RunFailure | None = None
    blocked: bool = False
    human_action_id: str | None = None
    stage_complete: bool = True

    @model_validator(mode="after")
    def validate_success_contract(self) -> StepResult:
        if self.blocked:
            if self.success or self.failure is not None:
                raise ValueError("A blocked step cannot be successful or failed.")
            if not self.human_action_id:
                raise ValueError("A blocked step must reference a human action.")
            if self.artifact_references:
                raise ValueError("A blocked step cannot publish artifact references.")
            return self
        if self.human_action_id is not None:
            raise ValueError("Only a blocked step may reference a human action.")
        if self.success and self.failure is not None:
            raise ValueError("A successful step cannot contain a failure.")
        if not self.success and self.failure is None:
            raise ValueError("A failed step must contain a structured failure.")
        if self.failure is not None and self.failure.stage is not self.stage:
            raise ValueError("A step failure must reference the step's stage.")
        if not self.success and self.artifact_references:
            raise ValueError("A failed step cannot publish artifact references.")
        return self
