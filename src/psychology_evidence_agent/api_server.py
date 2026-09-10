"""Local web service for deterministic literature metadata and run actions.

The service powers the browser workspace. It does not run Codex CLI or download
paywalled files; a user-provided full text is accepted only through an explicit,
audited Human Gate action.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from .adapters.llm.openai_compatible import ModelConnection
from .adapters.persistence.artifact_store import FileSystemArtifactStore
from .adapters.persistence.cancellation_store import FileSystemCancellationStore
from .adapters.persistence.event_store import FileSystemAgentEventStore
from .bootstrap import (
    agent_controller,
    full_text_service,
    human_gate,
    literature_search_service,
    research_run_lock,
    research_run_store,
    structured_output_adapter,
)
from .domain.agent import (
    AgentEvent,
    AgentEventType,
    AgentGoal,
    ExecutionBudget,
    ResearchBrief,
    SearchPreferences,
)
from .domain.enums import ArtifactType, HumanActionStatus, HumanDecisionType, RunStage, RunStatus
from .domain.errors import (
    ExternalServiceError,
    LiteratureSearchError,
    RunBusyError,
    RunNotFoundError,
    RunPersistenceError,
)
from .domain.paper import Paper
from .domain.run import HumanDecision, ResearchRun, RunFailure, create_research_run
from .resources import web_directory
from .runtime.agent_controller import (
    BRIEF_ARTIFACT,
    BUDGET_ARTIFACT,
    GOAL_ARTIFACT,
    PLAN_ARTIFACT,
)
from .runtime.state_machine import RunStateMachine
from .services.research_intake import (
    IntakeMessage,
    clarify_research_question,
)

LOGGER = logging.getLogger("psychology_evidence_api")
MAX_RESULTS = 25
MAX_PAPERS_PER_ACCESS_LOOKUP = 20
MAX_AGENT_RUNS = 50
MAX_HUMAN_FULLTEXT_BYTES = 50 * 1024 * 1024
ALLOWED_HUMAN_FULLTEXT_SUFFIXES = frozenset({".pdf", ".md", ".txt"})
_ACTION_ID_PATTERN = re.compile(r"^action_[0-9a-f]{32}$")
WEB_DIRECTORY = web_directory()
AGENT_OPTIONS_ARTIFACT = "agent/options.json"
_AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pea-agent")
_AGENT_JOBS: dict[str, Future[None]] = {}
_AGENT_JOBS_LOCK = threading.Lock()
_RUN_MODEL_CONNECTIONS: dict[str, ModelConnection] = {}
_RUN_MODEL_CONNECTIONS_LOCK = threading.Lock()


class LiteratureSearchRequest(BaseModel):
    """The local browser workspace search contract."""

    query: str = Field(
        min_length=3, max_length=500, description="Research question or literature query"
    )
    year_from: int = Field(default=2000, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    max_results: int = Field(default=10, ge=1, le=MAX_RESULTS)
    query_id: str = Field(default="web_search", min_length=1, max_length=80)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned

    @model_validator(mode="after")
    def validate_search_year_range(self) -> LiteratureSearchRequest:
        if self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from must not be later than year_to")
        return self


class PaperForAccessLookup(BaseModel):
    """Metadata needed to find lawful public full-text locations."""

    paper_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=1000)
    year: int | str | None = None
    doi: str = Field(default="", max_length=300)
    is_open_access: bool = False
    open_access_pdf_url: str = Field(default="", max_length=2000)
    open_access_url: str = Field(default="", max_length=2000)
    open_access_license: str = Field(default="", max_length=200)


class OpenAccessLookupRequest(BaseModel):
    papers: list[PaperForAccessLookup] = Field(
        min_length=1, max_length=MAX_PAPERS_PER_ACCESS_LOOKUP
    )
    unpaywall_email: str = Field(
        default="",
        max_length=254,
        description="Optional email required by Unpaywall. Used only for this request and never written to disk.",
    )


class ModelConnectionRequest(BaseModel):
    """User-selected model connection; the secret is never persisted or returned."""

    provider: Literal["codex_cli", "openai", "deepseek", "qwen", "custom"] = "codex_cli"
    model: str = Field(default="", max_length=200)
    api_base: str = Field(default="", max_length=1000)
    api_key: SecretStr | None = Field(default=None, repr=False)
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    json_mode: bool = True

    @model_validator(mode="after")
    def validate_external_connection(self) -> ModelConnectionRequest:
        if self.provider == "codex_cli":
            return self
        if not self.model.strip():
            raise ValueError("model is required for an external provider")
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError("api_key is required for an external provider")
        parsed = urlsplit(self.api_base.strip())
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("api_base must use HTTPS; HTTP is allowed only for loopback")
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("api_base must be a clean service base URL")
        return self

    def runtime_connection(self) -> ModelConnection | None:
        if self.provider == "codex_cli":
            return None
        assert self.api_key is not None
        return ModelConnection(
            provider=self.provider,
            model=self.model.strip(),
            api_base=self.api_base.strip().rstrip("/"),
            api_key=self.api_key.get_secret_value().strip(),
            timeout_seconds=float(self.timeout_seconds),
            json_mode=self.json_mode,
        )

    def safe_descriptor(self) -> dict[str, str]:
        if self.provider == "codex_cli":
            return {"provider": "codex_cli", "model": "Codex CLI"}
        connection = self.runtime_connection()
        assert connection is not None
        return connection.safe_descriptor()


class AgentRunRequest(BaseModel):
    """Validated input for one browser-created bounded Agent run."""

    objective: str = Field(min_length=3, max_length=1000)
    research_question: str | None = Field(default=None, max_length=4000)
    population: str = Field(default="", max_length=500)
    intervention_or_exposure: str = Field(default="", max_length=500)
    comparison: str = Field(default="", max_length=500)
    outcomes: list[str] = Field(default_factory=list, max_length=20)
    inclusion_criteria: list[str] = Field(default_factory=list, max_length=20)
    exclusion_criteria: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=10)
    study_types: list[str] = Field(default_factory=list, max_length=20)
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    config_reference: str | None = Field(default=None, max_length=500)
    max_per_query: int = Field(default=15, ge=1, le=100)
    max_screen: int = Field(default=30, ge=1, le=200)
    screen_all: bool = False
    screen_batch_size: int = Field(default=10, ge=1, le=100)
    max_steps: int = Field(default=40, ge=1, le=200)
    max_replans: int = Field(default=10, ge=1, le=50)
    max_model_calls: int = Field(default=20, ge=1, le=100)
    max_attempts_per_step: int = Field(default=2, ge=1, le=10)
    deterministic_planner: bool = False
    search_mode: Literal["automatic", "manual"] = "automatic"
    manual_query: str = Field(default="", max_length=500)
    candidate_limit: int = Field(default=30, ge=1, le=100)
    model_connection: ModelConnectionRequest = Field(default_factory=ModelConnectionRequest)

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("objective must not be blank")
        return cleaned

    @field_validator("research_question")
    @classmethod
    def blank_research_question_uses_objective(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_research_year_range(self) -> AgentRunRequest:
        if self.year_from is not None and self.year_to is not None:
            if self.year_from > self.year_to:
                raise ValueError("year_from must not be later than year_to")
        if self.search_mode == "manual" and not self.manual_query.strip():
            raise ValueError("manual_query is required in manual search mode")
        return self


class HumanActionResolutionRequest(BaseModel):
    """Safe browser-side decisions for a pending Human Gate."""

    decision: HumanDecisionType
    note: str = Field(default="", max_length=2000)


class ResearchIntakeRequest(BaseModel):
    """One browser turn used to clarify a research question before starting a run."""

    message: str = Field(min_length=1, max_length=4000)
    history: list[IntakeMessage] = Field(default_factory=list, max_length=12)
    current_brief: ResearchBrief | None = None
    model_connection: ModelConnectionRequest = Field(default_factory=ModelConnectionRequest)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message must not be blank")
        return cleaned


class ModelContinuationRequest(BaseModel):
    """Optionally reattach an external model secret after a local service restart."""

    model_connection: ModelConnectionRequest | None = None


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Require a shared key when the local tunnel sets ``PEA_API_KEY``.

    Leave the variable unset only for local browser development. A service
    exposed through a temporary public tunnel must use a non-empty key.
    """
    expected = os.getenv("PEA_API_KEY", "")
    if expected and (x_api_key is None or not secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


app = FastAPI(
    title="Psychology Evidence Agent API",
    version="0.1.0",
    description="A local, human-in-the-loop adapter for literature metadata and lawful OA discovery.",
)
app.mount("/assets", StaticFiles(directory=WEB_DIRECTORY), name="web-assets")


def _run_store_root() -> Path:
    """Resolve the one server-configured run root; it is never supplied by the browser."""
    return Path(os.getenv("PEA_STORE_ROOT", "data"))


def _artifact_store_for(root: Path, run_id: str) -> FileSystemArtifactStore:
    return FileSystemArtifactStore(root / "runs" / run_id / "artifacts")


def _cancellation_store(root: Path) -> FileSystemCancellationStore:
    return FileSystemCancellationStore(root)


def _load_artifact(artifact_store: FileSystemArtifactStore, name: str) -> Any | None:
    try:
        return artifact_store.load_json(name)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _job_running(run_id: str) -> bool:
    with _AGENT_JOBS_LOCK:
        job = _AGENT_JOBS.get(run_id)
        return job is not None and not job.done()


def _clear_agent_job(run_id: str, _future: Future[None]) -> None:
    with _AGENT_JOBS_LOCK:
        current = _AGENT_JOBS.get(run_id)
        if current is _future:
            _AGENT_JOBS.pop(run_id, None)


def _remember_model_connection(run_id: str, connection: ModelConnection | None) -> None:
    with _RUN_MODEL_CONNECTIONS_LOCK:
        if connection is None:
            _RUN_MODEL_CONNECTIONS.pop(run_id, None)
        else:
            _RUN_MODEL_CONNECTIONS[run_id] = connection


def _model_connection_for_run(root: Path, run_id: str) -> ModelConnection | None:
    with _RUN_MODEL_CONNECTIONS_LOCK:
        connection = _RUN_MODEL_CONNECTIONS.get(run_id)
    if connection is not None:
        return connection
    descriptor = _load_artifact(_artifact_store_for(root, run_id), AGENT_OPTIONS_ARTIFACT)
    provider = (
        descriptor.get("model_connection", {}).get("provider")
        if isinstance(descriptor, dict)
        else None
    )
    if provider not in {None, "codex_cli"}:
        raise RuntimeError(
            "External model credentials are no longer available; re-enter them before continuing."
        )
    return None


def _submit_agent_job(
    run_id: str,
    function: Any,
) -> None:
    with _AGENT_JOBS_LOCK:
        current = _AGENT_JOBS.get(run_id)
        if current is not None and not current.done():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This research run is already being processed.",
            )
        future = _AGENT_EXECUTOR.submit(function)
        _AGENT_JOBS[run_id] = future
        future.add_done_callback(partial(_clear_agent_job, run_id))


def _persist_background_failure(root: Path, run_id: str) -> None:
    """Turn an unexpected worker exception into a safe, inspectable run failure."""
    try:
        with research_run_lock(root, run_id):
            store = research_run_store(root)
            run = store.load(run_id)
            if run.status is RunStatus.RUNNING:
                run.record_failure(
                    RunFailure(
                        error_code="agent_web_job_failed",
                        message="The background Agent job stopped unexpectedly.",
                        stage=run.stage,
                        retryable=False,
                    )
                )
                RunStateMachine().transition_status(run, RunStatus.FAILED)
                store.save(run)
    except (OSError, RunBusyError, RunNotFoundError, RunPersistenceError, ValueError):
        LOGGER.warning("Could not persist background failure for run %s", run_id)


def _execute_agent_job(
    root: Path,
    run_id: str,
    goal: AgentGoal,
    deterministic_planner: bool,
) -> None:
    cancellations = _cancellation_store(root)
    try:
        with research_run_lock(root, run_id):
            store = research_run_store(root)
            run = store.load(run_id)
            controller = agent_controller(
                root,
                run_id,
                deterministic_planner=deterministic_planner,
                cancellation_requested=lambda: cancellations.is_requested(run_id),
                structured_output=structured_output_adapter(
                    _model_connection_for_run(root, run_id)
                ),
            )
            controller.run_until_blocked(run, goal)
    except Exception:
        LOGGER.exception("Agent web job failed for run %s", run_id)
        _persist_background_failure(root, run_id)
    finally:
        try:
            cancellations.clear(run_id)
        except (OSError, RunPersistenceError):
            LOGGER.warning("Could not clear cancellation request for run %s", run_id)
        try:
            finished = research_run_store(root).load(run_id)
            if finished.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                _remember_model_connection(run_id, None)
        except (RunNotFoundError, RunPersistenceError):
            _remember_model_connection(run_id, None)


def _agent_snapshot(root: Path, run: ResearchRun) -> dict[str, Any]:
    artifacts = _artifact_store_for(root, run.run_id)
    events = FileSystemAgentEventStore(root / "runs" / run.run_id / "events.jsonl").read()
    job_running = _job_running(run.run_id)
    run_payload = _run_payload_with_paper_context(artifacts, run)
    plan = _load_artifact(artifacts, PLAN_ARTIFACT)
    agent_options = _load_artifact(artifacts, AGENT_OPTIONS_ARTIFACT)
    return {
        "run": run_payload,
        "goal": _load_artifact(artifacts, GOAL_ARTIFACT),
        "research_brief": _load_artifact(artifacts, BRIEF_ARTIFACT),
        "plan": plan,
        "budget": _load_artifact(artifacts, BUDGET_ARTIFACT),
        "model_connection": (
            agent_options.get("model_connection") if isinstance(agent_options, dict) else None
        ),
        "events": [event.model_dump(mode="json") for event in events[-20:]],
        "job_running": job_running,
        "cancel_requested": _cancellation_store(root).is_requested(run.run_id),
        "recovery_available": run.status is RunStatus.RUNNING and not job_running,
        "progress": _progress_snapshot(artifacts, run, plan),
    }


def _run_payload_with_paper_context(
    artifacts: FileSystemArtifactStore, run: ResearchRun
) -> dict[str, Any]:
    payload = run.model_dump(mode="json")
    paper_index = _paper_index(artifacts)
    screening_index = {
        item.get("paper_id"): item
        for item in _screening_rows(artifacts)
        if isinstance(item.get("paper_id"), str)
    }
    for action in payload.get("human_actions", []):
        contexts: list[dict[str, Any]] = []
        for paper_id in action.get("related_paper_ids", []):
            context = dict(paper_index.get(paper_id, {"paper_id": paper_id}))
            context.update(screening_index.get(paper_id, {}))
            context["paper_id"] = paper_id
            contexts.append(context)
        action["paper_context"] = contexts
    return payload


def _paper_index(artifacts: FileSystemArtifactStore) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for artifact_name in ("search_results.json", "screening_results.json"):
        payload = _load_artifact(artifacts, artifact_name)
        if not isinstance(payload, dict):
            continue
        for field in (
            "papers",
            "screened_papers",
            "priority_papers",
            "shortlist_papers",
            "unscreened_papers",
        ):
            records = payload.get(field)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get("paper_id"), str):
                    continue
                paper_id = record["paper_id"]
                current = index.setdefault(paper_id, {})
                current.update(
                    {key: value for key, value in record.items() if value not in (None, "", [], {})}
                )
    return index


def _screening_rows(artifacts: FileSystemArtifactStore) -> list[dict[str, Any]]:
    payload = _load_artifact(artifacts, "screening_results.json")
    if not isinstance(payload, dict):
        return []
    paper_index = _paper_index_from_search_artifact(artifacts)
    records = payload.get("screened_papers")
    if not isinstance(records, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("paper_id"), str):
            continue
        row = dict(paper_index.get(record["paper_id"], {}))
        row.update(record)
        rows.append(row)
    return rows


def _paper_index_from_search_artifact(
    artifacts: FileSystemArtifactStore,
) -> dict[str, dict[str, Any]]:
    payload = _load_artifact(artifacts, "search_results.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        return {}
    return {
        record["paper_id"]: record
        for record in payload["papers"]
        if isinstance(record, dict) and isinstance(record.get("paper_id"), str)
    }


def _progress_snapshot(
    artifacts: FileSystemArtifactStore,
    run: ResearchRun,
    plan: Any,
) -> dict[str, Any]:
    stages = [stage.value for stage in RunStage]
    stage_index = stages.index(run.stage.value) if run.stage.value in stages else 0
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    completed_steps = sum(
        isinstance(step, dict) and step.get("status") == "completed" for step in steps
    )
    current_step = next(
        (
            step.get("tool_name")
            for step in steps
            if isinstance(step, dict) and step.get("status") in {"running", "waiting"}
        ),
        None,
    )
    unit_completed: int | None = None
    unit_total: int | None = None
    if run.stage is RunStage.SCREENING:
        rows = _screening_rows(artifacts)
        unit_completed = len(rows)
        manifest = _load_artifact(artifacts, "agent/screening_batches.json")
        if isinstance(manifest, dict) and isinstance(manifest.get("shortlist_paper_ids"), list):
            unit_total = len(manifest["shortlist_paper_ids"])
    elif run.stage is RunStage.RETRIEVING_FULLTEXT:
        unit_completed, unit_total = _queue_progress(artifacts, "agent/fulltext_queue.json")
    elif run.stage is RunStage.EXTRACTING_EVIDENCE:
        unit_completed, unit_total = _queue_progress(artifacts, "agent/evidence_queue.json")

    return {
        "stage_index": stage_index,
        "stage_count": len(stages),
        "completed_stage_count": stage_index
        if run.status not in {RunStatus.COMPLETED}
        else len(stages),
        "current_stage": run.stage.value,
        "current_step": current_step,
        "completed_steps": completed_steps,
        "total_steps": len(steps),
        "unit_completed": unit_completed,
        "unit_total": unit_total,
        "indeterminate": unit_completed is None or unit_total in (None, 0),
    }


def _queue_progress(
    artifacts: FileSystemArtifactStore, queue_name: str
) -> tuple[int | None, int | None]:
    payload = _load_artifact(artifacts, queue_name)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None, None
    items = [item for item in payload["items"] if isinstance(item, dict)]
    completed = sum(
        item.get("status") in {"completed", "downloaded", "provided", "skipped"} for item in items
    )
    return completed, len(items)


def _history_summary(root: Path, run: ResearchRun) -> dict[str, Any]:
    artifacts = _artifact_store_for(root, run.run_id)
    payload = run.model_dump(mode="json")
    search = _load_artifact(artifacts, "search_results.json")
    screened = _screening_rows(artifacts)
    payload.update(
        {
            "candidate_count": len(search.get("papers", [])) if isinstance(search, dict) else 0,
            "screened_count": len(screened),
            "artifact_count": len(run.artifact_references),
            "pending_human_count": sum(
                action.status is HumanActionStatus.PENDING for action in run.human_actions
            ),
        }
    )
    return payload


def _clear_cancellation_request(root: Path, run_id: str) -> None:
    _cancellation_store(root).clear(run_id)


def _append_cancelled_event(root: Path, run: ResearchRun) -> None:
    event_store = FileSystemAgentEventStore(root / "runs" / run.run_id / "events.jsonl")
    sequence = max((event.sequence for event in event_store.read()), default=0) + 1
    event_store.append(
        AgentEvent(
            run_id=run.run_id,
            sequence=sequence,
            event_type=AgentEventType.RUN_CANCELLED,
            summary="The Agent run was cancelled before a worker resumed it.",
            metadata={"source": "web_api"},
        )
    )


def _load_agent_goal(root: Path, run: ResearchRun) -> AgentGoal:
    payload = _load_artifact(_artifact_store_for(root, run.run_id), GOAL_ARTIFACT)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Agent goal artifact is missing or invalid.",
        )
    try:
        return AgentGoal.model_validate(payload)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Agent goal artifact is missing or invalid.",
        ) from error


def _load_deterministic_option(root: Path, run: ResearchRun) -> bool:
    payload = _load_artifact(_artifact_store_for(root, run.run_id), AGENT_OPTIONS_ARTIFACT)
    return isinstance(payload, dict) and bool(payload.get("deterministic_planner"))


def _prepare_model_continuation(
    root: Path,
    run_id: str,
    request: ModelContinuationRequest | None,
) -> None:
    if request is not None and request.model_connection is not None:
        _remember_model_connection(run_id, request.model_connection.runtime_connection())
    try:
        _model_connection_for_run(root, run_id)
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


async def _read_human_fulltext(upload: UploadFile) -> tuple[str, bytes]:
    """Read and validate a browser-provided full text within a hard size limit."""
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_HUMAN_FULLTEXT_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full text must be an existing PDF, Markdown, or TXT upload.",
        )

    content = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_HUMAN_FULLTEXT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Full-text upload exceeds the 50 MB safety limit.",
            )

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full-text upload must not be empty.",
        )
    if suffix == ".pdf" and not bytes(content[:1024]).lstrip().startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded PDF does not have a valid PDF header.",
        )
    return suffix, bytes(content)


def _pending_fulltext_action(run: ResearchRun, action_id: str) -> None:
    """Reject uploads that cannot be attached to a pending full-text Human Gate."""
    if not _ACTION_ID_PATTERN.fullmatch(action_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid action ID.")
    action = next((item for item in run.human_actions if item.action_id == action_id), None)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Human action not found.")
    if action.status is not HumanActionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Human action is already resolved."
        )
    if "provide_fulltext" not in {item.value for item in action.allowed_decisions}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This human action does not accept a provided full text.",
        )


@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    """Serve the local, browser-based research workspace."""
    return FileResponse(WEB_DIRECTORY / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "psychology-evidence-agent"}


@app.get("/v1/system/capabilities")
def system_capabilities() -> dict[str, Any]:
    """Expose non-secret connection capabilities for first-use UI guidance."""
    return {
        "service": "psychology-evidence-agent",
        "local_mode": True,
        "api_key_required": bool(os.getenv("PEA_API_KEY", "")),
        "intake_assistant": True,
        "model_providers": [
            {"id": "codex_cli", "label": "本机 Codex CLI", "external": False},
            {
                "id": "openai",
                "label": "OpenAI API",
                "external": True,
                "api_base": "https://api.openai.com/v1",
            },
            {
                "id": "deepseek",
                "label": "DeepSeek API",
                "external": True,
                "api_base": "https://api.deepseek.com",
            },
            {
                "id": "qwen",
                "label": "阿里云百炼 / Qwen",
                "external": True,
                "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            {"id": "custom", "label": "其他 OpenAI-compatible", "external": True},
        ],
    }


@app.post(
    "/v1/research-intake/messages",
    dependencies=[Depends(require_api_key)],
)
def research_intake_message(request: ResearchIntakeRequest) -> dict[str, Any]:
    """Clarify a research idea without starting search or granting tool access."""
    try:
        result = clarify_research_question(
            user_message=request.message,
            history=request.history,
            current_brief=request.current_brief,
            structured_output=structured_output_adapter(
                request.model_connection.runtime_connection()
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except Exception as error:
        LOGGER.warning("Research intake assistant failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="研究问题助手暂时不可用，请改用手动编辑方式。",
        ) from error
    return result.model_dump(mode="json")


@app.get("/v1/agent/runs", dependencies=[Depends(require_api_key)])
def list_agent_runs() -> dict[str, Any]:
    """List recent lightweight run summaries without exposing filesystem paths."""
    root = _run_store_root()
    runs_root = root / "runs"
    summaries: list[dict[str, Any]] = []
    if runs_root.is_dir():
        for candidate in sorted(
            (item for item in runs_root.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:MAX_AGENT_RUNS]:
            try:
                run = research_run_store(root).load(candidate.name)
            except (OSError, RunNotFoundError, RunPersistenceError, ValueError):
                continue
            summaries.append(_history_summary(root, run))
    return {"runs": summaries}


@app.post(
    "/v1/agent/runs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)]
)
def create_agent_run(request: AgentRunRequest) -> dict[str, Any]:
    """Create and asynchronously execute one bounded Agent run."""
    root = _run_store_root()
    store = research_run_store(root)
    research_question = request.research_question or request.objective
    brief = ResearchBrief(
        research_question=research_question,
        population=request.population,
        intervention_or_exposure=request.intervention_or_exposure,
        comparison=request.comparison,
        outcomes=request.outcomes,
        inclusion_criteria=request.inclusion_criteria,
        exclusion_criteria=request.exclusion_criteria,
        languages=request.languages,
        study_types=request.study_types,
        year_from=request.year_from,
        year_to=request.year_to,
    )
    run = create_research_run(research_question, config_reference=request.config_reference)
    store.create(run)
    goal = AgentGoal(
        objective=request.objective,
        research_question=research_question,
        brief=brief,
        search_preferences=SearchPreferences(
            mode=request.search_mode,
            manual_query=request.manual_query,
            candidate_limit=request.candidate_limit,
        ),
    )
    artifacts = _artifact_store_for(root, run.run_id)
    artifacts.save_json(GOAL_ARTIFACT, goal.model_dump(mode="json"))
    artifacts.save_json(BRIEF_ARTIFACT, brief.model_dump(mode="json"))
    artifacts.save_json(
        AGENT_OPTIONS_ARTIFACT,
        {
            "deterministic_planner": request.deterministic_planner,
            "model_connection": request.model_connection.safe_descriptor(),
        },
    )
    artifacts.save_json(
        "workflow_options.json",
        {
            "max_per_query": request.max_per_query,
            "max_screen": min(request.max_screen, request.candidate_limit),
            "screen_all": request.screen_all,
            "screen_batch_size": request.screen_batch_size,
            "candidate_limit": request.candidate_limit,
        },
    )
    budget = ExecutionBudget(
        max_steps=request.max_steps,
        max_replans=request.max_replans,
        max_model_calls=request.max_model_calls,
        max_attempts_per_step=request.max_attempts_per_step,
    )
    artifacts.save_json(BUDGET_ARTIFACT, budget.model_dump(mode="json"))
    try:
        _remember_model_connection(run.run_id, request.model_connection.runtime_connection())
        _clear_cancellation_request(root, run.run_id)
        _submit_agent_job(
            run.run_id,
            partial(_execute_agent_job, root, run.run_id, goal, request.deterministic_planner),
        )
    except HTTPException:
        raise
    return _agent_snapshot(root, run)


@app.get("/v1/agent/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_agent_run(run_id: str) -> dict[str, Any]:
    try:
        run = research_run_store(_run_store_root()).load(run_id)
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    return _agent_snapshot(_run_store_root(), run)


@app.get("/v1/agent/runs/{run_id}/search-results", dependencies=[Depends(require_api_key)])
def get_search_results(run_id: str) -> dict[str, Any]:
    """Return the current run's finalized search artifact for the results workspace."""
    root = _run_store_root()
    try:
        run = research_run_store(root).load(run_id)
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    artifacts = _artifact_store_for(root, run.run_id)
    payload = _load_artifact(artifacts, "search_results.json")
    goal = _load_artifact(artifacts, GOAL_ARTIFACT)
    brief = _load_artifact(artifacts, BRIEF_ARTIFACT)
    search_preferences: dict[str, Any] = {}
    if isinstance(goal, dict):
        raw_preferences = goal["search_preferences"] if "search_preferences" in goal else {}
        if isinstance(raw_preferences, dict):
            search_preferences = raw_preferences
    if not isinstance(payload, dict):
        return {
            "run_id": run.run_id,
            "status": "pending",
            "research_question": run.research_question,
            "research_brief": brief,
            "search_preferences": search_preferences,
            "queries": [],
            "report": {},
            "papers": [],
        }
    raw_config = payload.get("config")
    raw_report = payload.get("report")
    raw_papers = payload.get("papers")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    report: dict[str, Any] = raw_report if isinstance(raw_report, dict) else {}
    papers: list[Any] = raw_papers if isinstance(raw_papers, list) else []
    queries = config.get("queries") if isinstance(config.get("queries"), list) else []
    return {
        "run_id": run.run_id,
        "status": "ready",
        "research_question": run.research_question,
        "research_brief": brief,
        "search_preferences": search_preferences,
        "queries": queries,
        "report": report,
        "papers": papers,
    }


@app.get("/v1/agent/runs/{run_id}/screening-results", dependencies=[Depends(require_api_key)])
def get_screening_results(run_id: str) -> dict[str, Any]:
    """Return title-and-abstract screening rows with metadata for the review UI."""
    root = _run_store_root()
    try:
        run = research_run_store(root).load(run_id)
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    artifacts = _artifact_store_for(root, run.run_id)
    rows = _screening_rows(artifacts)
    action_by_paper: dict[str, dict[str, Any]] = {}
    for action in run.human_actions:
        for paper_id in action.related_paper_ids:
            action_by_paper[paper_id] = {
                "action_id": action.action_id,
                "status": action.status.value,
                "decision": action.decision.decision_type.value if action.decision else None,
            }
    for row in rows:
        row_paper_id = row.get("paper_id")
        review_action = action_by_paper.get(row_paper_id) if isinstance(row_paper_id, str) else None
        row["human_review"] = review_action
        if review_action and review_action["status"] == HumanActionStatus.PENDING.value:
            row["screening_status"] = "待人工审核"
        elif review_action and review_action["decision"]:
            row["screening_status"] = f"人工{review_action['decision']}"
        else:
            row["screening_status"] = "机器筛选完成"
    return {
        "run_id": run.run_id,
        "research_question": run.research_question,
        "results": rows,
        "result_count": len(rows),
    }


@app.post(
    "/v1/agent/runs/{run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def resume_agent_run(
    run_id: str, request: ModelContinuationRequest | None = None
) -> dict[str, Any]:
    root = _run_store_root()
    try:
        with research_run_lock(root, run_id):
            run = research_run_store(root).load(run_id)
            goal = _load_agent_goal(root, run)
            deterministic = _load_deterministic_option(root, run)
    except RunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Research run is busy."
        ) from error
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    _prepare_model_continuation(root, run_id, request)
    _clear_cancellation_request(root, run_id)
    _submit_agent_job(run_id, partial(_execute_agent_job, root, run_id, goal, deterministic))
    return _agent_snapshot(root, run)


@app.post(
    "/v1/agent/runs/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def retry_agent_run(run_id: str, request: ModelContinuationRequest | None = None) -> dict[str, Any]:
    root = _run_store_root()
    try:
        with research_run_lock(root, run_id):
            store = research_run_store(root)
            run = store.load(run_id)
            _prepare_model_continuation(root, run_id, request)
            RunStateMachine().retry(run)
            store.save(run)
            goal = _load_agent_goal(root, run)
            deterministic = _load_deterministic_option(root, run)
    except RunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Research run is busy."
        ) from error
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _clear_cancellation_request(root, run_id)
    _submit_agent_job(run_id, partial(_execute_agent_job, root, run_id, goal, deterministic))
    return _agent_snapshot(root, run)


@app.post("/v1/agent/runs/{run_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_agent_run(run_id: str) -> dict[str, Any]:
    root = _run_store_root()
    try:
        store = research_run_store(root)
        run = store.load(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return _agent_snapshot(root, run)
        if _job_running(run_id):
            _cancellation_store(root).request(run_id)
            return _agent_snapshot(root, run)
        with research_run_lock(root, run_id):
            run = store.load(run_id)
            if run.status not in {
                RunStatus.CREATED,
                RunStatus.RUNNING,
                RunStatus.WAITING_FOR_HUMAN,
            }:
                return _agent_snapshot(root, run)
            RunStateMachine().transition_status(run, RunStatus.CANCELLED)
            store.save(run)
            _append_cancelled_event(root, run)
    except RunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Research run is busy."
        ) from error
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _remember_model_connection(run_id, None)
    return _agent_snapshot(root, run)


@app.post(
    "/v1/agent/runs/{run_id}/human-actions/{action_id}/resolve",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def resolve_agent_action(
    run_id: str, action_id: str, request: HumanActionResolutionRequest
) -> dict[str, Any]:
    if request.decision is HumanDecisionType.PROVIDE_FULLTEXT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full-text upload remains an explicit local step; resolve it through the CLI.",
        )
    root = _run_store_root()
    try:
        with research_run_lock(root, run_id):
            store = research_run_store(root)
            run = store.load(run_id)
            human_gate(root).resolve(
                run,
                HumanDecision(
                    action_id=action_id,
                    decision_type=request.decision,
                    note=request.note,
                ),
            )
            goal = _load_agent_goal(root, run)
            deterministic = _load_deterministic_option(root, run)
    except RunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Research run is busy."
        ) from error
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _clear_cancellation_request(root, run_id)
    _submit_agent_job(run_id, partial(_execute_agent_job, root, run_id, goal, deterministic))
    return _agent_snapshot(root, run)


@app.post(
    "/v1/agent/runs/{run_id}/human-actions/{action_id}/fulltext",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def upload_human_fulltext(
    run_id: str,
    action_id: str,
    file: UploadFile = File(...),
    lawful_access_confirmed: bool = Form(...),
    note: str = Form(default="", max_length=2000),
) -> dict[str, Any]:
    """Store a user-provided lawful full text and resolve its Human Gate."""
    if not lawful_access_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirm that you have lawful access to the uploaded full text.",
        )
    suffix, content = await _read_human_fulltext(file)
    root = _run_store_root()
    try:
        with research_run_lock(root, run_id):
            store = research_run_store(root)
            run = store.load(run_id)
            _pending_fulltext_action(run, action_id)
            run_artifacts = _artifact_store_for(root, run_id)
            logical_key = f"documents/manual_{action_id}{suffix}"
            run_artifacts.save_bytes(logical_key, content)
            reference = run_artifacts.reference(
                logical_key,
                ArtifactType.FULLTEXT_DOCUMENT,
                metadata={
                    "source": "human_provided",
                    "lawful_access_confirmed": "true",
                    "original_filename": Path(file.filename or "").name,
                    "size_bytes": str(len(content)),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            human_gate(root).resolve(
                run,
                HumanDecision(
                    action_id=action_id,
                    decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                    note=note,
                    provided_artifact_reference=reference,
                ),
            )
            goal = _load_agent_goal(root, run)
            deterministic = _load_deterministic_option(root, run)
    except RunBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Research run is busy."
        ) from error
    except (RunNotFoundError, RunPersistenceError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _clear_cancellation_request(root, run_id)
    _submit_agent_job(run_id, partial(_execute_agent_job, root, run_id, goal, deterministic))
    return _agent_snapshot(root, run)


@app.post("/v1/literature/search", dependencies=[Depends(require_api_key)])
def literature_search(request: LiteratureSearchRequest) -> dict[str, Any]:
    """Retrieve and normalize OpenAlex article metadata without model screening."""
    try:
        papers = literature_search_service().search(
            query_id=request.query_id,
            query=request.query,
            year_from=request.year_from,
            per_page=request.max_results,
            year_to=request.year_to,
        )
    except LiteratureSearchError as error:
        LOGGER.warning("OpenAlex request failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAlex search failed. Please retry later; this does not mean no relevant papers exist.",
        ) from error
    return {
        "query": request.query,
        "year_from": request.year_from,
        "year_to": request.year_to,
        "paper_count": len(papers),
        "papers": [paper.model_dump(mode="json") for paper in papers],
        "limitations": [
            "This is metadata retrieval, not a systematic database search.",
            "Returned papers have not been screened or treated as direct intervention evidence.",
        ],
    }


@app.post("/v1/open-access/lookup", dependencies=[Depends(require_api_key)])
def open_access_lookup(request: OpenAccessLookupRequest) -> dict[str, Any]:
    """Locate lawful public versions; this endpoint never downloads files."""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for paper_model in request.papers:
        paper_payload = paper_model.model_dump()
        if paper_payload["year"] is None:
            paper_payload["year"] = ""
        paper = Paper.model_validate(paper_payload)
        try:
            candidate = full_text_service().discover_for_paper(
                paper=paper, unpaywall_email=request.unpaywall_email
            )
            results.append(candidate.model_dump(mode="json"))
            failures.extend(
                {
                    "paper_id": paper.paper_id,
                    "source": failure.source,
                    "error": failure.error,
                }
                for failure in candidate.source_failures
            )
        except ExternalServiceError as error:
            LOGGER.warning("Open-access lookup failed: %s", type(error).__name__)
            failures.append(
                {
                    "paper_id": paper.paper_id,
                    "source": "lookup",
                    "error": "The public-source lookup failed for this paper; try again later.",
                }
            )
    return {
        "candidate_count": len(results),
        "candidates": results,
        "failures": failures,
        "limitations": [
            "Only explicitly public full-text locations are returned.",
            "No PDF is downloaded and no paywall, login, or copyright restriction is bypassed.",
        ],
    }
