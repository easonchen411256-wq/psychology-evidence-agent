"""Command-line entry point for local multi-paper evidence-card synthesis."""

from __future__ import annotations

import argparse
from pathlib import Path

from .bootstrap import artifact_store
from .evidence_synthesis import build_synthesis, write_synthesis_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a local comparison matrix from evidence cards."
    )
    parser.add_argument(
        "--input", type=Path, nargs="*", help="One or more .evidence_card.json files"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/processed"),
        help="Directory scanned when --input is omitted",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/evidence_synthesis"),
        help="Directory for synthesis outputs",
    )
    parser.add_argument(
        "--include-sample",
        action="store_true",
        help="Include sample_*.evidence_card.json files when scanning",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Allow replacing existing synthesis output files"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = (
        args.input
        if args.input is not None
        else [
            path
            for path in sorted(args.input_dir.glob("*.evidence_card.json"))
            if args.include_sample or not path.name.startswith("sample_")
        ]
    )
    if not paths:
        print("No evidence-card files found. First generate at least one .evidence_card.json file.")
        return 2
    targets = [
        args.output_dir / name
        for name in (
            "evidence_synthesis.json",
            "evidence_matrix.csv",
            "evidence_matrix.md",
            "review_queue.md",
        )
    ]
    existing = [path for path in targets if path.exists()]
    if existing and not args.overwrite:
        print(
            "Output already exists: "
            + ", ".join(str(path) for path in existing)
            + ". Add --overwrite to replace it."
        )
        return 2
    synthesis = build_synthesis(paths)
    outputs = write_synthesis_outputs(synthesis, args.output_dir, artifact_store(args.output_dir))
    summary = synthesis.summary
    print(
        f"Included {summary.included_count} of {summary.input_count} evidence cards; skipped {summary.skipped_count}."
    )
    role_text = (
        ", ".join(f"{name}={count}" for name, count in summary.role_counts.items()) or "none"
    )
    print("Candidate roles: " + role_text)
    print("Written: " + ", ".join(str(path) for path in outputs.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
