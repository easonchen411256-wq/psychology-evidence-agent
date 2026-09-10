"""Report local versions and configuration fingerprints for reproducible runs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .codex_cli_client import CodexCLIError, find_codex_executable
from .resources import default_config_names, load_default_config


def sha256_file(path: Path) -> str:
    """Return a stable fingerprint without exposing a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not installed"


def codex_version() -> str:
    """Return the CLI version, while keeping a missing CLI non-fatal."""
    if os.environ.get("PEA_SKIP_CODEX_CHECK") == "1":
        return "skipped by environment"
    try:
        executable = find_codex_executable()
        completed = subprocess.run(
            [executable, "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
            check=False,
            cwd=Path.cwd(),
        )
    except (CodexCLIError, FileNotFoundError, subprocess.TimeoutExpired):
        return "not available"
    if completed.returncode != 0:
        return "not available"
    return (completed.stdout or completed.stderr).strip() or "version unavailable"


def collect_environment() -> dict[str, Any]:
    """Collect only tool versions and hashes needed to reproduce a run."""
    configs = {
        f"resources/configs/{name}": hashlib.sha256(
            json.dumps(load_default_config(name), ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        for name in default_config_names()
    }
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": {
            "pypdf": package_version("pypdf"),
            "jsonschema": package_version("jsonschema"),
            "python-docx": package_version("python-docx"),
        },
        "codex_cli": codex_version(),
        "config_sha256": configs,
    }


def main() -> int:
    print(json.dumps(collect_environment(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
