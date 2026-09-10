from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...domain.enums import ArtifactType
from ...domain.run import ArtifactReference, utc_now


class FileSystemArtifactStore:
    """Store small, named run artifacts below an injected root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.absolute()
        self._resolved_root = self._root.resolve()

    def _path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Artifact name must stay below the configured storage root.")
        path = self._root / relative
        resolved_path = path.resolve()
        if (
            self._resolved_root not in resolved_path.parents
            and resolved_path != self._resolved_root
        ):
            raise ValueError("Artifact name must stay below the configured storage root.")
        return path

    def save_json(self, name: str, value: Any) -> Path:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_json(self, name: str) -> Any:
        return json.loads(self._path(name).read_text(encoding="utf-8"))

    def save_text(self, name: str, value: str) -> Path:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def save_bytes(self, name: str, value: bytes) -> Path:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def reference(
        self, name: str, artifact_type: ArtifactType, metadata: dict[str, str] | None = None
    ) -> ArtifactReference:
        """Return a logical reference for an artifact key without exposing an absolute path."""
        self._path(name)
        return ArtifactReference(
            artifact_id=f"{artifact_type.value}:{name}",
            artifact_type=artifact_type,
            logical_key=name,
            created_at=utc_now(),
            metadata=metadata or {},
        )

    def read_text(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")
