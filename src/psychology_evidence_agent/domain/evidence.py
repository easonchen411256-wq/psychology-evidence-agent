"""Codex structured-output models for evidence cards."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .enums import InferenceStrength, MaterialCompleteness


class EvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    authors: list[str]
    year: str
    journal: str
    doi_or_url: str


class StudyMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_question: str
    design: str
    sample: str
    measures: list[str]
    analysis: str


class EvidenceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str
    inference_strength: InferenceStrength
    evidence_location: str


class ClaimBoundaries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_claims: list[str]
    unsupported_claims: list[str]


class EvidenceCard(BaseModel):
    """Strict, structured evidence extracted from one supplied paper material."""

    model_config = ConfigDict(extra="forbid", title="Psychology Evidence Card")

    source: EvidenceSource
    material_completeness: MaterialCompleteness
    study: StudyMetadata
    findings: list[EvidenceFinding]
    limitations: list[str]
    claim_boundaries: ClaimBoundaries
    human_review_items: list[str]
