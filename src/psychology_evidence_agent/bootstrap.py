"""Composition root: the sole location that binds ports to concrete adapters."""

import os
from collections.abc import Callable
from pathlib import Path

from .adapters.documents.pdf_reader import PdfReaderAdapter
from .adapters.fulltext.open_access import OpenAccessAdapter
from .adapters.fulltext.pdf_downloader import OpenPdfDownloaderAdapter
from .adapters.literature.openalex import OpenAlexAdapter
from .adapters.llm.codex_cli import CodexCliAdapter
from .adapters.llm.openai_compatible import ModelConnection, OpenAICompatibleAdapter
from .adapters.persistence.artifact_store import FileSystemArtifactStore
from .adapters.persistence.event_store import FileSystemAgentEventStore
from .adapters.persistence.run_lock import FileSystemResearchRunLock
from .adapters.persistence.run_store import FileSystemResearchRunStore
from .domain.agent import ExecutionBudget
from .ports.llm import StructuredOutputPort
from .runtime.agent_controller import AgentController
from .runtime.human_gate import HumanGate
from .runtime.orchestrator import EvidenceAgent
from .runtime.state_machine import RunStateMachine
from .runtime.step_executor import StepExecutor
from .services.agent_tools import (
    AdaptiveEvidenceFinalizeTool,
    AdaptiveEvidencePrepareTool,
    AdaptiveEvidenceProcessNextTool,
    AdaptiveFullTextFinalizeTool,
    AdaptiveFullTextPrepareTool,
    AdaptiveFullTextProcessNextTool,
    AdaptiveScreeningExecuteTool,
    AdaptiveScreeningFinalizeTool,
    AdaptiveSearchCoverageTool,
    AdaptiveSearchFinalizeTool,
    AdaptiveSearchQueryTool,
    WorkflowToolRegistry,
)
from .services.evidence_extraction import EvidenceExtractionService
from .services.fulltext import FullTextService
from .services.literature_search import LiteratureSearchService
from .services.planning import CodexPlanner, DeterministicPlanner
from .services.review_draft import ReviewDraftService
from .services.screening import ScreeningService
from .services.workflow import ResearchWorkflow, WorkflowSettings


def literature_search_service() -> LiteratureSearchService:
    return LiteratureSearchService(OpenAlexAdapter())


def structured_output_adapter(
    connection: ModelConnection | None = None,
) -> StructuredOutputPort:
    if connection is None:
        return CodexCliAdapter()
    return OpenAICompatibleAdapter(connection)


def evidence_extraction_service(
    structured_output: StructuredOutputPort | None = None,
) -> EvidenceExtractionService:
    return EvidenceExtractionService(structured_output or CodexCliAdapter())


def screening_service(structured_output: StructuredOutputPort | None = None) -> ScreeningService:
    return ScreeningService(structured_output or CodexCliAdapter())


def review_draft_service(
    structured_output: StructuredOutputPort | None = None,
) -> ReviewDraftService:
    return ReviewDraftService(structured_output or CodexCliAdapter())


def full_text_service() -> FullTextService:
    return FullTextService(OpenAccessAdapter())


def document_reader() -> PdfReaderAdapter:
    return PdfReaderAdapter()


def artifact_store(root: Path) -> FileSystemArtifactStore:
    return FileSystemArtifactStore(root)


def research_run_store(root: Path) -> FileSystemResearchRunStore:
    return FileSystemResearchRunStore(root)


def research_run_lock(root: Path, run_id: str) -> FileSystemResearchRunLock:
    return FileSystemResearchRunLock(root, run_id)


def human_gate(root: Path) -> HumanGate:
    """Compose the persisted human gate."""
    return HumanGate(RunStateMachine(), research_run_store(root))


def evidence_agent(
    root: Path,
    run_id: str,
    *,
    settings: WorkflowSettings | None = None,
) -> EvidenceAgent:
    """Compose the real service workflow for one persisted ResearchRun."""
    run_store, _, workflow, step_executor = _workflow_components(root, run_id, settings)
    return EvidenceAgent(
        run_store=run_store,
        state_machine=RunStateMachine(),
        step_executor=step_executor,
    )


def agent_controller(
    root: Path,
    run_id: str,
    *,
    settings: WorkflowSettings | None = None,
    budget: ExecutionBudget | None = None,
    deterministic_planner: bool = False,
    cancellation_requested: Callable[[], bool] | None = None,
    structured_output: StructuredOutputPort | None = None,
) -> AgentController:
    """Compose the bounded planner/controller around the existing workflow services."""
    model = structured_output or CodexCliAdapter()
    run_store, run_artifacts, workflow, step_executor = _workflow_components(
        root, run_id, settings, model
    )
    registry = WorkflowToolRegistry(
        workflow.handlers(),
        extra_tools=(
            AdaptiveSearchQueryTool(run_artifacts, literature_search_service()),
            AdaptiveSearchFinalizeTool(run_artifacts),
            AdaptiveSearchCoverageTool(run_artifacts),
            AdaptiveScreeningExecuteTool(run_artifacts, screening_service(model)),
            AdaptiveScreeningFinalizeTool(run_artifacts, HumanGate(RunStateMachine(), run_store)),
            AdaptiveFullTextPrepareTool(run_artifacts),
            AdaptiveFullTextProcessNextTool(
                run_artifacts,
                full_text_service(),
                OpenPdfDownloaderAdapter(),
                (root / "runs" / run_id / "artifacts").resolve(),
                HumanGate(RunStateMachine(), run_store),
                unpaywall_email=(
                    settings.unpaywall_email
                    if settings is not None
                    else os.environ.get("UNPAYWALL_EMAIL", "")
                ),
            ),
            AdaptiveFullTextFinalizeTool(run_artifacts),
            AdaptiveEvidencePrepareTool(
                run_artifacts,
                (root / "runs" / run_id / "artifacts").resolve(),
            ),
            AdaptiveEvidenceProcessNextTool(
                run_artifacts,
                (root / "runs" / run_id / "artifacts").resolve(),
                document_reader(),
                evidence_extraction_service(model),
            ),
            AdaptiveEvidenceFinalizeTool(run_artifacts),
        ),
    )
    planner = DeterministicPlanner() if deterministic_planner else CodexPlanner(model)
    event_store = FileSystemAgentEventStore(root / "runs" / run_id / "events.jsonl")
    return AgentController(
        run_store=run_store,
        artifact_store=run_artifacts,
        event_store=event_store,
        state_machine=RunStateMachine(),
        planner=planner,
        registry=registry,
        budget=budget,
        cancellation_requested=cancellation_requested,
    )


def _workflow_components(
    root: Path,
    run_id: str,
    settings: WorkflowSettings | None,
    structured_output: StructuredOutputPort | None = None,
) -> tuple[FileSystemResearchRunStore, FileSystemArtifactStore, ResearchWorkflow, StepExecutor]:
    state_machine = RunStateMachine()
    run_store = research_run_store(root)
    artifact_root = (root / "runs" / run_id / "artifacts").resolve()
    run_artifacts = artifact_store(artifact_root)
    synthesis_artifacts = artifact_store(artifact_root / "evidence_synthesis")
    options_name = "workflow_options.json"
    current = settings or WorkflowSettings(unpaywall_email=os.environ.get("UNPAYWALL_EMAIL", ""))
    options_path = artifact_root / options_name
    if options_path.is_file():
        stored = run_artifacts.load_json(options_name)
        if not isinstance(stored, dict):
            raise ValueError("Persisted workflow options must be a JSON object.")
        current = WorkflowSettings(
            max_per_query=int(stored.get("max_per_query", current.max_per_query)),
            max_screen=int(stored.get("max_screen", current.max_screen)),
            screen_all=bool(stored.get("screen_all", current.screen_all)),
            screen_batch_size=int(stored.get("screen_batch_size", current.screen_batch_size)),
            unpaywall_email=current.unpaywall_email,
        )
    else:
        run_artifacts.save_json(
            options_name,
            {
                "max_per_query": current.max_per_query,
                "max_screen": current.max_screen,
                "screen_all": current.screen_all,
                "screen_batch_size": current.screen_batch_size,
            },
        )

    model = structured_output or CodexCliAdapter()
    workflow = ResearchWorkflow(
        artifact_store=run_artifacts,
        synthesis_store=synthesis_artifacts,
        artifact_root=artifact_root,
        literature_search=literature_search_service(),
        screening=screening_service(model),
        full_text=full_text_service(),
        pdf_downloader=OpenPdfDownloaderAdapter(),
        document_reader=document_reader(),
        evidence_extraction=evidence_extraction_service(model),
        review_draft=review_draft_service(model),
        human_gate=HumanGate(state_machine, run_store),
        settings=current,
    )
    return run_store, run_artifacts, workflow, StepExecutor(workflow.handlers())
