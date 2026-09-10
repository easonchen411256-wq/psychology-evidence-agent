"""Discover and optionally download legally open PDFs from several public sources."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Callable
from os import fsync, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)
from uuid import uuid4

from .domain.fulltext import FullTextCandidate
from .openalex_search import paper_from_openalex

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
UNPAYWALL_URL = "https://api.unpaywall.org/v2"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent a provider URL from silently redirecting to a private address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_SAFE_URL_OPENER = build_opener(_NoRedirectHandler)


def _safe_urlopen(request: Request, *, timeout: float) -> Any:
    return _SAFE_URL_OPENER.open(request, timeout=timeout)


def _validate_public_download_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> None:
    """Allow only HTTPS URLs whose resolved addresses are public routable IPs."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS open-access PDF URLs can be downloaded.")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("The open-access PDF URL must not contain credentials.")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("The open-access PDF URL has an invalid port.") from error
    try:
        addresses = resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("The open-access PDF host could not be resolved.") from error
    if not addresses:
        raise ValueError("The open-access PDF host did not resolve to an address.")
    for address_info in addresses:
        address = ipaddress.ip_address(str(address_info[4][0]))
        if not address.is_global:
            raise ValueError("The open-access PDF URL resolves to a non-public address.")


def work_identifier(paper_id: str) -> str:
    return paper_id.rstrip("/").rsplit("/", 1)[-1]


def fetch_open_access_metadata(
    paper_id: str, opener: Callable[..., Any] = urlopen
) -> dict[str, Any]:
    """Refresh a candidate work so older search output can gain OA fields."""
    identifier = quote(work_identifier(paper_id), safe="")
    with opener(f"{OPENALEX_WORKS_URL}/{identifier}", timeout=30) as response:
        work = json.loads(response.read().decode("utf-8"))
    return paper_from_openalex(work, "open_access_lookup").model_dump(mode="json")


def fetch_json(url: str, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """Read a public JSON endpoint without retaining response bodies on disk."""
    request = Request(
        url, headers={"User-Agent": "psychology-evidence-agent/0.1 (open-access retrieval)"}
    )
    with opener(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Public metadata service returned an unexpected JSON value.")
    return value


def normalized_doi(value: Any) -> str:
    """Return a DOI suitable for source lookups, or an empty string."""
    doi = str(value or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip().rstrip(".")


def classify_access(paper: dict[str, Any]) -> dict[str, Any]:
    """Classify metadata conservatively; only an OA PDF URL is downloadable."""
    is_oa = bool(paper.get("is_open_access"))
    pdf_url = str(paper.get("open_access_pdf_url") or "")
    landing_url = str(paper.get("open_access_url") or "")
    if is_oa and pdf_url.startswith(("https://", "http://")):
        status = "open_pdf_available"
        next_step = "May be downloaded with --download after reviewing the candidate list."
    elif is_oa and landing_url.startswith(("https://", "http://")):
        status = "open_landing_page_only"
        next_step = "Open the landing page manually and confirm the accessible version."
    else:
        status = "manual_access_needed"
        next_step = (
            "No explicit open PDF was supplied by OpenAlex; obtain access manually if authorized."
        )
    return {
        "retrieval_source": "openalex",
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year", ""),
        "doi": paper.get("doi", ""),
        "is_open_access": is_oa,
        "open_access_pdf_url": pdf_url,
        "open_access_url": landing_url,
        "open_access_license": paper.get("open_access_license", ""),
        "access_status": status,
        "next_step": next_step,
    }


def _access_option(
    paper: dict[str, Any],
    *,
    source: str,
    pdf_url: str = "",
    landing_url: str = "",
    license_name: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Build a consistently conservative access option from one source."""
    has_pdf = pdf_url.startswith(("https://", "http://"))
    has_landing = landing_url.startswith(("https://", "http://"))
    if has_pdf:
        status = "open_pdf_available"
        next_step = "Confirmed public source returned a PDF URL; may be downloaded with --download."
    elif has_landing:
        status = "open_landing_page_only"
        next_step = "Public landing page found, but no confirmed downloadable PDF; review manually."
    else:
        status = "manual_access_needed"
        next_step = "This source did not provide a confirmed open full-text location."
    return {
        "retrieval_source": source,
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year", ""),
        "doi": normalized_doi(paper.get("doi", "")),
        "is_open_access": has_pdf or has_landing,
        "open_access_pdf_url": pdf_url,
        "open_access_url": landing_url,
        "open_access_license": license_name,
        "access_status": status,
        "next_step": next_step,
        "source_note": note,
    }


def fetch_unpaywall_options(
    paper: dict[str, Any],
    email: str,
    opener: Callable[..., Any] = urlopen,
) -> list[dict[str, Any]]:
    """Find legal OA locations listed by Unpaywall for a DOI.

    Unpaywall requires an email parameter. It is supplied at run time and never
    written to the project output.
    """
    doi = normalized_doi(paper.get("doi", ""))
    if not doi or not email.strip():
        return []
    payload = fetch_json(
        f"{UNPAYWALL_URL}/{quote(doi, safe='')}?{urlencode({'email': email.strip()})}",
        opener,
    )
    locations = [payload.get("best_oa_location")] + list(payload.get("oa_locations") or [])
    options: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for location in locations:
        if not isinstance(location, dict):
            continue
        pdf_url = str(location.get("url_for_pdf") or "")
        landing_url = str(location.get("url") or "")
        identity = (pdf_url, landing_url)
        if identity in seen or not any(identity):
            continue
        seen.add(identity)
        options.append(
            _access_option(
                paper,
                source="unpaywall",
                pdf_url=pdf_url,
                landing_url=landing_url,
                license_name=str(location.get("license") or ""),
                note=str(location.get("host_type") or "legal open location reported by Unpaywall"),
            )
        )
    return options


def fetch_europe_pmc_options(
    paper: dict[str, Any], opener: Callable[..., Any] = urlopen
) -> list[dict[str, Any]]:
    """Find open-access PMC/Europe PMC PDF locations by DOI, if present."""
    doi = normalized_doi(paper.get("doi", ""))
    if not doi:
        return []
    query = urlencode({"query": f"DOI:{doi}", "format": "json", "pageSize": "1"})
    payload = fetch_json(f"{EUROPE_PMC_SEARCH_URL}?{query}", opener)
    results = payload.get("resultList", {}).get("result", [])
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return []
    result = results[0]
    pmcid = str(result.get("pmcid") or "").upper()
    is_oa = str(result.get("isOpenAccess") or "").upper() in {"Y", "TRUE"}
    has_pdf = str(result.get("hasPDF") or "").upper() in {"Y", "TRUE"}
    if not pmcid:
        return []
    landing_url = f"https://europepmc.org/article/PMC/{pmcid}"
    pdf_url = (
        f"https://europepmc.org/articles/{pmcid.lower()}?pdf=render" if is_oa and has_pdf else ""
    )
    return [
        _access_option(
            paper,
            source="europe_pmc",
            pdf_url=pdf_url,
            landing_url=landing_url,
            license_name=str(result.get("license") or ""),
            note=f"PMCID {pmcid}; Europe PMC open-access metadata={is_oa}, PDF metadata={has_pdf}",
        )
    ]


def select_best_access_option(options: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer confirmed PDFs while retaining deterministic source preference."""
    source_order = {"openalex": 0, "unpaywall": 1, "europe_pmc": 2}
    status_order = {"open_pdf_available": 0, "open_landing_page_only": 1, "manual_access_needed": 2}
    return min(
        options,
        key=lambda option: (
            status_order.get(str(option.get("access_status")), 9),
            source_order.get(str(option.get("retrieval_source")), 9),
            str(option.get("open_access_pdf_url", "")),
        ),
    )


def discover_access_options(
    paper: dict[str, Any],
    *,
    unpaywall_email: str = "",
    opener: Callable[..., Any] = urlopen,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Merge allowed public sources and return a selected access record plus failures."""
    options = [classify_access(paper)]
    failures: list[dict[str, str]] = []
    for source, lookup in (
        ("unpaywall", lambda: fetch_unpaywall_options(paper, unpaywall_email, opener)),
        ("europe_pmc", lambda: fetch_europe_pmc_options(paper, opener)),
    ):
        try:
            options.extend(lookup())
        except Exception as error:
            failures.append({"source": source, "error": str(error)[:300]})
    selected = dict(select_best_access_option(options))
    selected["access_options"] = options
    selected["source_failures"] = failures
    candidate = FullTextCandidate.model_validate(selected)
    return candidate.model_dump(mode="json"), failures


def manual_retrieval_item(
    candidate: dict[str, Any], *, reason_override: str = ""
) -> dict[str, Any]:
    """Create the small, actionable queue for papers not auto-obtained."""
    options = candidate.get("access_options", [])
    checked_sources = [
        str(item.get("retrieval_source", "")) for item in options if isinstance(item, dict)
    ]
    landing_urls = [
        str(item.get("open_access_url", ""))
        for item in options
        if isinstance(item, dict) and item.get("open_access_url")
    ]
    reason = reason_override or (
        "Public sources found only landing pages; PDF availability or licence needs manual confirmation."
        if candidate.get("access_status") == "open_landing_page_only"
        else "No public source returned a confirmed downloadable open PDF."
    )
    return {
        "paper_id": candidate.get("paper_id", ""),
        "title": candidate.get("title", ""),
        "year": candidate.get("year", ""),
        "doi": candidate.get("doi", ""),
        "reason": reason,
        "checked_sources": checked_sources,
        "article_pages": landing_urls,
        "next_step": "Use the DOI or article page through your library or another authorized source; then save the lawful PDF under data/raw/.",
    }


def safe_pdf_filename(candidate: dict[str, Any]) -> str:
    title = (
        re.sub(r"[^A-Za-z0-9]+", "_", str(candidate.get("title", "paper"))).strip("_")[:70]
        or "paper"
    )
    identifier = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", work_identifier(str(candidate.get("paper_id", "")))).strip(
            "._"
        )[:80]
        or "openalex"
    )
    return f"{title}_{identifier}.pdf"


def download_open_pdf(
    candidate: dict[str, Any],
    output_dir: Path,
    *,
    overwrite: bool = False,
    opener: Callable[..., Any] = _safe_urlopen,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> Path:
    """Download only an explicitly OA PDF URL, with content and size checks."""
    if candidate.get("access_status") != "open_pdf_available":
        raise ValueError(
            "Only candidates explicitly classified as open_pdf_available can be downloaded."
        )
    url = str(candidate.get("open_access_pdf_url", ""))
    _validate_public_download_url(url, resolver=resolver)
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve()
    destination = (root / safe_pdf_filename(candidate)).resolve()
    if root not in destination.parents:
        raise ValueError("The PDF destination must stay below the download directory.")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {destination}")
    request = Request(
        url, headers={"User-Agent": "psychology-evidence-agent/0.1 (open-access retrieval)"}
    )
    with opener(request, timeout=60) as response:
        response_url = response.geturl() if hasattr(response, "geturl") else url
        _validate_public_download_url(str(response_url), resolver=resolver)
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                content_length_value = int(content_length)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "The open-access PDF returned an invalid content length."
                ) from error
            if content_length_value > MAX_DOWNLOAD_BYTES:
                raise ValueError("Open PDF exceeds the 50 MB download limit.")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Open PDF exceeds the 50 MB download limit.")
    if not data.startswith(b"%PDF-"):
        raise ValueError("The open-access link did not return a PDF; it was not saved.")
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            fsync(handle.fileno())
        replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
