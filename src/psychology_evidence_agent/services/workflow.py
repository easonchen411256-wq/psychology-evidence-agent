"""Composition-friendly handlers for the persisted research workflow.

This module joins existing application services without changing their prompts,
schemas, provider policies, or evidence rules.  Large intermediate values stay
in the run artifact store; ``ResearchRun`` receives only lightweight references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ..configuration import load_search_config
from ..domain.enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    OpenAccessStatus,
    RunStage,
)
from ..domain.errors import ExternalServiceError, LiteratureSearchError, StructuredOutputError
from ..domain.evidence import EvidenceCard
from ..domain.fulltext import FullTextCandidate
from ..domain.paper import Paper
from ..domain.review import ReviewDraft
from ..domain.run import ArtifactReference, ResearchRun, SearchRoundSummary, utc_now
from ..domain.screening import ScreeningDecision
from ..domain.synthesis import EvidenceSynthesis
from ..evidence_synthesis import build_synthesis, write_synthesis_outputs
from ..ports.documents import DocumentReaderPort
from ..ports.fulltext import OpenPdfDownloaderPort
from ..ports.persistence import RunArtifactStore
from ..resources import load_prompt, schema_file
from ..review_draft import render_review_markdown
from ..runtime.human_gate import HumanGate
from ..runtime.step_executor import StepHandler
from ..runtime.step_result import StepResult
from .checkpoints import WorkflowCheckpointStore, bytes_fingerprint, input_fingerprint
from .evidence_extraction import EvidenceExtractionService
from .fulltext import FullTextService
from .literature_search import (
    LiteratureSearchService,
    deduplicate_papers,
    select_screening_candidates,
)
from .review_draft import ReviewDraftService
from .screening import ScreeningService

MAX_INPUT_CHARS = 160_000
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class WorkflowSettings:
    """Operational limits for one run; not a replacement for research config."""

    max_per_query: int = 15
    max_screen: int = 30
    screen_all: bool = False
    screen_batch_size: int = 10
    unpaywall_email: str = ""

    def __post_init__(self) -> None:
        if self.max_per_query < 1 or self.max_screen < 1 or self.screen_batch_size < 1:
            raise ValueError("workflow limits must be positive")


class ResearchWorkflow:
    """Bind the stable workflow stages to injected application services."""

    def __init__(
        self,
        *,
        artifact_store: RunArtifactStore,
        synthesis_store: RunArtifactStore,
        artifact_root: Path,
        literature_search: LiteratureSearchService,
        screening: ScreeningService,
        full_text: FullTextService,
        pdf_downloader: OpenPdfDownloaderPort,
        document_reader: DocumentReaderPort,
        evidence_extraction: EvidenceExtractionService,
        review_draft: ReviewDraftService,
        human_gate: HumanGate,
        settings: WorkflowSettings | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._synthesis_store = synthesis_store
        self._artifact_root = artifact_root.resolve()
        self._literature_search = literature_search
        self._screening = screening
        self._full_text = full_text
        self._pdf_downloader = pdf_downloader
        self._document_reader = document_reader
        self._evidence_extraction = evidence_extraction
        self._review_draft = review_draft
        self._human_gate = human_gate
        self._settings = settings or WorkflowSettings()
        self._checkpoints = WorkflowCheckpointStore(artifact_store)

    def handlers(self) -> dict[RunStage, StepHandler]:
        return {
            RunStage.INITIALIZING: self._initialize,
            RunStage.SEARCHING: self._search,
            RunStage.SCREENING: self._screen,
            RunStage.RETRIEVING_FULLTEXT: self._retrieve_fulltext,
            RunStage.EXTRACTING_EVIDENCE: self._extract_evidence,
            RunStage.SYNTHESIZING: self._synthesize,
            RunStage.DRAFTING: self._draft,
        }

    def _initialize(self, run: ResearchRun) -> StepResult:
        return StepResult(stage=run.stage, success=True, summary="Workflow initialized.")

    def _search(self, run: ResearchRun) -> StepResult:
        name = "search_results.json"
        explicit_config = Path(run.config_reference) if run.config_reference else None
        config = load_search_config(explicit_config)
        queries = cast(list[dict[str, Any]], config["queries"])
        year_from = int(cast(int, config["publication_year_from"]))
        aggregate_fingerprint = input_fingerprint(
            {
                "research_question": run.research_question,
                "config": config,
                "max_per_query": self._settings.max_per_query,
            }
        )
        if self._has_file(name):
            existing = self._artifact_store.load_json(name)
            if (
                isinstance(existing, dict)
                and existing.get("_workflow_input_fingerprint") == aggregate_fingerprint
            ):
                reference = self._reference(name, ArtifactType.SEARCH_RESULTS)
                self._ensure_search_round(run, existing, reference, len(queries))
                return StepResult(
                    stage=run.stage,
                    success=True,
                    artifact_references=[reference],
                    summary="Loaded persisted search results.",
                )

        all_papers: list[Paper] = []
        failures: list[dict[str, str]] = []
        for index, query_definition in enumerate(queries, start=1):
            query_id = str(query_definition["id"])
            query_fingerprint = input_fingerprint(
                {
                    "query": query_definition,
                    "publication_year_from": year_from,
                    "max_per_query": self._settings.max_per_query,
                }
            )
            output_key = (
                f"checkpoints/search/{index}_"
                f"{_safe_component(query_id, fallback=f'query_{index}')}.json"
            )
            cached = self._checkpoints.completed("searching", query_id, query_fingerprint)
            cached_papers = (
                self._load_checkpoint_papers(cached.get("output_key"))
                if cached is not None and cached.get("output_key")
                else None
            )
            if cached_papers is not None:
                all_papers.extend(cached_papers)
                continue

            self._checkpoints.begin("searching", query_id, query_fingerprint)
            try:
                papers = self._literature_search.search(
                    query_id=query_id,
                    query=str(query_definition["query"]),
                    year_from=year_from,
                    per_page=self._settings.max_per_query,
                )
            except LiteratureSearchError as error:
                self._checkpoints.fail(
                    "searching",
                    query_id,
                    query_fingerprint,
                    error=str(error),
                    retryable=True,
                )
                failures.append({"query_id": query_id, "error": str(error)[:300]})
            else:
                self._artifact_store.save_json(
                    output_key,
                    {
                        "query_id": query_id,
                        "papers": [item.model_dump(mode="json") for item in papers],
                    },
                )
                self._checkpoints.complete(
                    "searching", query_id, query_fingerprint, output_key=output_key
                )
                all_papers.extend(papers)

        candidates = deduplicate_papers(all_papers)
        report = {
            "topic": str(config["topic"]),
            "research_question": run.research_question,
            "query_count": len(queries),
            "raw_result_count": len(all_papers),
            "deduplicated_candidate_count": len(candidates),
            "failures": failures,
        }
        payload = {
            "config": config,
            "papers": [paper.model_dump(mode="json") for paper in candidates],
            "report": report,
            "_workflow_input_fingerprint": aggregate_fingerprint,
        }
        self._artifact_store.save_json(name, payload)
        reference = self._reference(name, ArtifactType.SEARCH_RESULTS)
        self._checkpoints.complete(
            "searching", "__aggregate__", aggregate_fingerprint, output_key=name
        )
        self._ensure_search_round(run, payload, reference, len(queries))
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=[reference],
            summary=f"Retrieved {len(candidates)} deduplicated candidate papers.",
        )

    def _screen(self, run: ResearchRun) -> StepResult:
        name = "screening_results.json"
        search_payload = self._artifact_store.load_json("search_results.json")
        papers = self._papers_from_payload(search_payload, "papers")
        shortlist, unscreened = select_screening_candidates(
            papers, self._settings.max_screen, self._settings.screen_all
        )
        screening_resource_fingerprint = _resource_fingerprint(
            "literature_screening_prompt.md", "literature_screening.schema.json"
        )
        aggregate_fingerprint = input_fingerprint(
            {
                "papers": [paper.model_dump(mode="json") for paper in shortlist],
                "research_question": run.research_question,
                "screen_batch_size": self._settings.screen_batch_size,
                "resources": screening_resource_fingerprint,
            }
        )
        if self._has_file(name):
            existing = self._artifact_store.load_json(name)
            if (
                isinstance(existing, dict)
                and existing.get("_workflow_input_fingerprint") == aggregate_fingerprint
            ):
                payload = existing
                reference = self._reference(name, ArtifactType.SCREENING_RESULTS)
            else:
                payload = None
        else:
            payload = None

        if payload is None:
            decisions: list[ScreeningDecision] = []
            prompt_fingerprint = screening_resource_fingerprint
            for index, start in enumerate(
                range(0, len(shortlist), self._settings.screen_batch_size), start=1
            ):
                batch = shortlist[start : start + self._settings.screen_batch_size]
                batch_id = f"batch_{index}"
                batch_fingerprint = input_fingerprint(
                    {
                        "papers": [paper.model_dump(mode="json") for paper in batch],
                        "research_question": run.research_question,
                        "resources": prompt_fingerprint,
                    }
                )
                cached = self._checkpoints.completed("screening", batch_id, batch_fingerprint)
                cached_decisions = (
                    self._load_checkpoint_decisions(cached.get("output_key"))
                    if cached is not None and cached.get("output_key")
                    else None
                )
                if cached_decisions is not None:
                    decisions.extend(cached_decisions)
                    continue

                self._checkpoints.begin("screening", batch_id, batch_fingerprint)
                try:
                    batch_decisions = self._screening.screen_batch(batch, run.research_question)
                except StructuredOutputError as error:
                    self._checkpoints.fail(
                        "screening",
                        batch_id,
                        batch_fingerprint,
                        error=str(error),
                        retryable=False,
                    )
                    raise
                output_key = f"checkpoints/screening/{index}.json"
                self._artifact_store.save_json(
                    output_key,
                    {
                        "batch_id": batch_id,
                        "screened_papers": [
                            item.model_dump(mode="json") for item in batch_decisions
                        ],
                    },
                )
                self._checkpoints.complete(
                    "screening", batch_id, batch_fingerprint, output_key=output_key
                )
                decisions.extend(batch_decisions)

            decisions.sort(key=lambda item: item.relevance_score, reverse=True)
            candidate_by_id = {paper.paper_id: paper for paper in shortlist}
            original_priority = [
                {
                    **candidate_by_id[decision.paper_id].model_dump(mode="json"),
                    **decision.model_dump(mode="json"),
                }
                for decision in decisions
                if decision.paper_id in candidate_by_id
                and decision.evidence_level.value in {"direct", "adjacent"}
                and decision.relevance_score >= 7
            ]
            payload = {
                "screened_papers": [item.model_dump(mode="json") for item in decisions],
                "priority_papers": original_priority,
                "shortlist_papers": [item.model_dump(mode="json") for item in shortlist],
                "unscreened_papers": [item.model_dump(mode="json") for item in unscreened],
                "shortlist_count": len(shortlist),
                "_workflow_input_fingerprint": aggregate_fingerprint,
            }
            self._artifact_store.save_json(name, payload)
            reference = self._reference(name, ArtifactType.SCREENING_RESULTS)

        self._add_reference_if_missing(run, reference)
        decisions = [
            ScreeningDecision.model_validate(item)
            for item in cast(list[Any], payload.get("screened_papers", []))
        ]
        pending_review = next(
            (
                decision
                for decision in decisions
                if decision.human_review_note.strip()
                and self._resolved_screening_decision(run, decision.paper_id) is None
            ),
            None,
        )
        if pending_review is not None:
            action = self._human_gate.request_screening_review(
                run, pending_review, related_artifact_references=[reference]
            )
            return StepResult(
                stage=run.stage,
                success=False,
                blocked=True,
                human_action_id=action.action_id,
            )

        priority = self._effective_priority(payload, decisions, run)
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=[reference],
            summary=f"Screened {len(decisions)} candidate papers; {len(priority)} prioritized.",
        )

    def _retrieve_fulltext(self, run: ResearchRun) -> StepResult:
        screening_payload = self._artifact_store.load_json("screening_results.json")
        selected = self._papers_from_payload(screening_payload, "priority_papers")
        if not selected:
            decisions = [
                ScreeningDecision.model_validate(item)
                for item in cast(list[Any], screening_payload.get("screened_papers", []))
            ]
            selected = self._effective_priority(screening_payload, decisions, run)
        if not selected:
            raise StructuredOutputError("No papers are available for full-text retrieval.")

        metadata_name = "fulltext_metadata.json"
        metadata_reference = self._reference(metadata_name, ArtifactType.FULLTEXT_METADATA)
        metadata_fingerprint = input_fingerprint(
            {
                "selected_papers": [paper.model_dump(mode="json") for paper in selected],
                "email_configured": bool(self._settings.unpaywall_email),
            }
        )
        candidates: list[FullTextCandidate] = []
        failures: list[dict[str, str]] = []
        for index, paper in enumerate(selected, start=1):
            unit_id = f"candidate:{paper.paper_id}"
            candidate_fingerprint = input_fingerprint(
                {
                    "paper": paper.model_dump(mode="json"),
                    "email_configured": bool(self._settings.unpaywall_email),
                }
            )
            cached = self._checkpoints.completed(
                "retrieving_fulltext", unit_id, candidate_fingerprint
            )
            cached_candidate = (
                self._load_checkpoint_candidate(cached.get("output_key"))
                if cached is not None and cached.get("output_key")
                else None
            )
            if cached_candidate is not None:
                candidates.append(cached_candidate)
                continue

            self._checkpoints.begin("retrieving_fulltext", unit_id, candidate_fingerprint)
            try:
                candidate = self._full_text.discover_for_paper(
                    paper=paper, unpaywall_email=self._settings.unpaywall_email
                )
            except ExternalServiceError as error:
                self._checkpoints.fail(
                    "retrieving_fulltext",
                    unit_id,
                    candidate_fingerprint,
                    error=str(error),
                    retryable=True,
                )
                failures.append({"paper_id": paper.paper_id, "error": str(error)[:300]})
            else:
                output_key = f"checkpoints/fulltext/candidate_{index}.json"
                self._artifact_store.save_json(
                    output_key, {"candidate": candidate.model_dump(mode="json")}
                )
                self._checkpoints.complete(
                    "retrieving_fulltext",
                    unit_id,
                    candidate_fingerprint,
                    output_key=output_key,
                )
                candidates.append(candidate)

        self._artifact_store.save_json(
            metadata_name,
            {
                "candidates": [item.model_dump(mode="json") for item in candidates],
                "failures": failures,
                "_workflow_input_fingerprint": metadata_fingerprint,
            },
        )
        self._add_reference_if_missing(run, metadata_reference)
        documents = self._load_document_manifest()
        document_keys = {str(item.get("logical_key", "")) for item in documents}
        skipped: set[str] = set()
        by_paper_id = {candidate.paper_id: candidate for candidate in candidates}
        for index, paper in enumerate(selected, start=1):
            selected_candidate = by_paper_id.get(paper.paper_id)
            if selected_candidate is None:
                skipped.add(paper.paper_id)
                continue
            decision = self._resolved_fulltext_decision(run, paper.paper_id)
            if decision is not None:
                if decision.decision_type is HumanDecisionType.SKIP_PAPER:
                    skipped.add(paper.paper_id)
                    continue
                reference = decision.provided_artifact_reference
                if reference is None or not self._has_file(reference.logical_key):
                    raise StructuredOutputError(
                        f"Provided full text for {paper.paper_id} is not available in the run store."
                    )
                if reference.logical_key not in document_keys:
                    self._upsert_document(
                        documents,
                        {
                            "paper_id": paper.paper_id,
                            "title": paper.title,
                            "logical_key": reference.logical_key,
                            "retrieval_source": "human_provided",
                        },
                    )
                    document_keys.add(reference.logical_key)
                continue

            existing_document = next(
                (item for item in documents if item.get("paper_id") == paper.paper_id), None
            )
            if existing_document is not None and self._has_file(
                str(existing_document.get("logical_key", ""))
            ):
                continue

            if selected_candidate.access_status is OpenAccessStatus.OPEN_PDF_AVAILABLE:
                document_unit_id = f"document:{paper.paper_id}"
                document_fingerprint = input_fingerprint(
                    {
                        "paper": paper.model_dump(mode="json"),
                        "candidate": selected_candidate.model_dump(mode="json"),
                    }
                )
                cached_document = self._checkpoints.completed(
                    "retrieving_fulltext", document_unit_id, document_fingerprint
                )
                cached_entry = (
                    self._load_checkpoint_document(cached_document.get("output_key"))
                    if cached_document is not None and cached_document.get("output_key")
                    else None
                )
                if cached_entry is not None and self._has_file(
                    str(cached_entry.get("logical_key", ""))
                ):
                    self._upsert_document(documents, cached_entry)
                    document_keys.add(str(cached_entry.get("logical_key", "")))
                    continue

                self._checkpoints.begin(
                    "retrieving_fulltext", document_unit_id, document_fingerprint
                )
                try:
                    path = self._pdf_downloader.download(
                        selected_candidate, self._artifact_root / "documents", overwrite=False
                    )
                except (OSError, ValueError, ExternalServiceError) as error:
                    return self._block_for_fulltext(
                        run,
                        selected_candidate,
                        metadata_reference,
                        documents,
                        reason=(
                            f"The public PDF for {selected_candidate.paper_id} was identified but could not "
                            f"be downloaded automatically: {str(error)[:240]}"
                        ),
                    )
                logical_key = path.resolve().relative_to(self._artifact_root).as_posix()
                document_entry = {
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "logical_key": logical_key,
                    "retrieval_source": selected_candidate.retrieval_source,
                }
                self._upsert_document(documents, document_entry)
                document_keys.add(logical_key)
                output_key = f"checkpoints/fulltext/document_{index}.json"
                self._artifact_store.save_json(output_key, {"document": document_entry})
                self._checkpoints.complete(
                    "retrieving_fulltext",
                    document_unit_id,
                    document_fingerprint,
                    output_key=output_key,
                )
                continue

            return self._block_for_fulltext(
                run,
                selected_candidate,
                metadata_reference,
                documents,
                reason=f"No automatically usable lawful full text was found for {selected_candidate.paper_id}.",
            )

        if not documents:
            raise StructuredOutputError("No full-text documents remain after retrieval decisions.")
        manifest_reference = self._save_document_manifest(run, documents, skipped)
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=[metadata_reference, manifest_reference],
            summary=f"Prepared {len(documents)} full-text document(s).",
        )

    def _extract_evidence(self, run: ResearchRun) -> StepResult:
        manifest = self._artifact_store.load_json("fulltext_documents.json")
        documents = cast(list[dict[str, Any]], manifest.get("documents", []))
        if not documents:
            raise StructuredOutputError("The full-text manifest contains no documents.")
        resource_fingerprint = _resource_fingerprint(
            "evidence_extraction_prompt.md", "evidence_card.schema.json"
        )
        references: list[ArtifactReference] = []
        for index, document in enumerate(documents, start=1):
            paper_id = str(document.get("paper_id", f"paper_{index}"))
            logical_key = str(document.get("logical_key", ""))
            document_path = self._artifact_path(logical_key)
            card_name = (
                f"evidence_cards/{_safe_component(paper_id, fallback=f'paper_{index}')}.json"
            )
            document_fingerprint = bytes_fingerprint(document_path.read_bytes())
            unit_fingerprint = input_fingerprint(
                {
                    "paper_id": paper_id,
                    "document_key": logical_key,
                    "document": document_fingerprint,
                    "research_question": run.research_question,
                    "resources": resource_fingerprint,
                }
            )
            cached = self._checkpoints.completed("extracting_evidence", paper_id, unit_fingerprint)
            checkpoint_record = self._checkpoints.record("extracting_evidence", paper_id)
            cached_card = (
                self._load_checkpoint_card(cached.get("output_key"))
                if cached is not None and cached.get("output_key")
                else None
            )
            if cached_card is not None:
                card = cached_card
            else:
                self._checkpoints.begin("extracting_evidence", paper_id, unit_fingerprint)
                try:
                    if self._has_file(card_name) and (
                        checkpoint_record is None
                        or checkpoint_record.get("input_fingerprint") == unit_fingerprint
                    ):
                        try:
                            card = EvidenceCard.model_validate(
                                self._artifact_store.load_json(card_name)
                            )
                        except ValueError:
                            paper_text = self._document_reader.read_text(
                                document_path,
                                max_chars=MAX_INPUT_CHARS,
                                allow_large_input=False,
                            )
                            card = self._evidence_extraction.extract(
                                paper_text=paper_text, research_question=run.research_question
                            )
                            self._artifact_store.save_json(card_name, card.model_dump(mode="json"))
                    else:
                        paper_text = self._document_reader.read_text(
                            document_path,
                            max_chars=MAX_INPUT_CHARS,
                            allow_large_input=False,
                        )
                        card = self._evidence_extraction.extract(
                            paper_text=paper_text, research_question=run.research_question
                        )
                        self._artifact_store.save_json(card_name, card.model_dump(mode="json"))
                except Exception as error:
                    self._checkpoints.fail(
                        "extracting_evidence",
                        paper_id,
                        unit_fingerprint,
                        error=str(error),
                        retryable=False,
                    )
                    raise
                self._checkpoints.complete(
                    "extracting_evidence", paper_id, unit_fingerprint, output_key=card_name
                )
            references.append(self._reference(card_name, ArtifactType.EVIDENCE_CARD))
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=references,
            summary=f"Generated {len(references)} evidence card(s).",
        )

    def _synthesize(self, run: ResearchRun) -> StepResult:
        name = "evidence_synthesis/evidence_synthesis.json"
        card_references = [
            reference
            for reference in run.artifact_references
            if reference.artifact_type is ArtifactType.EVIDENCE_CARD
        ]
        if not card_references:
            raise StructuredOutputError("No evidence-card artifacts are available for synthesis.")
        card_paths = [self._artifact_path(reference.logical_key) for reference in card_references]
        synthesis_fingerprint = input_fingerprint(
            {
                "research_question": run.research_question,
                "cards": [
                    {
                        "logical_key": reference.logical_key,
                        "content": bytes_fingerprint(path.read_bytes()),
                    }
                    for reference, path in zip(card_references, card_paths, strict=True)
                ],
            }
        )
        cached = self._checkpoints.completed("synthesizing", "__aggregate__", synthesis_fingerprint)
        if cached is not None and self._has_file(name):
            reference = self._reference(name, ArtifactType.EVIDENCE_SYNTHESIS)
            return StepResult(
                stage=run.stage,
                success=True,
                artifact_references=[reference],
                summary="Loaded persisted evidence synthesis.",
            )
        synthesis = build_synthesis(card_paths)
        path_names = {
            str(path): path.relative_to(self._artifact_root).as_posix() for path in card_paths
        }
        synthesis = synthesis.model_copy(
            update={
                "cards": [
                    card.model_copy(update={"file": path_names.get(card.file, card.file)})
                    for card in synthesis.cards
                ],
                "skipped_files": [
                    item.model_copy(update={"file": path_names.get(item.file, item.file)})
                    for item in synthesis.skipped_files
                ],
            }
        )
        output_dir = self._artifact_root / "evidence_synthesis"
        write_synthesis_outputs(synthesis, output_dir, self._synthesis_store)
        reference = self._reference(name, ArtifactType.EVIDENCE_SYNTHESIS)
        self._checkpoints.complete(
            "synthesizing", "__aggregate__", synthesis_fingerprint, output_key=name
        )
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=[reference],
            summary=f"Synthesized {synthesis.summary.included_count} evidence card(s).",
        )

    def _draft(self, run: ResearchRun) -> StepResult:
        synthesis_name = "evidence_synthesis/evidence_synthesis.json"
        synthesis_path = self._artifact_path(synthesis_name)
        synthesis = EvidenceSynthesis.model_validate(self._artifact_store.load_json(synthesis_name))
        json_name = "review_draft/review_draft.json"
        markdown_name = "review_draft/review_draft.md"
        reference = self._reference(json_name, ArtifactType.REVIEW_DRAFT)
        draft_fingerprint = input_fingerprint(
            {
                "research_question": run.research_question,
                "synthesis": bytes_fingerprint(synthesis_path.read_bytes()),
                "resources": _resource_fingerprint(
                    "review_draft_prompt.md", "review_draft.schema.json"
                ),
            }
        )
        cached = self._checkpoints.completed("drafting", "__aggregate__", draft_fingerprint)
        if cached is not None and self._has_file(json_name):
            ReviewDraft.model_validate(self._artifact_store.load_json(json_name))
            return StepResult(
                stage=run.stage,
                success=True,
                artifact_references=[reference],
                summary="Loaded persisted review draft.",
            )
        draft = self._review_draft.generate(run.research_question, synthesis)
        self._artifact_store.save_json(json_name, draft.model_dump(mode="json"))
        self._artifact_store.save_text(markdown_name, render_review_markdown(draft))
        self._checkpoints.complete(
            "drafting", "__aggregate__", draft_fingerprint, output_key=json_name
        )
        return StepResult(
            stage=run.stage,
            success=True,
            artifact_references=[reference],
            summary="Generated a traceable review draft.",
        )

    def _block_for_fulltext(
        self,
        run: ResearchRun,
        candidate: FullTextCandidate,
        metadata_reference: ArtifactReference,
        documents: list[dict[str, Any]],
        *,
        reason: str,
    ) -> StepResult:
        manifest_reference = self._save_document_manifest(run, documents, set())
        action = self._human_gate.request_action(
            run,
            action_type=HumanActionType.FULLTEXT_REQUIRED,
            reason=reason,
            related_artifact_references=[metadata_reference, manifest_reference],
            related_paper_ids=[candidate.paper_id],
        )
        return StepResult(
            stage=run.stage,
            success=False,
            blocked=True,
            human_action_id=action.action_id,
        )

    def _save_document_manifest(
        self,
        run: ResearchRun,
        documents: list[dict[str, Any]],
        skipped: set[str],
    ) -> ArtifactReference:
        name = "fulltext_documents.json"
        self._artifact_store.save_json(
            name,
            {"documents": documents, "skipped_paper_ids": sorted(skipped)},
        )
        reference = self._reference(name, ArtifactType.FULLTEXT_DOCUMENT)
        self._add_reference_if_missing(run, reference)
        return reference

    def _load_document_manifest(self) -> list[dict[str, Any]]:
        name = "fulltext_documents.json"
        if not self._has_file(name):
            return []
        payload = self._artifact_store.load_json(name)
        return cast(list[dict[str, Any]], payload.get("documents", []))

    def _resolved_fulltext_decision(self, run: ResearchRun, paper_id: str) -> Any | None:
        for action in run.human_actions:
            if (
                action.action_type is HumanActionType.FULLTEXT_REQUIRED
                and action.status.value == "resolved"
                and paper_id in action.related_paper_ids
                and action.decision is not None
            ):
                return action.decision
        return None

    def _resolved_screening_decision(
        self, run: ResearchRun, paper_id: str
    ) -> HumanDecisionType | None:
        for action in run.human_actions:
            if (
                action.action_type is HumanActionType.SCREENING_REVIEW_REQUIRED
                and action.status is HumanActionStatus.RESOLVED
                and paper_id in action.related_paper_ids
                and action.decision is not None
            ):
                return action.decision.decision_type
        return None

    def _effective_priority(
        self,
        payload: dict[str, Any],
        decisions: list[ScreeningDecision],
        run: ResearchRun,
    ) -> list[Paper]:
        shortlist = self._papers_from_payload(payload, "shortlist_papers")
        candidate_by_id = {paper.paper_id: paper for paper in shortlist}
        original_priority = {
            str(item.get("paper_id", ""))
            for item in cast(list[Any], payload.get("priority_papers", []))
            if isinstance(item, dict)
        }
        selected: list[Paper] = []
        for decision in decisions:
            human_decision = self._resolved_screening_decision(run, decision.paper_id)
            explicitly_included = human_decision in {
                HumanDecisionType.INCLUDE,
                HumanDecisionType.KEEP_UNCERTAIN,
            }
            if human_decision is HumanDecisionType.EXCLUDE:
                continue
            if decision.paper_id in original_priority or explicitly_included:
                paper = candidate_by_id.get(decision.paper_id)
                if paper is not None:
                    selected.append(paper)
        return selected

    def _papers_from_payload(self, payload: Any, field: str) -> list[Paper]:
        if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
            raise StructuredOutputError(f"Workflow artifact field '{field}' must be a JSON array.")
        papers: list[Paper] = []
        for item in cast(list[Any], payload[field]):
            if not isinstance(item, dict):
                raise StructuredOutputError(
                    f"Workflow artifact field '{field}' contains invalid data."
                )
            values = {key: item[key] for key in Paper.model_fields if key in item}
            papers.append(Paper.model_validate(values))
        return papers

    def _load_checkpoint_candidate(self, output_key: Any) -> FullTextCandidate | None:
        if not isinstance(output_key, str) or not output_key:
            return None
        try:
            payload = self._artifact_store.load_json(output_key)
            if not isinstance(payload, dict):
                return None
            return FullTextCandidate.model_validate(payload.get("candidate"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None

    def _load_checkpoint_card(self, output_key: Any) -> EvidenceCard | None:
        if not isinstance(output_key, str) or not output_key:
            return None
        try:
            return EvidenceCard.model_validate(self._artifact_store.load_json(output_key))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None

    def _load_checkpoint_document(self, output_key: Any) -> dict[str, Any] | None:
        if not isinstance(output_key, str) or not output_key:
            return None
        try:
            payload = self._artifact_store.load_json(output_key)
            document = payload.get("document") if isinstance(payload, dict) else None
            if not isinstance(document, dict):
                return None
            if not document.get("paper_id") or not document.get("logical_key"):
                return None
            return {str(key): value for key, value in document.items()}
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None

    def _load_checkpoint_papers(self, output_key: Any) -> list[Paper] | None:
        if not isinstance(output_key, str) or not output_key:
            return None
        try:
            payload = self._artifact_store.load_json(output_key)
            if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
                return None
            return [Paper.model_validate(item) for item in payload["papers"]]
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None

    def _load_checkpoint_decisions(self, output_key: Any) -> list[ScreeningDecision] | None:
        if not isinstance(output_key, str) or not output_key:
            return None
        try:
            payload = self._artifact_store.load_json(output_key)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("screened_papers"), list
            ):
                return None
            return [ScreeningDecision.model_validate(item) for item in payload["screened_papers"]]
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _upsert_document(documents: list[dict[str, Any]], document: dict[str, Any]) -> None:
        paper_id = document.get("paper_id")
        for index, existing in enumerate(documents):
            if existing.get("paper_id") == paper_id:
                documents[index] = document
                return
        documents.append(document)

    @staticmethod
    def _ensure_search_round(
        run: ResearchRun,
        payload: dict[str, Any],
        reference: ArtifactReference,
        fallback_query_count: int,
    ) -> None:
        if any(item.artifact_id == reference.artifact_id for item in run.artifact_references):
            return
        report = payload.get("report")
        report = report if isinstance(report, dict) else {}
        run.search_rounds.append(
            SearchRoundSummary(
                round_number=len(run.search_rounds) + 1,
                query_count=int(report.get("query_count", fallback_query_count)),
                candidate_count=int(report.get("deduplicated_candidate_count", 0)),
                included_count=0,
                artifact_reference=reference,
                completed_at=utc_now(),
            )
        )
        run.touch()

    def _has_file(self, name: str) -> bool:
        return self._artifact_path(name).is_file()

    def _artifact_path(self, name: str) -> Path:
        path = (self._artifact_root / name).resolve()
        if self._artifact_root not in path.parents:
            raise ValueError("Workflow artifact path escapes the run artifact root.")
        return path

    def _reference(
        self, name: str, artifact_type: ArtifactType, metadata: dict[str, str] | None = None
    ) -> ArtifactReference:
        return self._artifact_store.reference(name, artifact_type, metadata)

    @staticmethod
    def _add_reference_if_missing(run: ResearchRun, reference: ArtifactReference) -> None:
        if not any(item.artifact_id == reference.artifact_id for item in run.artifact_references):
            run.add_artifact(reference)


def _safe_component(value: str, *, fallback: str) -> str:
    result = _SAFE_COMPONENT.sub("_", value).strip("._")[:100]
    return result or fallback


def _resource_fingerprint(prompt_name: str, schema_name: str) -> str:
    with schema_file(schema_name) as schema_path:
        schema_fingerprint = bytes_fingerprint(schema_path.read_bytes())
    return input_fingerprint({"prompt": load_prompt(prompt_name), "schema": schema_fingerprint})
