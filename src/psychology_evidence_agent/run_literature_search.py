"""Retrieve, deduplicate, and screen literature for the configured thesis topic."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .bootstrap import artifact_store, literature_search_service, screening_service
from .configuration import (  # noqa: F401
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_NAME,
    load_search_config,
)
from .domain.errors import LiteratureSearchError, StructuredOutputError
from .domain.paper import Paper
from .services.literature_search import deduplicate_papers, select_screening_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search and screen literature using OpenAlex and Codex CLI."
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="Optional user JSON configuration path"
    )
    parser.add_argument(
        "--max-per-query", type=int, default=15, help="OpenAlex results fetched for each query"
    )
    parser.add_argument(
        "--max-screen",
        type=int,
        default=30,
        help="Maximum deduplicated papers sent to Codex for screening",
    )
    parser.add_argument(
        "--screen-all",
        action="store_true",
        help="Screen every deduplicated candidate; may use substantial Codex capacity",
    )
    parser.add_argument(
        "--skip-screen", action="store_true", help="Only retrieve and deduplicate metadata"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/literature_search"))
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    artifact_store(path.parent).save_json(path.name, _json_value(value))


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def main() -> int:
    args = parse_args()
    if args.max_per_query < 1 or args.max_screen < 1:
        print("--max-per-query 和 --max-screen 必须大于 0。")
        return 2
    try:
        config = load_search_config(args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(f"无法读取检索配置：{error}")
        return 2

    all_papers: list[Paper] = []
    failures: list[dict[str, str]] = []
    for query_definition in config["queries"]:
        try:
            all_papers.extend(
                literature_search_service().search(
                    query_id=query_definition["id"],
                    query=query_definition["query"],
                    year_from=config["publication_year_from"],
                    per_page=args.max_per_query,
                )
            )
        except (
            LiteratureSearchError
        ) as error:  # Network/API failures should not erase successful queries.
            failures.append({"query_id": query_definition["id"], "error": str(error)[:300]})

    candidates = deduplicate_papers(all_papers)
    write_json(args.output_dir / "candidate_papers.json", candidates)
    shortlist, unscreened = select_screening_candidates(
        candidates, args.max_screen, args.screen_all
    )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "topic": config["topic"],
        "research_question": config["research_question"],
        "query_count": len(config["queries"]),
        "raw_result_count": len(all_papers),
        "deduplicated_candidate_count": len(candidates),
        "screened_candidate_count": 0 if args.skip_screen else len(shortlist),
        "unscreened_candidate_count": 0 if args.skip_screen else len(unscreened),
        "screening_selection_strategy": "all_deduplicated_candidates"
        if args.screen_all
        else "query_coverage_then_abstract_then_citations",
        "failures": failures,
    }
    write_json(args.output_dir / "search_report.json", report)
    print(f"已保存 {len(candidates)} 篇去重候选论文：{args.output_dir / 'candidate_papers.json'}")
    if failures:
        print(f"有 {len(failures)} 组查询失败；详见 search_report.json。")
    if args.skip_screen:
        return 0

    write_json(args.output_dir / "unscreened_papers.json", unscreened)
    try:
        screening = screening_service().screen(shortlist, config["research_question"])
    except StructuredOutputError as error:
        print(f"未完成 Codex 初筛：{error}")
        return 1
    write_json(
        args.output_dir / "screened_papers.json",
        [item.model_dump(mode="json") for item in screening],
    )
    candidate_by_id = {item.paper_id: item for item in shortlist}
    priority = [
        {**candidate_by_id[item.paper_id].model_dump(mode="json"), **item.model_dump(mode="json")}
        for item in screening
        if item.evidence_level in {"direct", "adjacent"} and item.relevance_score >= 7
    ]
    write_json(args.output_dir / "priority_reading_list.json", priority)
    print(f"已初筛 {len(screening)} 篇候选论文；优先阅读全文 {len(priority)} 篇。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
