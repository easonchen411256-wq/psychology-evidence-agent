"""Atomic file-system persistence for lightweight ResearchRun state."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...domain.errors import (
    RunAlreadyExistsError,
    RunConcurrencyError,
    RunNotFoundError,
    RunPersistenceError,
)
from ...domain.run import ResearchRun
from .run_lock import FileSystemResearchRunLock


class FileSystemResearchRunStore:
    """Store each run at ``<root>/runs/<run_id>/state.json``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def create(self, run: ResearchRun) -> None:
        path = self._state_path(run.run_id)
        with FileSystemResearchRunLock(self._root, run.run_id, lock_name=".state.lock"):
            if path.exists():
                raise RunAlreadyExistsError(f"Research run already exists: {run.run_id}")
            self._write_state(path, run)

    def save(self, run: ResearchRun) -> None:
        path = self._state_path(run.run_id)
        with FileSystemResearchRunLock(self._root, run.run_id, lock_name=".state.lock"):
            if not path.is_file():
                raise RunNotFoundError(f"Research run was not found: {run.run_id}")
            current = self._read_state(path, run.run_id)
            if current.revision != run.revision:
                raise RunConcurrencyError(
                    f"Research run {run.run_id} is stale: expected revision "
                    f"{run.revision}, found {current.revision}."
                )
            persisted = run.model_copy(deep=True)
            persisted.revision += 1
            self._write_state(path, persisted)
        run.revision = persisted.revision

    def load(self, run_id: str) -> ResearchRun:
        path = self._state_path(run_id)
        if not path.is_file():
            raise RunNotFoundError(f"Research run was not found: {run_id}")
        return self._read_state(path, run_id)

    @staticmethod
    def _read_state(path: Path, run_id: str) -> ResearchRun:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return ResearchRun.model_validate(value)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise RunPersistenceError(f"Research run state is invalid: {run_id}") from error

    def exists(self, run_id: str) -> bool:
        return self._state_path(run_id).is_file()

    def _state_path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise RunPersistenceError("Research run id must be a safe relative name.")
        return self._root / "runs" / run_id / "state.json"

    @staticmethod
    def _write_state(path: Path, run: ResearchRun) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    run.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RunPersistenceError(f"Could not persist research run: {run.run_id}") from error
