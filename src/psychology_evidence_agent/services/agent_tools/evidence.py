"""Bounded Agent tools for resumable evidence-card extraction."""

from __future__ import annotations

import re
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
from ...domain.enums import ArtifactType, RunStage
from ...domain.errors import AgentPlanRejectedError
from ...domain.evidence import EvidenceCard
from ...domain.run import ArtifactReference, ResearchRun
from ...evidence_card import validate_evidence_card
from ...ports.documents import DocumentReaderPort
from ...ports.persistence import RunArtifactStore
from ...resources import load_prompt, load_schema
from ..checkpoints import WorkflowCheckpointStore, bytes_fingerprint, input_fingerprint
from ..evidence_extraction import EvidenceExtractionService

_QUEUE = "agent/evidence_queue.json"
_BATCH_DIR = "agent/evidence_batches"
_MAX_QUEUE_ITEMS = 100
_MAX_BATCH_SIZE = 10
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_CARD_STAGE = "extracting_evidence"


class AdaptiveEvidencePrepareTool:
    """Create or reuse a queue from the stable full-text manifest."""

    def __init__(self, artifact_store: RunArtifactStore, artifact_root: Path) -> None:
        self._artifact_store = artifact_store
        self._artifact_root = artifact_root.resolve()
        self._descriptor = ToolDescriptor(
            name="evidence.prepare",
            description="Prepare a bounded evidence-card queue from full-text documents.",
            output_description="A resumable evidence-card extraction queue.",
            stage=RunStage.EXTRACTING_EVIDENCE,
            effect=ToolEffect.LOCAL,
            argument_specs=[
                ToolArgumentSpec(
                    name="max_cards",
                    value_type=ToolArgumentType.INTEGER,
                    description="Maximum full-text documents to place in the queue.",
                )
            ],
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        max_cards = _optional_integer(arguments, "max_cards", default=_MAX_QUEUE_ITEMS)
        if not 1 <= max_cards <= _MAX_QUEUE_ITEMS:
            raise AgentPlanRejectedError("max_cards must be between 1 and 100.")
        try:
            manifest = self._artifact_store.load_json("fulltext_documents.json")
            documents = _documents_from_manifest(manifest)
            documents = documents[:max_cards]
            fingerprints = [
                _document_fingerprint(
                    self._artifact_root,
                    document,
                    run.research_question,
                )
                for document in documents
            ]
        except (FileNotFoundError, OSError, TypeError, ValueError) as error:
            return ToolResult(
                success=False, summary=f"Full-text documents are unavailable: {error}"
            )
        if not documents:
            return ToolResult(
                success=False, summary="No full-text documents are available for extraction."
            )

        fingerprint = input_fingerprint(
            {
                "documents": documents,
                "document_fingerprints": fingerprints,
                "research_question": run.research_question,
                "resources": _resource_fingerprint(),
                "max_cards": max_cards,
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
                summary="Reused the persisted evidence-card queue.",
                stage_complete=False,
            )

        queue = {
            "version": 1,
            "input_fingerprint": fingerprint,
            "batch_count": 0,
            "items": [
                {
                    "paper_id": str(document["paper_id"]),
                    "document": document,
                    "card_key": _card_key(str(document["paper_id"])),
                    "input_fingerprint": item_fingerprint,
                    "status": "pending",
                }
                for document, item_fingerprint in zip(documents, fingerprints, strict=True)
            ],
        }
        self._artifact_store.save_json(_QUEUE, queue)
        return ToolResult(
            success=True,
            summary=f"Prepared {len(documents)} document(s) for evidence-card extraction.",
            stage_complete=False,
        )


class AdaptiveEvidenceProcessNextTool:
    """Extract only the next bounded batch through the existing validated service."""

    def __init__(
        self,
        artifact_store: RunArtifactStore,
        artifact_root: Path,
        document_reader: DocumentReaderPort,
        evidence_extraction: EvidenceExtractionService,
    ) -> None:
        self._artifact_store = artifact_store
        self._artifact_root = artifact_root.resolve()
        self._document_reader = document_reader
        self._evidence_extraction = evidence_extraction
        self._checkpoints = WorkflowCheckpointStore(artifact_store)
        self._descriptor = ToolDescriptor(
            name="evidence.process_next",
            description="Extract validated evidence cards from the next bounded document batch.",
            output_description="Evidence-card artifacts validated by the existing schema and guardrails.",
            stage=RunStage.EXTRACTING_EVIDENCE,
            effect=ToolEffect.MODEL,
            argument_specs=[
                ToolArgumentSpec(
                    name="batch_size",
                    value_type=ToolArgumentType.INTEGER,
                    description="Number of full-text documents to process in this call.",
                )
            ],
            retryable=False,
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
            return ToolResult(success=False, summary="No valid evidence-card queue is available.")

        processed = 0
        references: list[ArtifactReference] = []
        for item in cast(list[dict[str, Any]], queue["items"]):
            if processed >= batch_size:
                break
            if item.get("status") == "completed":
                reference = self._valid_card_reference(item)
                if reference is not None:
                    references.append(reference)
                    continue
                item["status"] = "pending"

            card_key = str(item.get("card_key", ""))
            paper_id = str(item.get("paper_id", ""))
            document = item.get("document")
            if not paper_id or not isinstance(document, dict):
                return ToolResult(
                    success=False, summary="The evidence queue contains invalid metadata."
                )
            try:
                document_path = _artifact_path(self._artifact_root, str(document["logical_key"]))
                current_fingerprint = _document_fingerprint(
                    self._artifact_root, document, run.research_question
                )
            except (KeyError, OSError, TypeError, ValueError) as error:
                return ToolResult(
                    success=False, summary=f"Evidence input is invalid for {paper_id}: {error}"
                )
            if current_fingerprint != item.get("input_fingerprint"):
                return ToolResult(
                    success=False,
                    summary=f"Full-text input changed for {paper_id}; prepare the evidence queue again.",
                )

            card_reference = self._reuse_existing_card(item, current_fingerprint)
            if card_reference is not None:
                item["status"] = "completed"
                references.append(card_reference)
                processed += 1
                continue

            unit_id = paper_id
            self._checkpoints.begin(_CARD_STAGE, unit_id, current_fingerprint)
            try:
                paper_text = self._document_reader.read_text(
                    document_path,
                    max_chars=160_000,
                    allow_large_input=False,
                )
                card = self._evidence_extraction.extract(
                    paper_text=paper_text,
                    research_question=run.research_question,
                )
                if validate_evidence_card(card):
                    raise ValueError(
                        "Evidence card failed the independent inference-boundary validation."
                    )
                self._artifact_store.save_json(card_key, card.model_dump(mode="json"))
            except Exception as error:
                self._checkpoints.fail(
                    _CARD_STAGE,
                    unit_id,
                    current_fingerprint,
                    error=str(error),
                    retryable=False,
                )
                item["status"] = "failed"
                item["last_error"] = str(error)[:300]
                self._artifact_store.save_json(_QUEUE, queue)
                return ToolResult(
                    success=False,
                    summary=f"Evidence-card extraction failed for {paper_id}: {str(error)[:240]}",
                )

            self._checkpoints.complete(
                _CARD_STAGE,
                unit_id,
                current_fingerprint,
                output_key=card_key,
            )
            item["status"] = "completed"
            item.pop("last_error", None)
            references.append(self._card_reference(card_key, paper_id))
            processed += 1
            self._artifact_store.save_json(_QUEUE, queue)

        self._artifact_store.save_json(_QUEUE, queue)
        if processed:
            _save_batch(self._artifact_store, queue)
        return ToolResult(
            success=True,
            summary=f"Processed {processed} evidence-card queue item(s).",
            artifact_references=references,
            stage_complete=False,
        )

    def _reuse_existing_card(
        self, item: dict[str, Any], fingerprint: str
    ) -> ArtifactReference | None:
        card_key = str(item.get("card_key", ""))
        try:
            checkpoint = self._checkpoints.completed(
                _CARD_STAGE, str(item["paper_id"]), fingerprint
            )
            if checkpoint is not None and checkpoint.get("output_key"):
                card_key = str(checkpoint["output_key"])
            payload = self._artifact_store.load_json(card_key)
            _validated_card(payload)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        return self._card_reference(card_key, str(item["paper_id"]))

    def _valid_card_reference(self, item: dict[str, Any]) -> ArtifactReference | None:
        card_key = str(item.get("card_key", ""))
        try:
            payload = self._artifact_store.load_json(card_key)
            _validated_card(payload)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        return self._card_reference(card_key, str(item["paper_id"]))

    def _card_reference(self, card_key: str, paper_id: str) -> ArtifactReference:
        return self._artifact_store.reference(
            card_key,
            ArtifactType.EVIDENCE_CARD,
            metadata={"paper_id": paper_id},
        )


class AdaptiveEvidenceFinalizeTool:
    """Publish all validated evidence-card references after the queue is terminal."""

    def __init__(self, artifact_store: RunArtifactStore) -> None:
        self._artifact_store = artifact_store
        self._descriptor = ToolDescriptor(
            name="evidence.finalize",
            description="Finalize the evidence-card queue for deterministic synthesis.",
            output_description="All validated evidence-card artifact references.",
            stage=RunStage.EXTRACTING_EVIDENCE,
            effect=ToolEffect.LOCAL,
        )

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(self, run: ResearchRun, arguments: dict[str, PlanArgumentValue]) -> ToolResult:
        if arguments:
            raise AgentPlanRejectedError("evidence.finalize does not accept arguments.")
        queue = _load_queue(self._artifact_store)
        if queue is None or not _queue_is_valid(queue):
            return ToolResult(success=False, summary="No valid evidence-card queue is available.")
        items = cast(list[dict[str, Any]], queue["items"])
        pending = [item for item in items if item.get("status") != "completed"]
        if pending:
            return ToolResult(
                success=True,
                summary=f"{len(pending)} evidence-card queue item(s) remain; continue with the next batch.",
                stage_complete=False,
                replan_required=True,
            )
        references: list[ArtifactReference] = []
        for item in items:
            card_key = str(item.get("card_key", ""))
            try:
                _validated_card(self._artifact_store.load_json(card_key))
            except (FileNotFoundError, OSError, TypeError, ValueError) as error:
                return ToolResult(success=False, summary=f"Evidence artifact is invalid: {error}")
            references.append(
                self._artifact_store.reference(
                    card_key,
                    ArtifactType.EVIDENCE_CARD,
                    metadata={"paper_id": str(item["paper_id"])},
                )
            )
        return ToolResult(
            success=True,
            summary=f"Finalized {len(references)} evidence card(s).",
            artifact_references=references,
        )


def _load_queue(artifact_store: RunArtifactStore) -> dict[str, Any] | None:
    try:
        payload = artifact_store.load_json(_QUEUE)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _queue_is_valid(queue: dict[str, Any]) -> bool:
    items = queue.get("items")
    return bool(
        isinstance(items, list)
        and items
        and all(
            isinstance(item, dict)
            and isinstance(item.get("paper_id"), str)
            and isinstance(item.get("document"), dict)
            and isinstance(item.get("card_key"), str)
            and item.get("status") in {"pending", "failed", "completed"}
            for item in items
        )
    )


def _documents_from_manifest(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise ValueError("Full-text manifest must contain a documents array.")
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cast(list[Any], payload["documents"]):
        if not isinstance(item, dict):
            raise ValueError("Full-text manifest contains invalid document metadata.")
        paper_id = str(item.get("paper_id", ""))
        logical_key = str(item.get("logical_key", ""))
        if not paper_id or not logical_key:
            raise ValueError("Each full-text document needs paper_id and logical_key.")
        if paper_id in seen:
            raise ValueError(f"Duplicate full-text paper_id: {paper_id}.")
        seen.add(paper_id)
        documents.append(dict(item))
    return documents


def _artifact_path(root: Path, logical_key: str) -> Path:
    relative = Path(logical_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Artifact logical key must remain below the run artifact root.")
    resolved = (root / relative).resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("Full-text artifact is not an existing file below the run artifact root.")
    return resolved


def _document_fingerprint(root: Path, document: dict[str, Any], research_question: str) -> str:
    path = _artifact_path(root, str(document["logical_key"]))
    return input_fingerprint(
        {
            "paper_id": str(document["paper_id"]),
            "document_key": str(document["logical_key"]),
            "document": bytes_fingerprint(path.read_bytes()),
            "research_question": research_question,
            "resources": _resource_fingerprint(),
        }
    )


def _resource_fingerprint() -> dict[str, Any]:
    return {
        "prompt": load_prompt("evidence_extraction_prompt.md"),
        "schema": load_schema("evidence_card.schema.json"),
    }


def _card_key(paper_id: str) -> str:
    safe = _SAFE_COMPONENT.sub("_", paper_id).strip("._") or "paper"
    return f"evidence_cards/{safe}.json"


def _validated_card(payload: Any) -> EvidenceCard:
    card = EvidenceCard.model_validate(payload)
    errors = validate_evidence_card(card)
    if errors:
        raise ValueError("; ".join(errors))
    return card


def _save_batch(artifact_store: RunArtifactStore, queue: dict[str, Any]) -> None:
    try:
        batch_number = int(queue.get("batch_count", 0)) + 1
    except (TypeError, ValueError):
        batch_number = 1
    queue["batch_count"] = batch_number
    artifact_store.save_json(_QUEUE, queue)
    artifact_store.save_json(
        f"{_BATCH_DIR}/batch_{batch_number}.json",
        {
            "batch_number": batch_number,
            "queue_fingerprint": queue.get("input_fingerprint", ""),
            "items": [
                item
                for item in cast(list[dict[str, Any]], queue["items"])
                if item.get("status") == "completed"
            ],
        },
    )


def _optional_integer(arguments: dict[str, PlanArgumentValue], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentPlanRejectedError(f"{name} must be an integer.")
    return value
