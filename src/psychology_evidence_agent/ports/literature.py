from __future__ import annotations

from typing import Protocol

from ..domain.paper import Paper


class LiteratureSearchPort(Protocol):
    def search(
        self,
        *,
        query_id: str,
        query: str,
        year_from: int,
        per_page: int,
        year_to: int | None = None,
    ) -> list[Paper]: ...
