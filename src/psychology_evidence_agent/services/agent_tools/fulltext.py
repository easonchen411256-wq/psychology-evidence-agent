"""Bounded Agent tools for lawful, resumable full-text retrieval.

The tools in this module are orchestration glue.  They deliberately reuse the
existing full-text service, downloader, artifact store, and Human Gate so that
the stable workflow remains the source of the business rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

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
    OpenAccessStatus,
    RunStage,
)
from ...domain.errors import AgentPlanRejectedError, ExternalServiceError
from ...domain.fulltext import FullTextCandidate
from ...domain.paper import Paper
from ...domain.run import ArtifactReference, ResearchRun
from ...ports.fulltext import OpenPdfDownloaderPort
from ...ports.persistence import RunArtifactStore
from ...runtime.human_gate import HumanGate
from ..checkpoints import input_fingerprint
from ..fulltext import FullTextService

_QUEUE = "agent/fulltext_queue.json"
_BATCH_DIR = "agent/fulltext_batches"
_METADATA = "fulltext_metadata.json"
_DOCUMENTS = "fulltext_documents.json"
_MAX_QUEUE_ITEMS = 100
_MAX_BATCH_SIZE = 10


class AdaptiveFullTextPrepareTool:
    """Create or reuse a queue from the validated screening artifact."""

    def __init__(self, artifact_store: RunArtifactStore) -> None:
        self._artifact_store = artifact_store
        self._descriptor = ToolDescriptor(
            name="fulltext.prepare",
            description="Prepare a bounded queue from prioritized screening results.",
            output_description="A resumable full-text retrieval queue.",
            stage=RunStage.RETRIEVING_FULLTEXT,
            effect=ToolEffect.LOCAL,
            argument_specs=[
                ToolArgumentSpec(
                    name="max_papers",
                    value_type=ToolArgumentType.INTEGER,
                    description="Maximum prioritized papers to place in the queue.",
                ),
                ToolArgumentSpec(
                    name="batch_size",
                    value_type=ToolArgumentType.INTEGER,
                    description="Default number of queue items processed per bounded batch.",
                ),
            ],
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        max_papers = _optional_integer(arguments, "max_papers", default=_MAX_QUEUE_ITEMS)
        batch_size = _optional_integer(arguments, "batch_size", default=1)
        if not 1 <= max_papers <= _MAX_QUEUE_ITEMS:
            raise AgentPlanRejectedError("max_papers must be between 1 and 100.")
        if not 1 <= batch_size <= _MAX_BATCH_SIZE:
            raise AgentPlanRejectedError("batch_size must be between 1 and 10.")

        try:
            screening = self._artifact_store.load_json("screening_results.json")
            selected = _papers_from_payload(screening, "priority_papers")[:max_papers]
        except (FileNotFoundError, OSError, TypeError, ValueError) as error:
            return ToolResult(success=False, summary=f"Screening results are unavailable: {error}")
        if not selected:
            return ToolResult(
                success=False, summary="No prioritized papers are available for full text."
            )

        fingerprint = input_fingerprint(
            {
                "selected_papers": [paper.model_dump(mode="json") for paper in selected],
                "max_papers": max_papers,
                "batch_size": batch_size,
            }
        )
        existing = _load_queue(self._artifact_store)
        if (
            existing is not None
            and existing.get("input_fingerprint") == fingerprint
            and _queue_is_valid(existing)
        ):
            return ToolResult(
                success=True,
                summary="Reused the persisted full-text retrieval queue.",
                stage_complete=False,
            )

        queue = {
            "version": 1,
            "input_fingerprint": fingerprint,
            "batch_size": batch_size,
            "batch_count": 0,
            "items": [
                {
                    "paper": paper.model_dump(mode="json"),
                    "paper_id": paper.paper_id,
                    "status": "pending",
                }
                for paper in selected
            ],
        }
        self._artifact_store.save_json(_QUEUE, queue)
        return ToolResult(
            success=True,
            summary=f"Prepared {len(selected)} prioritized paper(s) for full-text retrieval.",
            stage_complete=False,
        )


class AdaptiveFullTextProcessNextTool:
    """Process only the next bounded queue batch and pause at the existing gate."""

    def __init__(
        self,
        artifact_store: RunArtifactStore,
        full_text: FullTextService,
        pdf_downloader: OpenPdfDownloaderPort,
        artifact_root: Path,
        human_gate: HumanGate,
        *,
        unpaywall_email: str = "",
    ) -> None:
        self._artifact_store = artifact_store
        self._full_text = full_text
        self._pdf_downloader = pdf_downloader
        self._artifact_root = artifact_root.resolve()
        self._human_gate = human_gate
        self._unpaywall_email = unpaywall_email
        self._descriptor = ToolDescriptor(
            name="fulltext.process_next",
            description="Retrieve the next bounded batch through lawful public-access sources.",
            output_description="Updated queue, candidate metadata, documents, or a Human Gate action.",
            stage=RunStage.RETRIEVING_FULLTEXT,
            effect=ToolEffect.NETWORK,
            argument_specs=[
                ToolArgumentSpec(
                    name="batch_size",
                    value_type=ToolArgumentType.INTEGER,
                    description="Number of pending papers to process in this call.",
                )
            ],
            may_request_human=True,
            retryable=True,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        batch_size = _optional_integer(arguments, "batch_size", default=1)
        if not 1 <= batch_size <= _MAX_BATCH_SIZE:
            raise AgentPlanRejectedError("batch_size must be between 1 and 10.")
        queue = _load_queue(self._artifact_store)
        if queue is None or not _queue_is_valid(queue):
            return ToolResult(success=False, summary="No valid full-text queue is available.")

        processed = 0
        for item in cast(list[dict[str, Any]], queue["items"]):
            if processed >= batch_size:
                break
            paper = _paper_from_item(item)
            if paper is None:
                return ToolResult(
                    success=False, summary="The full-text queue contains invalid paper metadata."
                )

            decision = _resolved_fulltext_decision(run, paper.paper_id)
            if decision is not None:
                resolved = self._apply_human_decision(item, decision)
                if resolved is not None:
                    return resolved
                processed += 1
                continue

            if item.get("status") in {"downloaded", "provided", "skipped"}:
                continue
            pending_action = _pending_fulltext_action(run, paper.paper_id)
            if pending_action is not None:
                return ToolResult(
                    success=False,
                    blocked=True,
                    human_action_id=pending_action.action_id,
                    summary="Full-text retrieval is waiting for a Human Gate decision.",
                )

            candidate = _candidate_from_item(item)
            if candidate is None:
                try:
                    candidate = self._full_text.discover_for_paper(
                        paper=paper, unpaywall_email=self._unpaywall_email
                    )
                except ExternalServiceError as error:
                    item["last_error"] = str(error)[:300]
                    self._save_queue(queue)
                    return ToolResult(
                        success=False,
                        summary=f"Full-text discovery failed for {paper.paper_id}: {error}",
                        retryable=True,
                        stage_complete=False,
                    )
                item["candidate"] = candidate.model_dump(mode="json")
                item["status"] = "candidate_ready"
                self._save_queue(queue)

            if candidate.access_status is OpenAccessStatus.OPEN_PDF_AVAILABLE:
                try:
                    document = self._download_document(paper, candidate)
                except (OSError, ValueError, ExternalServiceError) as error:
                    self._save_queue(queue)
                    self._write_metadata(queue)
                    action = self._human_gate.request_action(
                        run,
                        action_type=HumanActionType.FULLTEXT_REQUIRED,
                        reason=(
                            f"The public PDF for {paper.paper_id} was identified but could not "
                            f"be downloaded automatically: {str(error)[:240]}"
                        ),
                        related_artifact_references=[self._metadata_reference()],
                        related_paper_ids=[paper.paper_id],
                    )
                    return ToolResult(
                        success=False,
                        blocked=True,
                        human_action_id=action.action_id,
                        summary="Full-text download requires a Human Gate decision.",
                    )
                item["document"] = document
                item["status"] = "downloaded"
            else:
                item["status"] = "waiting_for_human"
                self._save_queue(queue)
                self._write_metadata(queue)
                action = self._human_gate.request_action(
                    run,
                    action_type=HumanActionType.FULLTEXT_REQUIRED,
                    reason=f"No legal full text was automatically available for {paper.paper_id}.",
                    related_artifact_references=[self._metadata_reference()],
                    related_paper_ids=[paper.paper_id],
                )
                return ToolResult(
                    success=False,
                    blocked=True,
                    human_action_id=action.action_id,
                    summary="Full-text retrieval requires a Human Gate decision.",
                )

            processed += 1
            self._save_queue(queue)

        self._save_queue(queue)
        self._write_metadata(queue)
        if processed:
            self._save_batch(queue)
        return ToolResult(
            success=True,
            summary=f"Processed {processed} full-text queue item(s).",
            artifact_references=[self._metadata_reference()],
            stage_complete=False,
        )

    def _download_document(self, paper: Paper, candidate: FullTextCandidate) -> dict[str, str]:
        path = self._pdf_downloader.download(
            candidate, self._artifact_root / "documents", overwrite=False
        )
        resolved = path.resolve()
        if self._artifact_root not in resolved.parents or not resolved.is_file():
            raise ValueError("The downloaded full text must be a file below the run artifact root.")
        return {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "logical_key": resolved.relative_to(self._artifact_root).as_posix(),
            "retrieval_source": candidate.retrieval_source,
        }

    def _apply_human_decision(self, item: dict[str, Any], decision: Any) -> ToolResult | None:
        decision_type = decision.decision_type
        if decision_type is HumanDecisionType.SKIP_PAPER:
            item["status"] = "skipped"
            return None
        if decision_type is not HumanDecisionType.PROVIDE_FULLTEXT:
            return ToolResult(success=False, summary="Unsupported full-text Human Gate decision.")
        reference = decision.provided_artifact_reference
        if reference is None or not _artifact_file_exists(
            self._artifact_root, reference.logical_key
        ):
            return ToolResult(
                success=False,
                summary="The provided full-text artifact is unavailable in the run store.",
            )
        item["document"] = {
            "paper_id": str(item["paper_id"]),
            "title": str(cast(dict[str, Any], item["paper"])["title"]),
            "logical_key": reference.logical_key,
            "retrieval_source": "human_provided",
        }
        item["status"] = "provided"
        return None

    def _save_queue(self, queue: dict[str, Any]) -> None:
        self._artifact_store.save_json(_QUEUE, queue)

    def _save_batch(self, queue: dict[str, Any]) -> None:
        try:
            batch_number = int(queue.get("batch_count", 0)) + 1
        except (TypeError, ValueError):
            batch_number = 1
        queue["batch_count"] = batch_number
        self._save_queue(queue)
        self._artifact_store.save_json(
            f"{_BATCH_DIR}/batch_{batch_number}.json",
            {
                "batch_number": batch_number,
                "queue_fingerprint": queue.get("input_fingerprint", ""),
                "items": [
                    item
                    for item in cast(list[dict[str, Any]], queue["items"])
                    if item.get("status") in {"downloaded", "provided", "skipped"}
                ],
            },
        )

    def _metadata_reference(self) -> ArtifactReference:
        return self._artifact_store.reference(_METADATA, ArtifactType.FULLTEXT_METADATA)

    def _write_metadata(self, queue: dict[str, Any]) -> None:
        items = cast(list[dict[str, Any]], queue["items"])
        self._artifact_store.save_json(
            _METADATA,
            {
                "candidates": [item["candidate"] for item in items if item.get("candidate")],
                "failures": [
                    {"paper_id": item["paper_id"], "error": item["last_error"]}
                    for item in items
                    if item.get("last_error")
                ],
                "queue_fingerprint": queue.get("input_fingerprint", ""),
            },
        )


class AdaptiveFullTextFinalizeTool:
    """Publish the stable full-text artifacts once the bounded queue is terminal."""

    def __init__(self, artifact_store: RunArtifactStore) -> None:
        self._artifact_store = artifact_store
        self._descriptor = ToolDescriptor(
            name="fulltext.finalize",
            description="Finalize downloaded and human-provided full-text documents.",
            output_description="Stable fulltext metadata and document manifest.",
            stage=RunStage.RETRIEVING_FULLTEXT,
            effect=ToolEffect.LOCAL,
            may_request_human=True,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError("fulltext.finalize does not accept arguments.")
        queue = _load_queue(self._artifact_store)
        if queue is None or not _queue_is_valid(queue):
            return ToolResult(success=False, summary="No valid full-text queue is available.")
        items = cast(list[dict[str, Any]], queue["items"])
        pending = [
            item
            for item in items
            if item.get("status") not in {"downloaded", "provided", "skipped"}
        ]
        if pending:
            return ToolResult(
                success=True,
                summary=f"{len(pending)} full-text queue item(s) remain; continue with the next batch.",
                stage_complete=False,
                replan_required=True,
            )

        documents = [item["document"] for item in items if item.get("document")]
        skipped = [str(item["paper_id"]) for item in items if item.get("status") == "skipped"]
        if not documents:
            return ToolResult(
                success=False,
                summary="No full-text documents remain after retrieval decisions.",
            )
        metadata_reference = self._artifact_store.reference(
            _METADATA, ArtifactType.FULLTEXT_METADATA
        )
        document_reference = self._artifact_store.reference(
            _DOCUMENTS, ArtifactType.FULLTEXT_DOCUMENT
        )
        self._artifact_store.save_json(
            _DOCUMENTS,
            {"documents": documents, "skipped_paper_ids": sorted(skipped)},
        )
        return ToolResult(
            success=True,
            summary=f"Finalized {len(documents)} full-text document(s).",
            artifact_references=[metadata_reference, document_reference],
        )


def _load_queue(artifact_store: RunArtifactStore) -> dict[str, Any] | None:
    try:
        payload = artifact_store.load_json(_QUEUE)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _queue_is_valid(queue: dict[str, Any]) -> bool:
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("paper_id"), str)
        and isinstance(item.get("paper"), dict)
        and item.get("status")
        in {
            "pending",
            "candidate_ready",
            "waiting_for_human",
            "downloaded",
            "provided",
            "skipped",
        }
        for item in items
    )


def _papers_from_payload(payload: Any, field: str) -> list[Paper]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise ValueError(f"Workflow artifact field '{field}' must be a JSON array.")
    return [Paper.model_validate(item) for item in cast(list[Any], payload[field])]


def _paper_from_item(item: dict[str, Any]) -> Paper | None:
    try:
        return Paper.model_validate(item.get("paper"))
    except (TypeError, ValueError):
        return None


def _candidate_from_item(item: dict[str, Any]) -> FullTextCandidate | None:
    raw = item.get("candidate")
    if not isinstance(raw, dict):
        return None
    try:
        return FullTextCandidate.model_validate(raw)
    except ValueError:
        return None


def _resolved_fulltext_decision(run: ResearchRun, paper_id: str) -> Any | None:
    for action in run.human_actions:
        if (
            action.action_type is HumanActionType.FULLTEXT_REQUIRED
            and action.status is HumanActionStatus.RESOLVED
            and paper_id in action.related_paper_ids
            and action.decision is not None
        ):
            return action.decision
    return None


def _pending_fulltext_action(run: ResearchRun, paper_id: str) -> Any | None:
    for action in run.human_actions:
        if (
            action.action_type is HumanActionType.FULLTEXT_REQUIRED
            and action.status is HumanActionStatus.PENDING
            and paper_id in action.related_paper_ids
        ):
            return action
    return None


def _artifact_file_exists(root: Path, logical_key: str) -> bool:
    relative = Path(logical_key)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    resolved = (root / relative).resolve()
    return root in resolved.parents and resolved.is_file()


def _optional_integer(arguments: dict[str, PlanArgumentValue], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentPlanRejectedError(f"{name} must be an integer.")
    return value
