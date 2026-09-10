"""Small, durable cancellation signals for active web Agent jobs."""

from __future__ import annotations

import os
from pathlib import Path

from ...domain.errors import RunPersistenceError


class FileSystemCancellationStore:
    """Persist a stop request outside the run lock held by the worker.

    The marker is intentionally not part of ``ResearchRun``: requesting a stop
    must remain possible while the worker owns the run's exclusive lock.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def request(self, run_id: str) -> None:
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            temporary.write_text("requested\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RunPersistenceError(
                "Could not persist the Agent cancellation request."
            ) from error

    def clear(self, run_id: str) -> None:
        try:
            self._path(run_id).unlink(missing_ok=True)
        except OSError as error:
            raise RunPersistenceError("Could not clear the Agent cancellation request.") from error

    def is_requested(self, run_id: str) -> bool:
        return self._path(run_id).is_file()

    def _path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise RunPersistenceError("Research run id must be a safe relative name.")
        return self._root / "runs" / run_id / "control" / "cancel.requested"
