import unittest

from pydantic import ValidationError

from psychology_evidence_agent.domain.enums import RunStage
from psychology_evidence_agent.domain.evidence import EvidenceCard
from psychology_evidence_agent.domain.fulltext import FullTextCandidate
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.domain.quality import (
    QualityAction,
    QualityStatus,
    ResearchQualityReport,
)
from psychology_evidence_agent.domain.review import ReviewDraft
from psychology_evidence_agent.domain.screening import ScreeningResult
from psychology_evidence_agent.domain.synthesis import EvidenceSynthesis
from psychology_evidence_agent.schema_tools import check_schemas, generated_schemas


def evidence_card_payload() -> dict:
    return {
        "source": {
            "title": "Example paper",
            "authors": ["Example Author"],
            "year": "2026",
            "journal": "Example Journal",
            "doi_or_url": "未报告",
        },
        "material_completeness": "complete",
        "study": {
            "research_question": "Question",
            "design": "cross-sectional",
            "sample": "100 adults",
            "measures": ["stress"],
            "analysis": "regression",
        },
        "findings": [],
        "limitations": [],
        "claim_boundaries": {"supported_claims": [], "unsupported_claims": []},
        "human_review_items": [],
    }


class DomainModelTests(unittest.TestCase):
    def test_paper_model_validates_canonical_search_fields(self):
        paper = Paper.model_validate({"paper_id": "W1", "title": "Example", "year": 2026})
        self.assertEqual(paper.paper_id, "W1")
        self.assertEqual(paper.authors, [])

    def test_screening_rejects_invalid_enum_and_unknown_llm_field(self):
        payload = {
            "screened_papers": [
                {
                    "paper_id": "W1",
                    "relevance_score": 8,
                    "evidence_level": "not_a_level",
                    "subtopic": "topic",
                    "rationale": "reason",
                    "human_review_note": "review",
                    "unexpected": "not allowed",
                }
            ]
        }
        with self.assertRaises(ValidationError):
            ScreeningResult.model_validate(payload)

    def test_evidence_card_rejects_missing_required_field_and_round_trips(self):
        payload = evidence_card_payload()
        del payload["study"]["analysis"]
        with self.assertRaises(ValidationError):
            EvidenceCard.model_validate(payload)
        card = EvidenceCard.model_validate(evidence_card_payload())
        self.assertEqual(EvidenceCard.model_validate_json(card.model_dump_json()), card)

    def test_synthesis_and_review_models_validate_persisted_contracts(self):
        synthesis = EvidenceSynthesis.model_validate(
            {
                "summary": {
                    "input_count": 0,
                    "included_count": 0,
                    "skipped_count": 0,
                    "role_counts": {},
                },
                "cards": [],
                "skipped_files": [],
            }
        )
        self.assertEqual(synthesis.summary.included_count, 0)
        draft = ReviewDraft.model_validate(
            {
                "title": "Draft",
                "research_question": "Question",
                "evidence_scope": "Scope",
                "sections": [
                    {
                        "heading": name,
                        "paragraph": "Text",
                        "supporting_card_files": [],
                        "claims": [],
                        "caveat": "Caution",
                    }
                    for name in ("A", "B", "C")
                ],
                "evidence_gaps": [],
                "human_review_items": [],
            }
        )
        self.assertEqual(len(draft.sections), 3)

    def test_full_text_candidate_rejects_unknown_access_status(self):
        with self.assertRaises(ValidationError):
            FullTextCandidate.model_validate(
                {
                    "retrieval_source": "openalex",
                    "paper_id": "W1",
                    "is_open_access": False,
                    "access_status": "unknown",
                    "next_step": "Review manually.",
                }
            )

    def test_research_quality_report_is_metadata_only_and_has_consistent_status(self):
        report = ResearchQualityReport(
            stage=RunStage.SEARCHING,
            query_count=1,
            raw_result_count=4,
            deduplicated_candidate_count=3,
            new_candidate_count=3,
            duplicate_count=1,
            new_candidate_ratio=1.0,
            duplicate_ratio=0.25,
            multi_query_candidate_count=1,
            max_query_coverage=2,
            scope_signal_count=2,
            candidate_target=5,
            minimum_new_candidate_ratio=0.2,
            max_search_rounds=3,
            candidate_paper_ids=["W1", "W2", "W3"],
            status=QualityStatus.NEEDS_MORE_SEARCH,
            recommended_action=QualityAction.REPLAN_SEARCH,
            reason="More bounded search is allowed.",
        )
        self.assertEqual(report.candidate_paper_ids, ["W1", "W2", "W3"])
        self.assertNotIn("abstract", report.model_dump())

    def test_generated_schemas_match_committed_artifacts(self):
        self.assertEqual(check_schemas(), [])

    def test_codex_schemas_inline_all_pydantic_references(self):
        def refs(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    yield value["$ref"]
                for item in value.values():
                    yield from refs(item)
            elif isinstance(value, list):
                for item in value:
                    yield from refs(item)

        references = [
            reference for schema in generated_schemas().values() for reference in refs(schema)
        ]
        self.assertEqual(references, [])

    def test_codex_schemas_require_every_object_property(self):
        def object_requirements(value):
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    self.assertEqual(list(properties), value.get("required"))
                for item in value.values():
                    object_requirements(item)
            elif isinstance(value, list):
                for item in value:
                    object_requirements(item)

        for schema in generated_schemas().values():
            object_requirements(schema)


if __name__ == "__main__":
    unittest.main()
