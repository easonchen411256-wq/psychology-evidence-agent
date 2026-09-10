"""Stable persisted structure for local evidence synthesis output."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import EvidenceRoleCandidate, MaterialCompleteness


class SynthesisSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    role_counts: dict[str, int]


class SynthesisCard(BaseModel):
    """Persisted projection of an evidence card, retaining its source data."""

    model_config = ConfigDict(extra="forbid")

    file: str
    source: dict[str, Any]
    material_completeness: MaterialCompleteness
    study: dict[str, Any]
    signals: dict[str, str]
    evidence_role_candidate: EvidenceRoleCandidate
    findings: list[Any]
    limitations: list[str]
    claim_boundaries: dict[str, Any]
    human_review_items: list[str]


class SkippedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    reason: str


class EvidenceSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Psychology Evidence Synthesis")

    summary: SynthesisSummary
    cards: list[SynthesisCard]
    skipped_files: list[SkippedFile]
