"""Unified command-line entry point for Psychology Evidence Agent."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import (
    agent_cli,
    environment_check,
    run_agent,
    run_batch_agent,
    run_check,
    run_config,
    run_evidence_synthesis,
    run_literature_review_report,
    run_literature_search,
    run_management,
    run_open_access_lookup,
    run_review_draft,
    run_web,
    schema_tools,
)

Command = Callable[[], int]
COMMANDS: dict[str, Command] = {
    "doctor": environment_check.main,
    "web": run_web.main,
    "search": run_literature_search.main,
    "evidence": run_agent.main,
    "synthesize": run_evidence_synthesis.main,
    "draft": run_review_draft.main,
    "access": run_open_access_lookup.main,
    "batch": run_batch_agent.main,
    "check": run_check.main,
    "config": run_config.main,
    "report": run_literature_review_report.main,
    "schemas": schema_tools.main,
    "run": run_management.main,
    "agent": agent_cli.main,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pea",
        description="Local, traceable psychology literature evidence workflow.",
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "args", nargs=argparse.REMAINDER, help="Arguments passed to the selected command"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch a stable ``pea`` subcommand to its existing command implementation."""
    parsed = parse_args(argv)
    original_argv = sys.argv
    try:
        sys.argv = [f"pea {parsed.command}", *parsed.args]
        return COMMANDS[parsed.command]()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
