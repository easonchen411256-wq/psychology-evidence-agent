"""Concrete bounded Agent tools for search, screening, and fixed-stage registration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from ...configuration import load_search_config
from ...domain.agent import (
    PlanArgumentValue,
    ToolArgumentSpec,
    ToolArgumentType,
    ToolDescriptor,
    ToolEffect,
    ToolResult,
)
from ...domain.enums import (
    ArtifactType,
    HumanActionStatus,
    HumanActionType,
    HumanDecisionType,
    RunStage,
)
from ...domain.errors import AgentPlanRejectedError, LiteratureSearchError, StructuredOutputError
from ...domain.paper import Paper
from ...domain.quality import QualityAction, QualityStatus, ResearchQualityReport
from ...domain.run import ArtifactReference, ResearchRun, SearchRoundSummary, utc_now
from ...domain.screening import ScreeningDecision
from ...ports.agent_tools import AgentTool, AgentToolRegistry
from ...ports.literature import LiteratureSearchPort
from ...ports.persistence import RunArtifactStore
from ...resources import load_prompt, load_schema
from ...runtime.human_gate import HumanGate
from ...runtime.step_executor import StepExecutor, StepHandler
from ..checkpoints import input_fingerprint
from ..literature_search import deduplicate_papers, select_screening_candidates
from ..screening import ScreeningService

_SEARCH_MANIFEST = "agent/search_queries.json"
_QUALITY_REPORT = "agent/research_quality_report.json"
_SCREENING_MANIFEST = "agent/screening_batches.json"
_SAFE_QUERY_ID = re.compile(r"[^A-Za-z0-9._-]+")


class _ScreeningOptions(TypedDict):
    max_screen: int
    screen_all: bool
    screen_batch_size: int


_DESCRIPTIONS: dict[RunStage, tuple[str, str, ToolEffect, bool, bool]] = {
    RunStage.INITIALIZING: (
        "Initialize the persisted research workflow.",
        "No business artifact; advances the workflow stage.",
        ToolEffect.LOCAL,
        False,
        False,
    ),
    RunStage.SEARCHING: (
        "Search public literature metadata and persist the candidate set.",
        "Search-results artifact and lightweight search summary.",
        ToolEffect.NETWORK,
        False,
        True,
    ),
    RunStage.SCREENING: (
        "Screen candidate metadata with the existing structured-output contract.",
        "Screening-results artifact and possible Human Gate action.",
        ToolEffect.MODEL,
        True,
        False,
    ),
    RunStage.RETRIEVING_FULLTEXT: (
        "Locate lawful full text and request human input when automatic access is unavailable.",
        "Full-text metadata/document artifacts or a Human Gate action.",
        ToolEffect.NETWORK,
        True,
        True,
    ),
    RunStage.EXTRACTING_EVIDENCE: (
        "Extract and validate one or more evidence cards from available full text.",
        "Validated evidence-card artifacts.",
        ToolEffect.MODEL,
        False,
        False,
    ),
    RunStage.SYNTHESIZING: (
        "Build a deterministic evidence synthesis from validated evidence cards.",
        "Evidence-synthesis artifacts.",
        ToolEffect.LOCAL,
        False,
        False,
    ),
    RunStage.DRAFTING: (
        "Generate a traceable review draft from the validated evidence synthesis.",
        "Validated review-draft artifacts.",
        ToolEffect.MODEL,
        False,
        False,
    ),
}


class StageAgentTool:
    """A named capability that delegates to one existing stage executor."""

    def __init__(self, stage: RunStage, executor: StepExecutor) -> None:
        self._stage = stage
        self._executor = executor
        description, output_description, effect, may_request_human, retryable = _DESCRIPTIONS[stage]
        self._descriptor = ToolDescriptor(
            name=f"workflow.{stage.value}",
            description=description,
            output_description=output_description,
            stage=stage,
            effect=effect,
            may_request_human=may_request_human,
            retryable=retryable,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError(f"Tool {self.descriptor.name} does not accept arguments.")
        result = self._executor.execute_one_step(run)
        return ToolResult(
            success=result.success,
            summary=result.summary,
            artifact_references=result.artifact_references,
            blocked=result.blocked,
            human_action_id=result.human_action_id,
            retryable=result.failure.retryable if result.failure is not None else False,
            stage_complete=result.stage_complete,
        )


class AdaptiveSearchQueryTool:
    """Execute one typed search query and keep the workflow in SEARCHING."""

    def __init__(self, artifact_store: RunArtifactStore, search: LiteratureSearchPort) -> None:
        self._artifact_store = artifact_store
        self._search = search
        self._descriptor = ToolDescriptor(
            name="search.execute_query",
            description="Execute one bounded public metadata query for the research brief.",
            output_description="One query artifact containing normalized paper metadata.",
            stage=RunStage.SEARCHING,
            effect=ToolEffect.NETWORK,
            argument_specs=[
                ToolArgumentSpec(
                    name="query_id",
                    value_type=ToolArgumentType.STRING,
                    required=True,
                    description="Stable identifier for this query attempt.",
                ),
                ToolArgumentSpec(
                    name="query",
                    value_type=ToolArgumentType.STRING,
                    required=True,
                    description="The public literature metadata query.",
                ),
                ToolArgumentSpec(
                    name="year_from",
                    value_type=ToolArgumentType.INTEGER,
                    description="Optional publication-year lower bound.",
                ),
                ToolArgumentSpec(
                    name="year_to",
                    value_type=ToolArgumentType.INTEGER,
                    description="Optional publication-year upper bound.",
                ),
                ToolArgumentSpec(
                    name="max_results",
                    value_type=ToolArgumentType.INTEGER,
                    description=(
                        "Maximum provider results for this query; the persisted workflow cap "
                        "is authoritative and safely limits larger requests."
                    ),
                ),
            ],
            retryable=True,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        query_id = _required_string(arguments, "query_id")
        query = _required_string(arguments, "query")
        if not query.strip():
            raise AgentPlanRejectedError("Search query must not be blank.")
        configured_max_results = self._configured_max_results()
        requested_max_results = _optional_integer(arguments, "max_results")
        max_results = (
            configured_max_results if requested_max_results is None else requested_max_results
        )
        if requested_max_results is not None and requested_max_results < 1:
            raise AgentPlanRejectedError("max_results must be a positive integer.")
        max_results = min(max_results, configured_max_results, 100)
        if not 1 <= max_results <= min(100, configured_max_results):
            raise AgentPlanRejectedError(
                f"max_results must be between 1 and {min(100, configured_max_results)}."
            )

        year_from = _optional_integer(arguments, "year_from")
        if year_from is None:
            config = load_search_config(
                Path(run.config_reference) if run.config_reference else None
            )
            year_from = int(config["publication_year_from"])
        if not 1900 <= year_from <= 2100:
            raise AgentPlanRejectedError("year_from must be between 1900 and 2100.")
        year_to = _optional_integer(arguments, "year_to")
        if year_to is not None and not 1900 <= year_to <= 2100:
            raise AgentPlanRejectedError("year_to must be between 1900 and 2100.")
        if year_to is not None and year_to < year_from:
            raise AgentPlanRejectedError("year_to must not be earlier than year_from.")

        fingerprint = input_fingerprint(
            {
                "query_id": query_id,
                "query": query.strip(),
                "year_from": year_from,
                "year_to": year_to,
                "max_results": max_results,
            }
        )
        query_key = f"agent/search_queries/{_safe_query_component(query_id)}.json"
        manifest = _load_search_manifest(self._artifact_store)
        existing = next(
            (item for item in manifest if item.get("query_id") == query_id),
            None,
        )
        if isinstance(existing, dict) and existing.get("input_fingerprint") == fingerprint:
            reference = self._artifact_store.reference(
                query_key,
                ArtifactType.SEARCH_RESULTS,
                metadata={"query_id": query_id},
            )
            return ToolResult(
                success=True,
                summary=f"Reused query results for {query_id}.",
                artifact_references=[reference],
                stage_complete=False,
            )

        try:
            if year_to is None:
                papers = self._search.search(
                    query_id=query_id,
                    query=query.strip(),
                    year_from=year_from,
                    per_page=max_results,
                )
            else:
                papers = self._search.search(
                    query_id=query_id,
                    query=query.strip(),
                    year_from=year_from,
                    year_to=year_to,
                    per_page=max_results,
                )
        except LiteratureSearchError as error:
            return ToolResult(
                success=False,
                summary=f"Search query {query_id} failed: {error}",
                retryable=True,
                stage_complete=False,
            )

        self._artifact_store.save_json(
            query_key,
            {
                "query_id": query_id,
                "query": query.strip(),
                "year_from": year_from,
                "year_to": year_to,
                "max_results": max_results,
                "input_fingerprint": fingerprint,
                "papers": [paper.model_dump(mode="json") for paper in papers],
            },
        )
        manifest = [item for item in manifest if item.get("query_id") != query_id]
        manifest.append(
            {
                "query_id": query_id,
                "query": query.strip(),
                "year_from": year_from,
                "year_to": year_to,
                "max_results": max_results,
                "input_fingerprint": fingerprint,
                "artifact_key": query_key,
            }
        )
        self._artifact_store.save_json(_SEARCH_MANIFEST, {"queries": manifest})
        reference = self._artifact_store.reference(
            query_key,
            ArtifactType.SEARCH_RESULTS,
            metadata={"query_id": query_id},
        )
        return ToolResult(
            success=True,
            summary=f"Retrieved {len(papers)} candidates for query {query_id}.",
            artifact_references=[reference],
            stage_complete=False,
        )

    def _configured_max_results(self) -> int:
        try:
            payload = self._artifact_store.load_json("workflow_options.json")
        except (FileNotFoundError, OSError, ValueError):
            return 15
        value = payload.get("max_per_query") if isinstance(payload, dict) else None
        return value if isinstance(value, int) and 1 <= value <= 100 else 15


class AdaptiveSearchFinalizeTool:
    """Aggregate adaptive query artifacts into the stable workflow search artifact."""

    def __init__(self, artifact_store: RunArtifactStore) -> None:
        self._artifact_store = artifact_store
        self._descriptor = ToolDescriptor(
            name="search.finalize",
            description="Deduplicate completed query artifacts and finalize the search stage.",
            output_description="Stable search_results.json consumed by the existing screening stage.",
            stage=RunStage.SEARCHING,
            effect=ToolEffect.LOCAL,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError("search.finalize does not accept arguments.")
        manifest = _load_search_manifest(self._artifact_store)
        if not manifest:
            return ToolResult(
                success=False,
                summary="Cannot finalize search without at least one completed query.",
            )

        all_papers: list[Paper] = []
        for item in manifest:
            key = item.get("artifact_key")
            if not isinstance(key, str):
                return ToolResult(
                    success=False, summary="Search manifest contains an invalid artifact key."
                )
            try:
                payload = self._artifact_store.load_json(key)
                all_papers.extend(Paper.model_validate(raw) for raw in payload.get("papers", []))
            except (OSError, ValueError, TypeError, AttributeError) as error:
                return ToolResult(
                    success=False, summary=f"Search query artifact is invalid: {error}"
                )

        config = load_search_config(Path(run.config_reference) if run.config_reference else None)
        config["research_question"] = run.research_question
        config["queries"] = [
            {
                "id": item["query_id"],
                "query": item["query"],
                "purpose": "Agent-generated query",
            }
            for item in manifest
        ]
        candidates = deduplicate_papers(all_papers)[: self._configured_candidate_limit()]
        aggregate_fingerprint = input_fingerprint(
            {
                "research_question": run.research_question,
                "queries": manifest,
            }
        )
        payload = {
            "config": config,
            "papers": [paper.model_dump(mode="json") for paper in candidates],
            "report": {
                "topic": str(config["topic"]),
                "research_question": run.research_question,
                "query_count": len(manifest),
                "raw_result_count": len(all_papers),
                "deduplicated_candidate_count": len(candidates),
                "candidate_limit": self._configured_candidate_limit(),
                "failures": [],
                "adaptive": True,
            },
            "_workflow_input_fingerprint": aggregate_fingerprint,
        }
        self._artifact_store.save_json("search_results.json", payload)
        reference = self._artifact_store.reference(
            "search_results.json", ArtifactType.SEARCH_RESULTS
        )
        if not any(
            item.artifact_reference is not None
            and item.artifact_reference.artifact_id == reference.artifact_id
            for item in run.search_rounds
        ):
            run.search_rounds.append(
                SearchRoundSummary(
                    round_number=len(run.search_rounds) + 1,
                    query_count=len(manifest),
                    candidate_count=len(candidates),
                    included_count=0,
                    artifact_reference=reference,
                    completed_at=utc_now(),
                )
            )
        return ToolResult(
            success=True,
            summary=f"Finalized {len(candidates)} deduplicated candidate papers.",
            artifact_references=[reference],
            stage_complete=False,
        )

    def _configured_candidate_limit(self) -> int:
        try:
            payload = self._artifact_store.load_json("workflow_options.json")
        except (FileNotFoundError, OSError, ValueError):
            return 1000
        value = payload.get("candidate_limit") if isinstance(payload, dict) else None
        return value if isinstance(value, int) and 1 <= value <= 1000 else 1000


class AdaptiveSearchCoverageTool:
    """Assess search coverage without exposing paper content to the planner."""

    def __init__(
        self,
        artifact_store: RunArtifactStore,
        *,
        candidate_target: int = 5,
        minimum_new_candidate_ratio: float = 0.2,
        max_search_rounds: int = 3,
    ) -> None:
        if candidate_target < 1 or not 0 <= minimum_new_candidate_ratio <= 1:
            raise ValueError("search quality thresholds are invalid")
        if max_search_rounds < 1:
            raise ValueError("max_search_rounds must be positive")
        self._artifact_store = artifact_store
        self._candidate_target = candidate_target
        self._minimum_new_candidate_ratio = minimum_new_candidate_ratio
        self._max_search_rounds = max_search_rounds
        self._descriptor = ToolDescriptor(
            name="search.assess_coverage",
            description="Assess bounded search coverage from metadata counts and scope signals.",
            output_description="Metadata-only ResearchQualityReport used for proceed or re-plan.",
            stage=RunStage.SEARCHING,
            effect=ToolEffect.LOCAL,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError("search.assess_coverage does not accept arguments.")
        manifest = _load_search_manifest(self._artifact_store)
        finalized_references: list[ArtifactReference] = []
        try:
            search_payload = self._artifact_store.load_json("search_results.json")
        except FileNotFoundError:
            finalized = AdaptiveSearchFinalizeTool(self._artifact_store).execute(run, {})
            if not finalized.success:
                return finalized
            finalized_references.extend(finalized.artifact_references)
            try:
                search_payload = self._artifact_store.load_json("search_results.json")
            except (OSError, ValueError) as error:
                return ToolResult(success=False, summary=f"Search results are unavailable: {error}")
        except (OSError, ValueError) as error:
            return ToolResult(success=False, summary=f"Search results are unavailable: {error}")
        raw_papers = search_payload.get("papers") if isinstance(search_payload, dict) else None
        if not isinstance(raw_papers, list):
            return ToolResult(success=False, summary="Search results do not contain a paper list.")
        try:
            candidates = [Paper.model_validate(item) for item in raw_papers]
        except (TypeError, ValueError) as error:
            return ToolResult(success=False, summary=f"Search results are invalid: {error}")

        raw_result_count = _manifest_raw_count(self._artifact_store, manifest)
        candidate_ids = sorted({paper.paper_id for paper in candidates})
        previous_ids = self._previous_candidate_ids()
        new_candidate_count = (
            len(candidate_ids) if previous_ids is None else len(set(candidate_ids) - previous_ids)
        )
        duplicate_count = max(0, raw_result_count - len(candidate_ids))
        new_candidate_ratio = _ratio(new_candidate_count, len(candidate_ids))
        duplicate_ratio = _ratio(duplicate_count, raw_result_count)
        multi_query_count = sum(paper.query_coverage >= 2 for paper in candidates)
        max_query_coverage = max((paper.query_coverage for paper in candidates), default=0)
        scope_signal_count = self._scope_signal_count()
        query_count = len(manifest)
        candidate_target = self._configured_candidate_target()
        target_met = len(candidate_ids) >= candidate_target
        saturated = query_count > 1 and new_candidate_ratio < self._minimum_new_candidate_ratio
        if target_met:
            status = QualityStatus.SUFFICIENT
            action = QualityAction.PROCEED
            if saturated:
                reason = (
                    f"{len(candidate_ids)} candidates meet the target of {candidate_target}; "
                    "additional query novelty is below the configured threshold."
                )
            else:
                reason = (
                    f"{len(candidate_ids)} deduplicated candidates meet the target "
                    f"of {candidate_target}."
                )
        elif query_count < self._max_search_rounds:
            status = QualityStatus.NEEDS_MORE_SEARCH
            action = QualityAction.REPLAN_SEARCH
            reason = (
                f"Only {len(candidate_ids)} deduplicated candidates are available; "
                "another bounded search round is allowed."
            )
        else:
            status = QualityStatus.LIMIT_REACHED
            action = QualityAction.STOP
            reason = (
                f"The search limit of {self._max_search_rounds} query rounds was reached "
                f"with {len(candidate_ids)} deduplicated candidates."
            )

        report = ResearchQualityReport(
            stage=run.stage,
            query_count=query_count,
            raw_result_count=raw_result_count,
            deduplicated_candidate_count=len(candidate_ids),
            new_candidate_count=new_candidate_count,
            duplicate_count=duplicate_count,
            new_candidate_ratio=new_candidate_ratio,
            duplicate_ratio=duplicate_ratio,
            multi_query_candidate_count=multi_query_count,
            max_query_coverage=max_query_coverage,
            scope_signal_count=scope_signal_count,
            candidate_target=candidate_target,
            minimum_new_candidate_ratio=self._minimum_new_candidate_ratio,
            max_search_rounds=self._max_search_rounds,
            candidate_paper_ids=candidate_ids,
            fulltext_count=_artifact_count(run, ArtifactType.FULLTEXT_DOCUMENT),
            evidence_card_count=_artifact_count(run, ArtifactType.EVIDENCE_CARD),
            unresolved_human_gate_count=sum(
                action.status is HumanActionStatus.PENDING for action in run.human_actions
            ),
            status=status,
            recommended_action=action,
            reason=reason,
        )
        self._artifact_store.save_json(_QUALITY_REPORT, report.model_dump(mode="json"))
        reference = self._artifact_store.reference(
            _QUALITY_REPORT, ArtifactType.RESEARCH_QUALITY_REPORT
        )
        if status is QualityStatus.LIMIT_REACHED:
            return ToolResult(
                success=False,
                summary=reason,
                artifact_references=finalized_references,
            )
        return ToolResult(
            success=True,
            summary=reason,
            artifact_references=[*finalized_references, reference],
            stage_complete=status is QualityStatus.SUFFICIENT,
            replan_required=status is QualityStatus.NEEDS_MORE_SEARCH,
        )

    def _previous_candidate_ids(self) -> set[str] | None:
        try:
            payload = self._artifact_store.load_json(_QUALITY_REPORT)
            report = ResearchQualityReport.model_validate(payload)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        return set(report.candidate_paper_ids)

    def _configured_candidate_target(self) -> int:
        try:
            payload = self._artifact_store.load_json("workflow_options.json")
        except (FileNotFoundError, OSError, ValueError):
            return self._candidate_target
        value = payload.get("candidate_limit") if isinstance(payload, dict) else None
        return value if isinstance(value, int) and value >= 1 else self._candidate_target

    def _scope_signal_count(self) -> int:
        try:
            payload = self._artifact_store.load_json("agent/research_brief.json")
        except (FileNotFoundError, OSError, ValueError):
            return 1
        if not isinstance(payload, dict):
            return 1
        names = (
            "population",
            "intervention_or_exposure",
            "comparison",
            "outcomes",
            "inclusion_criteria",
            "exclusion_criteria",
            "languages",
            "study_types",
            "year_from",
            "year_to",
        )
        return 1 + sum(bool(payload.get(name)) for name in names)


class AdaptiveScreeningExecuteTool:
    """Run the existing title-and-abstract screening service as a bounded tool."""

    def __init__(self, artifact_store: RunArtifactStore, screening: ScreeningService) -> None:
        self._artifact_store = artifact_store
        self._screening = screening
        self._descriptor = ToolDescriptor(
            name="screen.execute_batches",
            description="Screen bounded candidate metadata using the existing structured contract.",
            output_description="Persisted screening batch artifacts for the finalization tool.",
            stage=RunStage.SCREENING,
            effect=ToolEffect.MODEL,
            argument_specs=[
                ToolArgumentSpec(
                    name="max_screen",
                    value_type=ToolArgumentType.INTEGER,
                    description="Maximum number of ranked candidates to screen.",
                ),
                ToolArgumentSpec(
                    name="screen_all",
                    value_type=ToolArgumentType.BOOLEAN,
                    description="Whether to screen the complete candidate set.",
                ),
                ToolArgumentSpec(
                    name="batch_size",
                    value_type=ToolArgumentType.INTEGER,
                    description="Maximum candidates sent to one structured-output call.",
                ),
            ],
            retryable=True,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        options = self._load_options()
        requested_max_screen = _optional_integer(arguments, "max_screen")
        max_screen = (
            options["max_screen"]
            if requested_max_screen is None
            else min(requested_max_screen, options["max_screen"])
        )
        requested_screen_all = _optional_boolean(arguments, "screen_all")
        screen_all = (
            options["screen_all"]
            if requested_screen_all is None
            else (options["screen_all"] and requested_screen_all)
        )
        requested_batch_size = _optional_integer(arguments, "batch_size")
        batch_size = (
            options["screen_batch_size"]
            if requested_batch_size is None
            else min(requested_batch_size, options["screen_batch_size"])
        )
        if not 1 <= max_screen <= 1000:
            raise AgentPlanRejectedError("max_screen must be between 1 and 1000.")
        if not 1 <= batch_size <= 100:
            raise AgentPlanRejectedError("batch_size must be between 1 and 100.")

        try:
            search_payload = self._artifact_store.load_json("search_results.json")
            papers = _papers_from_payload(search_payload, "papers")
        except (FileNotFoundError, OSError, TypeError, ValueError) as error:
            return ToolResult(success=False, summary=f"Search results are unavailable: {error}")
        shortlist, unscreened = select_screening_candidates(papers, max_screen, screen_all)
        aggregate_fingerprint = input_fingerprint(
            {
                "papers": [paper.model_dump(mode="json") for paper in shortlist],
                "research_question": run.research_question,
                "max_screen": max_screen,
                "screen_all": screen_all,
                "screen_batch_size": batch_size,
                "prompt": load_prompt("literature_screening_prompt.md"),
                "schema": load_schema("literature_screening.schema.json"),
            }
        )
        existing = _load_screening_manifest(self._artifact_store)
        if (
            existing is not None
            and existing.get("input_fingerprint") == aggregate_fingerprint
            and _screening_manifest_is_valid(self._artifact_store, existing)
        ):
            reference = self._artifact_store.reference(
                _SCREENING_MANIFEST,
                ArtifactType.SCREENING_RESULTS,
                metadata={"phase": "batches"},
            )
            return ToolResult(
                success=True,
                summary="Reused persisted screening batches.",
                artifact_references=[reference],
                stage_complete=False,
            )

        batches: list[dict[str, Any]] = []
        for index, start in enumerate(range(0, len(shortlist), batch_size), start=1):
            batch = shortlist[start : start + batch_size]
            batch_id = f"batch_{index}"
            batch_fingerprint = input_fingerprint(
                {
                    "batch_id": batch_id,
                    "papers": [paper.model_dump(mode="json") for paper in batch],
                    "research_question": run.research_question,
                    "prompt": load_prompt("literature_screening_prompt.md"),
                    "schema": load_schema("literature_screening.schema.json"),
                }
            )
            decisions = None
            last_error: StructuredOutputError | None = None
            for _attempt in range(2):
                try:
                    decisions = self._screening.screen_batch(batch, run.research_question)
                    break
                except StructuredOutputError as error:
                    last_error = error
            if decisions is None:
                return ToolResult(
                    success=False,
                    summary=(
                        f"Screening batch {batch_id} failed contract validation: {last_error}"
                    ),
                    retryable=True,
                )
            artifact_key = f"agent/screening_batches/{batch_id}.json"
            self._artifact_store.save_json(
                artifact_key,
                {
                    "batch_id": batch_id,
                    "input_fingerprint": batch_fingerprint,
                    "paper_ids": [paper.paper_id for paper in batch],
                    "screened_papers": [item.model_dump(mode="json") for item in decisions],
                },
            )
            batches.append(
                {
                    "batch_id": batch_id,
                    "artifact_key": artifact_key,
                    "paper_ids": [paper.paper_id for paper in batch],
                    "input_fingerprint": batch_fingerprint,
                }
            )

        manifest = {
            "input_fingerprint": aggregate_fingerprint,
            "research_question": run.research_question,
            "settings": {
                "max_screen": max_screen,
                "screen_all": screen_all,
                "screen_batch_size": batch_size,
            },
            "shortlist_paper_ids": [paper.paper_id for paper in shortlist],
            "unscreened_paper_ids": [paper.paper_id for paper in unscreened],
            "batches": batches,
        }
        self._artifact_store.save_json(_SCREENING_MANIFEST, manifest)
        reference = self._artifact_store.reference(
            _SCREENING_MANIFEST,
            ArtifactType.SCREENING_RESULTS,
            metadata={"phase": "batches"},
        )
        return ToolResult(
            success=True,
            summary=f"Screened {len(shortlist)} candidate papers in {len(batches)} batch(es).",
            artifact_references=[reference],
            stage_complete=False,
        )

    def _load_options(self) -> _ScreeningOptions:
        try:
            payload = self._artifact_store.load_json("workflow_options.json")
        except (FileNotFoundError, OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "max_screen": _positive_option(payload.get("max_screen"), 30),
            "screen_all": bool(payload.get("screen_all", False)),
            "screen_batch_size": _positive_option(payload.get("screen_batch_size"), 10),
        }


class AdaptiveScreeningFinalizeTool:
    """Create the existing screening artifact and preserve its Human Gate behavior."""

    def __init__(self, artifact_store: RunArtifactStore, human_gate: HumanGate) -> None:
        self._artifact_store = artifact_store
        self._human_gate = human_gate
        self._descriptor = ToolDescriptor(
            name="screen.finalize",
            description="Validate screening batches and publish the stable screening artifact.",
            output_description="screening_results.json plus an optional screening Human Gate.",
            stage=RunStage.SCREENING,
            effect=ToolEffect.LOCAL,
            may_request_human=True,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError("screen.finalize does not accept arguments.")
        manifest = _load_screening_manifest(self._artifact_store)
        if manifest is None or not _screening_manifest_is_valid(self._artifact_store, manifest):
            return ToolResult(success=False, summary="No valid screening batches are available.")
        try:
            search_payload = self._artifact_store.load_json("search_results.json")
            papers = _papers_from_payload(search_payload, "papers")
            decisions = _load_screening_decisions(self._artifact_store, manifest)
        except (FileNotFoundError, OSError, TypeError, ValueError, StructuredOutputError) as error:
            return ToolResult(success=False, summary=f"Screening artifacts are invalid: {error}")

        shortlist_ids = [str(item) for item in manifest.get("shortlist_paper_ids", [])]
        unscreened_ids = {str(item) for item in manifest.get("unscreened_paper_ids", [])}
        paper_by_id = {paper.paper_id: paper for paper in papers}
        shortlist = [paper_by_id[paper_id] for paper_id in shortlist_ids if paper_id in paper_by_id]
        candidate_ids = {paper.paper_id for paper in shortlist}
        decision_ids = [decision.paper_id for decision in decisions]
        if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != candidate_ids:
            return ToolResult(
                success=False,
                summary="Screening batches are incomplete or contain unknown paper IDs.",
            )
        decisions.sort(key=lambda item: item.relevance_score, reverse=True)

        reference = self._artifact_store.reference(
            "screening_results.json", ArtifactType.SCREENING_RESULTS
        )
        candidate_by_id = {paper.paper_id: paper for paper in shortlist}
        original_priority = [
            {
                **candidate_by_id[decision.paper_id].model_dump(mode="json"),
                **decision.model_dump(mode="json"),
            }
            for decision in decisions
            if decision.evidence_level.value in {"direct", "adjacent"}
            and decision.relevance_score >= 7
        ]
        priority_ids = {item["paper_id"] for item in original_priority if isinstance(item, dict)}
        priority = [
            candidate_by_id[decision.paper_id]
            for decision in decisions
            if _resolved_screening_decision(run, decision.paper_id)
            not in {HumanDecisionType.EXCLUDE}
            and (
                decision.paper_id in priority_ids
                or _resolved_screening_decision(run, decision.paper_id)
                in {HumanDecisionType.INCLUDE, HumanDecisionType.KEEP_UNCERTAIN}
            )
        ]
        payload = {
            "screened_papers": [item.model_dump(mode="json") for item in decisions],
            "priority_papers": [item.model_dump(mode="json") for item in priority],
            "shortlist_papers": [item.model_dump(mode="json") for item in shortlist],
            "unscreened_papers": [
                paper.model_dump(mode="json")
                for paper in papers
                if paper.paper_id in unscreened_ids
            ],
            "shortlist_count": len(shortlist),
            "_workflow_input_fingerprint": str(manifest["input_fingerprint"]),
        }
        self._artifact_store.save_json("screening_results.json", payload)
        pending_review = next(
            (
                decision
                for decision in decisions
                if decision.human_review_note.strip()
                and _resolved_screening_decision(run, decision.paper_id) is None
            ),
            None,
        )
        if pending_review is not None:
            action = self._human_gate.request_screening_review(
                run, pending_review, related_artifact_references=[reference]
            )
            return ToolResult(
                success=False,
                blocked=True,
                human_action_id=action.action_id,
                summary="Screening requires a Human Gate decision.",
            )
        return ToolResult(
            success=True,
            summary=f"Finalized screening for {len(decisions)} candidate papers.",
            artifact_references=[reference],
        )


class WorkflowToolRegistry(AgentToolRegistry):
    """Registry containing only the existing, approved workflow capabilities."""

    def __init__(
        self,
        handlers: Mapping[RunStage, StepHandler],
        extra_tools: tuple[AgentTool, ...] = (),
    ) -> None:
        executor = StepExecutor(handlers)
        self._tools: dict[str, AgentTool] = {
            f"workflow.{stage.value}": StageAgentTool(stage, executor) for stage in RunStage
        }
        for tool in extra_tools:
            if tool.descriptor.name in self._tools:
                raise ValueError(f"Duplicate Agent tool: {tool.descriptor.name}")
            self._tools[tool.descriptor.name] = tool

    def descriptors(self) -> list[ToolDescriptor]:
        return [self._tools[name].descriptor for name in sorted(self._tools)]

    def get(self, name: str) -> AgentTool:
        tool = self._tools.get(name)
        if tool is None:
            raise AgentPlanRejectedError(f"Tool '{name}' is not registered.")
        return tool


def _load_search_manifest(artifact_store: RunArtifactStore) -> list[dict[str, Any]]:
    try:
        payload = artifact_store.load_json(_SEARCH_MANIFEST)
    except (FileNotFoundError, OSError, ValueError):
        return []
    queries = payload.get("queries") if isinstance(payload, dict) else None
    return [item for item in queries if isinstance(item, dict)] if isinstance(queries, list) else []


def _load_screening_manifest(artifact_store: RunArtifactStore) -> dict[str, Any] | None:
    try:
        payload = artifact_store.load_json(_SCREENING_MANIFEST)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _screening_manifest_is_valid(
    artifact_store: RunArtifactStore, manifest: dict[str, Any]
) -> bool:
    batches = manifest.get("batches")
    shortlist_ids = manifest.get("shortlist_paper_ids")
    if not isinstance(batches, list) or not isinstance(shortlist_ids, list):
        return False
    try:
        decisions = _load_screening_decisions(artifact_store, manifest)
    except (FileNotFoundError, OSError, TypeError, ValueError, StructuredOutputError):
        return False
    return {decision.paper_id for decision in decisions} == {str(item) for item in shortlist_ids}


def _load_screening_decisions(
    artifact_store: RunArtifactStore, manifest: dict[str, Any]
) -> list[ScreeningDecision]:
    batches = manifest.get("batches")
    if not isinstance(batches, list):
        raise StructuredOutputError("Screening manifest batches must be a JSON array.")
    decisions: list[ScreeningDecision] = []
    seen: set[str] = set()
    for item in batches:
        if not isinstance(item, dict) or not isinstance(item.get("artifact_key"), str):
            raise StructuredOutputError("Screening manifest contains an invalid batch entry.")
        payload = artifact_store.load_json(item["artifact_key"])
        raw_decisions = payload.get("screened_papers") if isinstance(payload, dict) else None
        if not isinstance(raw_decisions, list):
            raise StructuredOutputError("Screening batch is missing screened_papers.")
        batch_ids = item.get("paper_ids")
        if not isinstance(batch_ids, list):
            raise StructuredOutputError("Screening batch is missing paper_ids.")
        parsed = [ScreeningDecision.model_validate(raw) for raw in raw_decisions]
        parsed_ids = [decision.paper_id for decision in parsed]
        if len(parsed_ids) != len(batch_ids) or set(parsed_ids) != {
            str(value) for value in batch_ids
        }:
            raise StructuredOutputError("Screening batch output does not match its input IDs.")
        if seen.intersection(parsed_ids):
            raise StructuredOutputError("Screening batches contain duplicate paper IDs.")
        seen.update(parsed_ids)
        decisions.extend(parsed)
    return decisions


def _papers_from_payload(payload: Any, field: str) -> list[Paper]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise ValueError(f"Workflow artifact field '{field}' must be a JSON array.")
    papers: list[Paper] = []
    for item in payload[field]:
        if not isinstance(item, dict):
            raise ValueError(f"Workflow artifact field '{field}' contains invalid data.")
        papers.append(Paper.model_validate(item))
    return papers


def _resolved_screening_decision(run: ResearchRun, paper_id: str) -> HumanDecisionType | None:
    for action in run.human_actions:
        if (
            action.action_type is HumanActionType.SCREENING_REVIEW_REQUIRED
            and action.status is HumanActionStatus.RESOLVED
            and paper_id in action.related_paper_ids
            and action.decision is not None
        ):
            return action.decision.decision_type
    return None


def _positive_option(value: Any, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _manifest_raw_count(artifact_store: RunArtifactStore, manifest: list[dict[str, Any]]) -> int:
    total = 0
    for item in manifest:
        key = item.get("artifact_key")
        if not isinstance(key, str):
            continue
        try:
            payload = artifact_store.load_json(key)
        except (FileNotFoundError, OSError, ValueError):
            continue
        papers = payload.get("papers") if isinstance(payload, dict) else None
        if isinstance(papers, list):
            total += len(papers)
    return total


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _artifact_count(run: ResearchRun, artifact_type: ArtifactType) -> int:
    return sum(reference.artifact_type is artifact_type for reference in run.artifact_references)


def _safe_query_component(query_id: str) -> str:
    value = _SAFE_QUERY_ID.sub("_", query_id.strip()).strip("._")
    if not value:
        raise AgentPlanRejectedError("query_id must contain at least one safe character.")
    return value[:80]


def _required_string(arguments: Mapping[str, PlanArgumentValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgentPlanRejectedError(f"Argument '{name}' must be a non-empty string.")
    return value


def _required_integer(arguments: Mapping[str, PlanArgumentValue], name: str) -> int:
    value = arguments.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AgentPlanRejectedError(f"Argument '{name}' must be an integer.")
    return value


def _optional_integer(arguments: Mapping[str, PlanArgumentValue], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise AgentPlanRejectedError(f"Argument '{name}' must be an integer.")
    return value


def _optional_boolean(arguments: Mapping[str, PlanArgumentValue], name: str) -> bool | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise AgentPlanRejectedError(f"Argument '{name}' must be a boolean.")
    return value
