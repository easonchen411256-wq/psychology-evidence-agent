from __future__ import annotations

from ...domain.enums import OpenAccessStatus
from ...domain.fulltext import FullTextCandidate
from ...domain.paper import Paper


def candidate(
    paper: Paper,
    *,
    source: str,
    pdf_url: str = "",
    landing_url: str = "",
    license_name: str = "",
    note: str = "",
) -> FullTextCandidate:
    if pdf_url.startswith(("https://", "http://")):
        status, next_step = (
            "open_pdf_available",
            "Confirmed public source returned a PDF URL; may be downloaded with --download.",
        )
    elif landing_url.startswith(("https://", "http://")):
        status, next_step = (
            "open_landing_page_only",
            "Public landing page found, but no confirmed downloadable PDF; review manually.",
        )
    else:
        status, next_step = (
            "manual_access_needed",
            "This source did not provide a confirmed open full-text location.",
        )
    return FullTextCandidate(
        retrieval_source=source,
        paper_id=paper.paper_id,
        title=paper.title,
        year=paper.year,
        doi=paper.doi,
        is_open_access=bool(pdf_url or landing_url),
        open_access_pdf_url=pdf_url,
        open_access_url=landing_url,
        open_access_license=license_name,
        access_status=OpenAccessStatus(status),
        next_step=next_step,
        source_note=note,
    )
