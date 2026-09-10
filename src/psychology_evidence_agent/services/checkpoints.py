"""Small persisted checkpoints for restartable workflow work units."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ..ports.persistence import RunArtifactStore

CHECKPOINTS_NAME = "workflow_checkpoints.json"


def input_fingerprint(value: Any) -> str:
    """Return a stable SHA-256 fingerprint for JSON-compatible stage input."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def bytes_fingerprint(value: bytes) -> str:
    """Return a stable SHA-256 fingerprint for a document or artifact."""
    return hashlib.sha256(value).hexdigest()


class WorkflowCheckpointStore:
    """Persist one metadata record per stage work unit.

    Checkpoint records intentionally contain only status, input identity,
    attempt metadata, and an artifact key. Large outputs remain separate
    artifacts and are loaded only when their checkpoint is reusable.
    """

    def __init__(self, artifact_store: RunArtifactStore) -> None:
        self._artifact_store = artifact_store

    def completed(self, stage: str, unit_id: str, fingerprint: str) -> dict[str, Any] | None:
        record = self.record(stage, unit_id)
        if not isinstance(record, dict):
            return None
        if record.get("status") != "completed":
            return None
        if record.get("input_fingerprint") != fingerprint:
            return None
        return record

    def record(self, stage: str, unit_id: str) -> dict[str, Any] | None:
        """Return the latest record, including running and stale records."""
        record = self._records().get(self._key(stage, unit_id))
        return record if isinstance(record, dict) else None

    def begin(self, stage: str, unit_id: str, fingerprint: str) -> int:
        records = self._records()
        key = self._key(stage, unit_id)
        previous = records.get(key)
        previous_attempts = previous.get("attempt_count", 0) if isinstance(previous, dict) else 0
        attempt_count = int(previous_attempts) + 1
        records[key] = {
            "stage": stage,
            "unit_id": unit_id,
            "status": "running",
            "attempt_count": attempt_count,
            "input_fingerprint": fingerprint,
            "started_at": _now(),
            "completed_at": None,
            "last_error": None,
            "retryable": False,
            "output_key": None,
        }
        self._save(records)
        return attempt_count

    def complete(
        self,
        stage: str,
        unit_id: str,
        fingerprint: str,
        *,
        output_key: str | None = None,
    ) -> None:
        records = self._records()
        key = self._key(stage, unit_id)
        previous = records.get(key)
        attempts = int(previous.get("attempt_count", 1)) if isinstance(previous, dict) else 1
        records[key] = {
            "stage": stage,
            "unit_id": unit_id,
            "status": "completed",
            "attempt_count": attempts,
            "input_fingerprint": fingerprint,
            "started_at": previous.get("started_at", _now())
            if isinstance(previous, dict)
            else _now(),
            "completed_at": _now(),
            "last_error": None,
            "retryable": False,
            "output_key": output_key,
        }
        self._save(records)

    def fail(
        self,
        stage: str,
        unit_id: str,
        fingerprint: str,
        *,
        error: str,
        retryable: bool,
    ) -> None:
        records = self._records()
        key = self._key(stage, unit_id)
        previous = records.get(key)
        attempts = int(previous.get("attempt_count", 1)) if isinstance(previous, dict) else 1
        records[key] = {
            "stage": stage,
            "unit_id": unit_id,
            "status": "failed",
            "attempt_count": attempts,
            "input_fingerprint": fingerprint,
            "started_at": previous.get("started_at", _now())
            if isinstance(previous, dict)
            else _now(),
            "completed_at": None,
            "last_error": error[:500],
            "retryable": retryable,
            "output_key": None,
        }
        self._save(records)

    def _records(self) -> dict[str, dict[str, Any]]:
        try:
            payload = self._artifact_store.load_json(CHECKPOINTS_NAME)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
            return {}
        return {
            str(key): value for key, value in payload["entries"].items() if isinstance(value, dict)
        }

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        self._artifact_store.save_json(
            CHECKPOINTS_NAME,
            {"version": 1, "updated_at": _now(), "entries": records},
        )

    @staticmethod
    def _key(stage: str, unit_id: str) -> str:
        return f"{stage}:{unit_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat()
