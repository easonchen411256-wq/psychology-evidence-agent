"""Create a readable Word report for manual review of screened literature."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from .bootstrap import artifact_store
from .literature_review_report import build_review_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Word manual-review report from literature screening results."
    )
    parser.add_argument("--search-dir", type=Path, default=Path("data/processed/literature_search"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--all-screened",
        action="store_true",
        help="Include every screened paper instead of only the priority reading list.",
    )
    parser.add_argument(
        "--abstract-limit",
        type=int,
        default=1200,
        help="Maximum abstract characters per paper; use 0 for full abstracts.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing report file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.abstract_limit < 0:
        print("--abstract-limit must be 0 or greater.")
        return 2
    output_path = args.output or args.search_dir / "literature_manual_review_report.docx"
    if output_path.exists() and not args.overwrite:
        print(f"Output already exists: {output_path}. Add --overwrite to replace it.")
        return 2
    try:
        document = BytesIO()
        record_count, counts = build_review_report(
            args.search_dir,
            document,
            all_screened=args.all_screened,
            abstract_limit=args.abstract_limit,
        )
        artifact_store(output_path.parent).save_bytes(output_path.name, document.getvalue())
    except (OSError, ValueError, KeyError) as error:
        print(f"Could not create manual-review report: {error}")
        return 2
    print(f"Created Word manual-review report for {record_count} papers: {output_path}")
    print(
        f"Candidates={counts['candidate_count']}, screened={counts['screened_count']}, open_pdf={counts['open_pdf_count']}, manual_queue={counts['manual_queue_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
