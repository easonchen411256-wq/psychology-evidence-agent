"""Codex structured-output models for traceable review drafts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_card_file: str
    finding: str
    evidence_location: str


class DraftClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    evidence: list[ClaimEvidence] = Field(min_length=1)


class ReviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    paragraph: str
    supporting_card_files: list[str]
    claims: list[DraftClaim]
    caveat: str


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Psychology Evidence Review Draft")

    title: str
    research_question: str
    evidence_scope: str
    sections: list[ReviewSection] = Field(min_length=3, max_length=3)
    evidence_gaps: list[str]
    human_review_items: list[str]
