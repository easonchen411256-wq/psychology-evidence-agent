"""Vocabulary already used by stable project contracts."""

from enum import StrEnum


class ScreeningEvidenceLevel(StrEnum):
    DIRECT = "direct"
    ADJACENT = "adjacent"
    BACKGROUND = "background"
    EXCLUDE = "exclude"


class MaterialCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class InferenceStrength(StrEnum):
    ASSOCIATION = "association"
    PREDICTION = "prediction"
    CAUSAL = "causal"
    INTERVENTION_EFFECT = "intervention_effect"
    UNCERTAIN = "uncertain"


class EvidenceRoleCandidate(StrEnum):
    DIRECT_CANDIDATE = "direct_candidate"
    ADJACENT_CANDIDATE = "adjacent_candidate"
    BACKGROUND_CANDIDATE = "background_candidate"
    REVIEW_REQUIRED = "review_required"


class OpenAccessStatus(StrEnum):
    OPEN_PDF_AVAILABLE = "open_pdf_available"
    OPEN_LANDING_PAGE_ONLY = "open_landing_page_only"
    MANUAL_ACCESS_NEEDED = "manual_access_needed"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStage(StrEnum):
    INITIALIZING = "initializing"
    SEARCHING = "searching"
    SCREENING = "screening"
    RETRIEVING_FULLTEXT = "retrieving_fulltext"
    EXTRACTING_EVIDENCE = "extracting_evidence"
    SYNTHESIZING = "synthesizing"
    DRAFTING = "drafting"


class ArtifactType(StrEnum):
    SEARCH_RESULTS = "search_results"
    RESEARCH_QUALITY_REPORT = "research_quality_report"
    SCREENING_RESULTS = "screening_results"
    FULLTEXT_METADATA = "fulltext_metadata"
    EVIDENCE_CARD = "evidence_card"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"
    REVIEW_DRAFT = "review_draft"
    REVIEW_REPORT = "review_report"
    FULLTEXT_DOCUMENT = "fulltext_document"


class HumanActionType(StrEnum):
    FULLTEXT_REQUIRED = "fulltext_required"
    SCREENING_REVIEW_REQUIRED = "screening_review_required"


class HumanDecisionType(StrEnum):
    PROVIDE_FULLTEXT = "provide_fulltext"
    SKIP_PAPER = "skip_paper"
    INCLUDE = "include"
    EXCLUDE = "exclude"
    KEEP_UNCERTAIN = "keep_uncertain"


class HumanActionStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
