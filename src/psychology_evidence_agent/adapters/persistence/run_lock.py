"""Cross-platform process lock for one mutable ResearchRun directory."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from ...domain.errors import RunBusyError, RunPersistenceError

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - selected by the operating system
    import fcntl


class FileSystemResearchRunLock:
    """Hold an OS-level lock that is released when the owning process exits.

    The lock file is deliberately separate from ``state.json``. Atomic state
    replacement can therefore remain the persistence mechanism while the lock
    protects the complete load -> work -> artifact/state commit operation.
    """

    def __init__(self, root: Path, run_id: str, *, lock_name: str = ".run.lock") -> None:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise RunPersistenceError("Research run id must be a safe relative name.")
        if not lock_name or Path(lock_name).name != lock_name:
            raise RunPersistenceError("Research run lock name must be a safe file name.")
        self._path = root / "runs" / run_id / lock_name
        self._handle: BinaryIO | None = None

    @property
    def path(self) -> Path:
        """Return the diagnostic lock-file path."""
        return self._path

    def __enter__(self) -> FileSystemResearchRunLock:
        if self._handle is not None:
            raise RunBusyError(f"Research run lock is already held: {self._path.parent.name}")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        try:
            if self._path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                locking = getattr(msvcrt, "locking")
                locking(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
            else:
                flock = getattr(fcntl, "flock")
                flock(handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))
        except (OSError, ValueError) as error:
            handle.close()
            raise RunBusyError(
                f"Research run is already active in another process: {self._path.parent.name}"
            ) from error

        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                locking = getattr(msvcrt, "locking")
                locking(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
            else:
                getattr(fcntl, "flock")(handle.fileno(), getattr(fcntl, "LOCK_UN"))
        finally:
            handle.close()
