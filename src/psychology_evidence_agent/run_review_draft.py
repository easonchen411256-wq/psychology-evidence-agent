"""Create a cautious review-draft Markdown file from evidence-card synthesis."""

from __future__ import annotations

import argparse
from pathlib import Path

from .batch_evidence_cards import default_research_question
from .bootstrap import artifact_store, review_draft_service
from .domain.errors import StructuredOutputError
from .review_draft import render_review_markdown
from .services.review_draft import load_synthesis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a traceable Chinese review draft from evidence-card synthesis."
    )
    parser.add_argument(
        "--synthesis",
        type=Path,
        default=Path("data/processed/evidence_synthesis/evidence_synthesis.json"),
    )
    parser.add_argument("--research-question", default=default_research_question())
    parser.add_argument("--output", type=Path, default=Path("data/processed/review_draft.md"))
    parser.add_argument(
        "--overwrite", action="store_true", help="Allow replacing an existing draft"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists() and not args.overwrite:
        print(f"Output already exists: {args.output}. Add --overwrite to replace it.")
        return 2
    try:
        synthesis = load_synthesis(args.synthesis)
        draft = review_draft_service().generate(args.research_question, synthesis)
    except (OSError, ValueError, StructuredOutputError) as error:
        print(f"Review draft was not generated: {error}")
        return 1
    artifact_store(args.output.parent).save_text(args.output.name, render_review_markdown(draft))
    print(f"Review draft written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
