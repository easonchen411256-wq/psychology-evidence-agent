"""Prepare literature-review data and delegate document rendering to an exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class ReportExporter(Protocol):
    """Boundary used by the review-report service to render a prepared report."""

    def export(
        self,
        report: dict[str, Any],
        records: list[dict[str, Any]],
        counts: dict[str, int],
        output_path: Path | BinaryIO,
        *,
        abstract_limit: int,
    ) -> None: ...


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def text(value: Any, fallback: str = "未报告") -> str:
    value = str(value or "").strip()
    return value or fallback


def truncate(value: Any, limit: int) -> str:
    content = text(value, "未提供摘要")
    if limit > 0 and len(content) > limit:
        return (
            content[:limit].rstrip()
            + f"\n\n[摘要已截取前 {limit} 个字符；如需完整摘要，请使用 --abstract-limit 0。]"
        )
    return content


def evidence_level_label(value: Any) -> str:
    return {
        "direct": "直接证据候选",
        "adjacent": "相邻证据",
        "background": "背景证据",
        "uncertain": "需人工判断",
    }.get(str(value or ""), "未标记")


def access_label(access: dict[str, Any] | None) -> str:
    if not access:
        return "未运行全文获取"
    source = text(access.get("retrieval_source"), "OpenAlex")
    status = str(access.get("access_status") or "")
    labels = {
        "open_pdf_available": "可自动下载开放 PDF",
        "open_landing_page_only": "需人工确认开放页面",
        "manual_access_needed": "需通过授权渠道获取",
    }
    return f"{labels.get(status, '全文状态未报告')}（来源：{source}）"


def load_review_records(
    search_dir: Path, *, all_screened: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """Join candidate metadata, screening judgments and optional full-text results."""
    candidates = read_json(search_dir / "candidate_papers.json", [])
    screened = read_json(search_dir / "screened_papers.json", [])
    priority = read_json(search_dir / "priority_reading_list.json", [])
    access_payload = read_json(search_dir / "fulltext_access_candidates.json", None)
    if access_payload is None:
        access_payload = read_json(search_dir / "open_access_candidates.json", {})
    access_records = (
        access_payload.get("candidates", []) if isinstance(access_payload, dict) else []
    )
    manual_payload = read_json(search_dir / "manual_retrieval_queue.json", None)
    if isinstance(manual_payload, dict):
        manual_items = manual_payload.get("items", [])
    else:
        manual_items = [
            {
                "paper_id": item.get("paper_id", ""),
                "reason": "旧版全文清单未生成人工队列；该论文尚无确认可下载的开放 PDF。",
                "checked_sources": [text(item.get("retrieval_source"), "OpenAlex")],
                "article_pages": [item["open_access_url"]] if item.get("open_access_url") else [],
                "next_step": "使用 DOI 或文章页通过学校图书馆等已授权途径获取全文。",
            }
            for item in access_records
            if isinstance(item, dict) and item.get("access_status") != "open_pdf_available"
        ]
    report = read_json(search_dir / "search_report.json", {})

    candidate_index = {
        str(item.get("paper_id", "")): item for item in candidates if isinstance(item, dict)
    }
    screening_index = {
        str(item.get("paper_id", "")): item for item in screened if isinstance(item, dict)
    }
    access_index = {
        str(item.get("paper_id", "")): item for item in access_records if isinstance(item, dict)
    }
    manual_index = {
        str(item.get("paper_id", "")): item for item in manual_items if isinstance(item, dict)
    }
    selected = screened if all_screened or not priority else priority

    records: list[dict[str, Any]] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id", ""))
        merged = {
            **candidate_index.get(paper_id, {}),
            **screening_index.get(paper_id, {}),
            **item,
            "access": access_index.get(paper_id),
            "manual_retrieval": manual_index.get(paper_id),
        }
        if paper_id:
            records.append(merged)

    counts = {
        "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "screened_count": len(screened) if isinstance(screened, list) else 0,
        "priority_count": len(priority) if isinstance(priority, list) else 0,
        "selected_count": len(records),
        "open_pdf_count": sum(
            1
            for item in access_records
            if isinstance(item, dict) and item.get("access_status") == "open_pdf_available"
        ),
        "manual_queue_count": len(manual_items) if isinstance(manual_items, list) else 0,
    }
    return report if isinstance(report, dict) else {}, records, counts


def build_review_report(
    search_dir: Path,
    output_path: Path | BinaryIO,
    *,
    all_screened: bool = False,
    abstract_limit: int = 1200,
    exporter: ReportExporter | None = None,
) -> tuple[int, dict[str, int]]:
    """Create a report through the configured exporter and return its summary."""
    report, records, counts = load_review_records(search_dir, all_screened=all_screened)
    if not records:
        raise ValueError(
            "No screened or priority papers found. Run literature screening before generating a review report."
        )
    if exporter is None:
        from .documents.word_exporter import WordReportExporter

        exporter = WordReportExporter()
    exporter.export(report, records, counts, output_path, abstract_limit=abstract_limit)
    return len(records), counts
