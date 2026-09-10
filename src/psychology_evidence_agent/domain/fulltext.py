"""Stable result contract for lawful open-access discovery."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import OpenAccessStatus


class SourceFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    error: str


class FullTextCandidate(BaseModel):
    """A conservative public-access result; it never represents paywall bypass."""

    model_config = ConfigDict(extra="forbid")

    retrieval_source: str
    paper_id: str
    title: str = ""
    year: int | str = ""
    doi: str = ""
    is_open_access: bool
    open_access_pdf_url: str = ""
    open_access_url: str = ""
    open_access_license: str = ""
    access_status: OpenAccessStatus
    next_step: str
    source_note: str = ""
    access_options: list[dict[str, Any]] = Field(default_factory=list)
    source_failures: list[SourceFailure] = Field(default_factory=list)
