"""Deterministic guardrails for Psychology Evidence Agent output."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .domain.evidence import EvidenceCard
from .resources import load_schema

CAUSAL_TERMS = (
    "cause",
    "causes",
    "caused",
    "improve",
    "improves",
    "improved",
    "lead to",
    "leads to",
    "导致",
    "引起",
    "改善",
    "提高",
    "降低",
    "干预有效",
)
CROSS_SECTIONAL_MARKERS = ("cross-sectional", "cross sectional", "横断面")
CAUSAL_INFERENCE_STRENGTHS = {"causal", "intervention_effect"}


@lru_cache(maxsize=1)
def evidence_card_validator() -> Draft202012Validator:
    """Load the single source of truth for evidence-card structure."""
    return Draft202012Validator(load_schema("evidence_card.schema.json"))


def schema_errors(card: Any) -> list[str]:
    """Return deterministic JSON Schema errors in a stable display order."""
    errors: list[str] = []
    for error in sorted(
        evidence_card_validator().iter_errors(card), key=lambda item: list(item.absolute_path)
    ):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"Schema validation error at {location}: {error.message}")
    return errors


def validate_evidence_card(card: EvidenceCard | dict[str, Any]) -> list[str]:
    """Run schema validation first, then apply psychology-specific guardrails."""
    payload = card.model_dump(mode="json") if isinstance(card, EvidenceCard) else card
    errors = schema_errors(payload)
    if errors:
        return errors
    try:
        validated = EvidenceCard.model_validate(payload)
    except ValidationError as error:
        return [f"Pydantic validation error: {item['msg']}" for item in error.errors()]

    completeness = validated.material_completeness
    study = validated.study
    supported_claims = validated.claim_boundaries.supported_claims

    design = study.design.lower()
    is_cross_sectional = any(marker in design for marker in CROSS_SECTIONAL_MARKERS)
    if is_cross_sectional:
        for index, finding in enumerate(validated.findings):
            if finding.inference_strength in CAUSAL_INFERENCE_STRENGTHS:
                errors.append(
                    "Cross-sectional studies cannot assign causal or intervention inference strength "
                    f"to findings[{index}]."
                )
        for claim in supported_claims:
            claim_text = str(claim).lower()
            if any(term in claim_text for term in CAUSAL_TERMS):
                errors.append(
                    "Cross-sectional studies cannot support causal or intervention claims: "
                    f"{claim!r}."
                )

    if completeness in {"partial", "insufficient"} and not validated.human_review_items:
        errors.append(
            "Partial or insufficient material requires at least one human_review_items entry."
        )

    return errors
