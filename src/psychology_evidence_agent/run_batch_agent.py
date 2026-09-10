"""Generate one validated evidence card per local paper text, sequentially."""

from __future__ import annotations

import argparse
from pathlib import Path

from .batch_evidence_cards import (
    default_research_question,
    discover_paper_texts,
    evidence_card_path,
)
from .bootstrap import artifact_store, document_reader, evidence_extraction_service
from .domain.errors import StructuredOutputError
from .paper_input import PaperInputError

MAX_INPUT_CHARS = 160_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-generate validated evidence cards from local text files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory containing .md, .txt, or searchable .pdf paper files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed"),
        help="Directory for generated evidence cards",
    )
    parser.add_argument(
        "--research-question",
        default=default_research_question(),
        help="Question framing every extraction",
    )
    parser.add_argument(
        "--max-files", type=int, default=5, help="Maximum new files to process; default: 5"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing same-name evidence card",
    )
    parser.add_argument(
        "--allow-large-input",
        action="store_true",
        help="Allow inputs longer than 160,000 characters",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List planned actions without calling Codex CLI"
    )
    return parser.parse_args()


def _read_paper(path: Path, allow_large_input: bool) -> tuple[str | None, str | None]:
    try:
        return document_reader().read_text(
            path, max_chars=MAX_INPUT_CHARS, allow_large_input=allow_large_input
        ), None
    except PaperInputError as error:
        return None, str(error)


def main() -> int:
    args = parse_args()
    if args.max_files < 1:
        print("--max-files must be at least 1.")
        return 2

    files = discover_paper_texts(args.input_dir)
    if not files:
        print(f"No UTF-8 .md or .txt paper texts found in: {args.input_dir}")
        return 2

    eligible: list[tuple[Path, Path]] = []
    skipped = 0
    for path in files:
        output_path = evidence_card_path(path, args.output_dir, args.input_dir)
        if output_path.exists() and not args.overwrite:
            print(f"SKIP existing card: {path} -> {output_path}")
            skipped += 1
        else:
            eligible.append((path, output_path))

    selected = eligible[: args.max_files]
    if args.dry_run:
        for path, output_path in selected:
            print(f"PLAN: {path} -> {output_path}")
        print(
            f"Planned {len(selected)} new cards; {skipped} existing cards skipped; {len(eligible) - len(selected)} deferred by --max-files."
        )
        return 0

    completed = 0
    failed = 0
    for path, output_path in selected:
        paper_text, issue = _read_paper(path, args.allow_large_input)
        if issue:
            print(f"FAIL {path}: {issue}")
            failed += 1
            continue
        try:
            card = evidence_extraction_service().extract(
                paper_text=paper_text or "",
                research_question=args.research_question,
            )
        except StructuredOutputError as error:
            print(f"FAIL {path}: {error}")
            failed += 1
            continue
        artifact_store(output_path.parent).save_text(
            output_path.name, card.model_dump_json(indent=2)
        )
        print(f"DONE {path} -> {output_path}")
        completed += 1

    deferred = len(eligible) - len(selected)
    print(
        f"Batch complete: completed={completed}, skipped={skipped}, failed={failed}, deferred={deferred}."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
