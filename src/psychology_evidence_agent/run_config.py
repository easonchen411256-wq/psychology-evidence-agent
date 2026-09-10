"""Create editable configuration files from packaged defaults."""

from __future__ import annotations

import argparse
from pathlib import Path

from .resources import copy_default_config
from .run_literature_search import DEFAULT_CONFIG_NAME


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage Psychology Evidence Agent configuration files."
    )
    subcommands = parser.add_subparsers(dest="action", required=True)
    init_parser = subcommands.add_parser("init", help="Create an editable search configuration.")
    init_parser.add_argument(
        "--output",
        type=Path,
        default=Path("psychology-evidence-agent.json"),
        help="Destination JSON file; an existing file is never overwritten.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action != "init":
        return 2
    try:
        output_path = copy_default_config(DEFAULT_CONFIG_NAME, args.output)
    except (FileExistsError, OSError) as error:
        print(f"Could not create configuration: {error}")
        return 2
    print(f"Created editable configuration: {output_path}")
    return 0
