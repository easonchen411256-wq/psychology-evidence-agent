from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from ..domain.enums import ArtifactType
from ..domain.run import ArtifactReference, ResearchRun


class RunArtifactStore(Protocol):
    """Semantic storage boundary for artifacts belonging to one research run."""

    def save_json(self, name: str, value: Any) -> Path: ...

    def load_json(self, name: str) -> Any: ...

    def save_text(self, name: str, value: str) -> Path: ...

    def read_text(self, name: str) -> str: ...

    def save_bytes(self, name: str, value: bytes) -> Path: ...

    def reference(
        self, name: str, artifact_type: ArtifactType, metadata: dict[str, str] | None = None
    ) -> ArtifactReference: ...


class ResearchRunStore(Protocol):
    """Persistence boundary for lightweight ResearchRun state."""

    def create(self, run: ResearchRun) -> None: ...

    def save(self, run: ResearchRun) -> None: ...

    def load(self, run_id: str) -> ResearchRun: ...

    def exists(self, run_id: str) -> bool: ...


class ResearchRunLock(Protocol):
    """Process-level exclusive lock for one mutable research run."""

    def __enter__(self) -> ResearchRunLock: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class CancellationStore(Protocol):
    """Cross-request signal used to stop an active Agent cooperatively."""

    def request(self, run_id: str) -> None: ...

    def clear(self, run_id: str) -> None: ...

    def is_requested(self, run_id: str) -> bool: ...
