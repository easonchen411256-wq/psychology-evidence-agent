"""Small deterministic helpers shared by the batch evidence-card command."""

from __future__ import annotations

import json
from pathlib import Path

from .paper_input import SUPPORTED_SUFFIXES
from .resources import load_default_config


def discover_paper_texts(input_dir: Path) -> list[Path]:
    """Return supported paper texts in stable order, including raw-material subfolders."""
    if not input_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ),
        key=lambda path: str(path).lower(),
    )


def default_research_question() -> str:
    """Reuse the configured search question when present; never invent a replacement."""
    try:
        config = load_default_config("default.literature_search.json")
    except (FileNotFoundError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    question = config.get("research_question", "")
    return question.strip() if isinstance(question, str) else ""


def evidence_card_path(source_path: Path, output_dir: Path, input_dir: Path | None = None) -> Path:
    """Preserve the source's relative directory so same-name inputs cannot collide."""
    if input_dir is not None:
        try:
            relative = source_path.relative_to(input_dir)
        except ValueError:
            relative = Path(source_path.name)
    else:
        relative = Path(source_path.name)
    return output_dir / relative.with_suffix(".evidence_card.json")
