"""Canonical, provider-neutral literature metadata."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Paper(BaseModel):
    """Normalized paper metadata produced from an external search result."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str
    doi: str = ""
    title: str = ""
    abstract: str = ""
    year: int | str = ""
    venue: str = ""
    authors: list[str] = Field(default_factory=list)
    cited_by_count: int = 0
    openalex_url: str = ""
    is_open_access: bool = False
    open_access_url: str = ""
    open_access_pdf_url: str = ""
    open_access_license: str = ""
    matched_queries: list[str] = Field(default_factory=list)
    query_coverage: int = 0
