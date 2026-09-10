"""Create an OA access list from OpenAlex candidates and optionally download open PDFs."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .bootstrap import artifact_store, full_text_service
from .domain.errors import ExternalServiceError
from .open_access_fulltext import (
    download_open_pdf,
    manual_retrieval_item,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find legally open full-text PDFs from public sources for candidate papers."
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/processed/literature_search/candidate_papers.json"),
    )
    parser.add_argument(
        "--priority-list",
        type=Path,
        default=Path("data/processed/literature_search/priority_reading_list.json"),
    )
    parser.add_argument(
        "--all-candidates",
        action="store_true",
        help="Use all candidate papers instead of the screened priority list",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/literature_search/open_access_candidates.json"),
    )
    parser.add_argument(
        "--download", action="store_true", help="Download only confirmed legally open PDFs"
    )
    parser.add_argument("--download-dir", type=Path, default=Path("data/raw/open_access"))
    parser.add_argument(
        "--download-report",
        type=Path,
        default=None,
        help="JSON report path; defaults beside --output",
    )
    parser.add_argument(
        "--manual-queue",
        type=Path,
        default=None,
        help="JSON queue for papers needing authorized manual access; defaults beside --output",
    )
    parser.add_argument(
        "--unpaywall-email",
        default=os.environ.get("UNPAYWALL_EMAIL", ""),
        help="Optional email required by Unpaywall; use environment variable to avoid placing it in shell history",
    )
    parser.add_argument(
        "--max-papers", type=int, default=20, help="Maximum candidates to refresh from OpenAlex"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing the OA list and existing downloaded PDFs",
    )
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    artifact_store(path.parent).save_json(path.name, value)


def main() -> int:
    args = parse_args()
    if args.max_papers < 1:
        print("--max-papers must be at least 1.")
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output already exists: {args.output}. Add --overwrite to refresh it.")
        return 2
    try:
        candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        print(f"Could not read candidates: {error}")
        return 2
    if not isinstance(candidates, list):
        print("Candidate file must contain a JSON array.")
        return 2

    selected = candidates
    if not args.all_candidates:
        try:
            priority = json.loads(args.priority_list.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            print(
                f"Could not read priority list: {error}. Use --all-candidates to proceed without it."
            )
            return 2
        if not isinstance(priority, list):
            print("Priority list must contain a JSON array.")
            return 2
        priority_ids = {
            str(item.get("paper_id", "")) for item in priority if isinstance(item, dict)
        }
        selected = [
            item
            for item in candidates
            if isinstance(item, dict) and str(item.get("paper_id", "")) in priority_ids
        ]
        if not selected:
            print(
                "No priority papers could be matched to the candidate file. Use --all-candidates to inspect all candidates."
            )
            return 2

    access_list: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for candidate in selected[: args.max_papers]:
        paper_id = str(candidate.get("paper_id", "")) if isinstance(candidate, dict) else ""
        if not paper_id:
            failures.append({"paper_id": "", "error": "Candidate has no paper_id."})
            continue
        try:
            access_record = full_text_service().discover(
                paper_id=paper_id, unpaywall_email=args.unpaywall_email
            )
            access_list.append(access_record.model_dump(mode="json"))
            for failure in access_record.source_failures:
                failures.append(
                    {
                        "paper_id": paper_id,
                        "source": failure.source,
                        "error": failure.error,
                    }
                )
        except ExternalServiceError as error:
            failures.append({"paper_id": paper_id, "error": str(error)[:300]})
    manual_queue = [
        manual_retrieval_item(candidate)
        for candidate in access_list
        if candidate["access_status"] != "open_pdf_available"
    ]
    payload = {
        "candidates": access_list,
        "failures": failures,
        "unpaywall_enabled": bool(args.unpaywall_email),
    }
    write_json(args.output, payload)
    print(f"Wrote {len(access_list)} access candidates to: {args.output}")
    if failures:
        print(f"Could not refresh {len(failures)} candidates; see failures in the output file.")
    queue_path = args.manual_queue or args.output.with_name("manual_retrieval_queue.json")
    write_json(
        queue_path,
        {
            "generated_at": datetime.now().astimezone().isoformat(),
            "queue_count": len(manual_queue),
            "items": manual_queue,
        },
    )
    print(f"Manual retrieval queue: {queue_path} ({len(manual_queue)} papers)")

    if not args.download:
        return 0
    report_path = args.download_report or args.output.with_name("open_access_download_report.json")
    downloaded = 0
    download_failures = 0
    download_results: list[dict[str, str]] = []
    for candidate in access_list:
        if candidate["access_status"] != "open_pdf_available":
            download_results.append(
                {
                    "paper_id": str(candidate["paper_id"]),
                    "title": str(candidate["title"]),
                    "retrieval_source": str(candidate.get("retrieval_source", "")),
                    "status": "not_attempted",
                    "detail": str(candidate["access_status"]),
                }
            )
            continue
        try:
            path = download_open_pdf(candidate, args.download_dir, overwrite=args.overwrite)
            print(f"Downloaded: {path}")
            downloaded += 1
            download_results.append(
                {
                    "paper_id": str(candidate["paper_id"]),
                    "title": str(candidate["title"]),
                    "retrieval_source": str(candidate.get("retrieval_source", "")),
                    "status": "downloaded",
                    "detail": str(path),
                }
            )
        except FileExistsError as error:
            download_results.append(
                {
                    "paper_id": str(candidate["paper_id"]),
                    "title": str(candidate["title"]),
                    "retrieval_source": str(candidate.get("retrieval_source", "")),
                    "status": "already_present",
                    "detail": str(error),
                }
            )
        except Exception as error:
            detail = str(error)[:300]
            print(f"Download failed for {candidate['paper_id']}: {detail}")
            download_failures += 1
            download_results.append(
                {
                    "paper_id": str(candidate["paper_id"]),
                    "title": str(candidate["title"]),
                    "retrieval_source": str(candidate.get("retrieval_source", "")),
                    "status": "failed",
                    "detail": detail,
                }
            )
            manual_queue.append(
                manual_retrieval_item(
                    candidate,
                    reason_override=f"Automatic download from {candidate.get('retrieval_source', 'public source')} failed: {detail}",
                )
            )
    write_json(
        queue_path,
        {
            "generated_at": datetime.now().astimezone().isoformat(),
            "queue_count": len(manual_queue),
            "items": manual_queue,
        },
    )
    write_json(
        report_path,
        {
            "generated_at": datetime.now().astimezone().isoformat(),
            "attempted_count": downloaded + download_failures,
            "downloaded_count": downloaded,
            "failed_count": download_failures,
            "results": download_results,
        },
    )
    print(f"Download report: {report_path}")
    print(f"Open-PDF download complete: downloaded={downloaded}, failed={download_failures}.")
    return 1 if download_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
