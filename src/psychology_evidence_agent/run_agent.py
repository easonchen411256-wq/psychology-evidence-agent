"""Generate a validated psychology evidence card from a UTF-8 text or Markdown file."""

from __future__ import annotations

import argparse
from pathlib import Path

from .bootstrap import artifact_store, document_reader, evidence_extraction_service
from .domain.errors import StructuredOutputError
from .paper_input import PaperInputError

MAX_INPUT_CHARS = 160_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a validated psychology evidence card.")
    parser.add_argument(
        "input_path", type=Path, help="UTF-8 .txt/.md paper text, or a searchable local PDF"
    )
    parser.add_argument(
        "--research-question", default="", help="Question used to frame the extraction"
    )
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path")
    parser.add_argument(
        "--overwrite", action="store_true", help="Allow replacing an existing output file"
    )
    parser.add_argument(
        "--allow-large-input",
        action="store_true",
        help="Allow input longer than 160,000 characters",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        paper_text = document_reader().read_text(
            args.input_path,
            max_chars=MAX_INPUT_CHARS,
            allow_large_input=args.allow_large_input,
        )
    except PaperInputError as error:
        print(f"无法读取输入材料：{error}")
        return 2

    output_path = (
        args.output or Path("data/processed") / f"{args.input_path.stem}.evidence_card.json"
    )
    if output_path.exists() and not args.overwrite:
        print(f"输出文件已存在：{output_path}。若确认替换，请加 --overwrite。")
        return 2

    try:
        card = evidence_extraction_service().extract(
            paper_text=paper_text,
            research_question=args.research_question,
        )
    except StructuredOutputError as error:
        print(f"未生成证据卡：{error}")
        return 1

    artifact_store(output_path.parent).save_text(output_path.name, card.model_dump_json(indent=2))
    print(f"证据卡已写入：{output_path}")
    print("推理引擎：codex_cli；沙箱：read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
