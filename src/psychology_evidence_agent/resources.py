"""Access packaged runtime resources without assuming a source checkout exists."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

_PACKAGE_NAME = "psychology_evidence_agent"
_RESOURCE_ROOT = files(_PACKAGE_NAME).joinpath("resources")
_WEB_RESOURCE_CONTEXT = ExitStack()


def _resource(directory: str, name: str) -> Traversable:
    resource = _RESOURCE_ROOT.joinpath(directory, name)
    if not resource.is_file():
        raise FileNotFoundError(f"Packaged resource was not found: {directory}/{name}")
    return resource


def load_prompt(name: str) -> str:
    """Read one built-in Codex prompt as UTF-8 text."""
    return _resource("prompts", name).read_text(encoding="utf-8").strip()


def load_schema(name: str) -> dict[str, Any]:
    """Read one built-in JSON Schema without exposing filesystem assumptions."""
    value = json.loads(_resource("schemas", name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Packaged schema must be a JSON object: {name}")
    return value


@contextmanager
def schema_file(name: str) -> Iterator[Path]:
    """Materialize a schema as a real path for the Codex CLI output-schema flag."""
    with as_file(_resource("schemas", name)) as path:
        yield path


def load_default_config(name: str) -> dict[str, Any]:
    """Read an immutable built-in configuration; user configurations stay external."""
    value = json.loads(_resource("configs", name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Packaged configuration must be a JSON object: {name}")
    return value


def copy_default_config(name: str, destination: Path) -> Path:
    """Copy a packaged default to an editable external file without overwriting it."""
    if destination.exists():
        raise FileExistsError(f"Configuration file already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_resource("configs", name).read_bytes())
    return destination


def default_config_names() -> list[str]:
    """List bundled configurations for diagnostics without relying on a repository."""
    return sorted(
        item.name for item in _RESOURCE_ROOT.joinpath("configs").iterdir() if item.is_file()
    )


def web_directory() -> Path:
    """Return a real directory for FastAPI static files for this process lifetime."""
    return _WEB_RESOURCE_CONTEXT.enter_context(as_file(_RESOURCE_ROOT.joinpath("web")))


def web_resource(name: str) -> Traversable:
    """Return a packaged web asset for diagnostics and tests."""
    return _resource("web", name)
