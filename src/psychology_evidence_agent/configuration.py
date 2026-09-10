"""Validated user-facing configuration contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .resources import load_default_config

CONFIG_ENV_VAR = "PEA_CONFIG"
DEFAULT_CONFIG_NAME = "default.literature_search.json"


class SearchQueryConfig(BaseModel):
    """One literature query and its human-readable purpose."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=3, max_length=500)
    purpose: str = Field(min_length=1, max_length=500)

    @field_validator("id", "query", "purpose")
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("configuration text values must not be blank")
        return cleaned


class SearchConfig(BaseModel):
    """Complete configuration required by the literature-search command."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=500)
    research_question: str = Field(min_length=1, max_length=2000)
    publication_year_from: int = Field(ge=1900, le=2100)
    queries: list[SearchQueryConfig] = Field(min_length=1, max_length=100)

    @field_validator("topic", "research_question")
    @classmethod
    def long_text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("configuration text values must not be blank")
        return cleaned

    @model_validator(mode="after")
    def query_ids_must_be_unique(self) -> SearchConfig:
        ids = [query.id for query in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError("configuration query ids must be unique")
        return self


def validate_search_config(value: object) -> dict[str, object]:
    """Validate and normalize JSON configuration before command execution."""
    return SearchConfig.model_validate(value).model_dump(mode="python")


def load_search_config(explicit_config: Path | None) -> dict[str, Any]:
    """Apply explicit path, environment path, then packaged default precedence."""
    configured_path = explicit_config
    if configured_path is None and os.environ.get(CONFIG_ENV_VAR):
        configured_path = Path(os.environ[CONFIG_ENV_VAR])
    if configured_path is not None:
        value = json.loads(configured_path.read_text(encoding="utf-8"))
        return cast(dict[str, Any], validate_search_config(value))
    return cast(dict[str, Any], validate_search_config(load_default_config(DEFAULT_CONFIG_NAME)))
