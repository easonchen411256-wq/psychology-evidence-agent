"""Codex structured-output models for title-and-abstract screening."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import ScreeningEvidenceLevel


class ScreeningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    relevance_score: float = Field(ge=1, le=10)
    evidence_level: ScreeningEvidenceLevel
    subtopic: str
    rationale: str
    human_review_note: str


class ScreeningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Literature Screening Result")

    screened_papers: list[ScreeningDecision]
