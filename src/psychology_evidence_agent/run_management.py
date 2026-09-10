"""Minimal ResearchRun creation and inspection commands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .bootstrap import (
    artifact_store,
    evidence_agent,
    human_gate,
    research_run_lock,
    research_run_store,
)
from .domain.enums import ArtifactType, HumanDecisionType, RunStatus
from .domain.errors import (
    InvalidStateTransitionError,
    RunAlreadyExistsError,
    RunBusyError,
    RunNotFoundError,
    RunPersistenceError,
)
from .domain.run import HumanDecision, create_research_run
from .runtime.state_machine import RunStateMachine
from .services.workflow import WorkflowSettings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect persisted research-run state.")
    parser.add_argument(
        "--store-root",
        type=Path,
        default=Path("data"),
        help="Root directory containing the runs/ state directory.",
    )
    subcommands = parser.add_subparsers(dest="action", required=True)
    create = subcommands.add_parser("create", help="Create an initial ResearchRun.")
    create.add_argument("research_question")
    create.add_argument("--config-reference", default=None)
    show = subcommands.add_parser("show", help="Show a persisted ResearchRun.")
    show.add_argument("run_id")
    start = subcommands.add_parser("start", help="Start and run a persisted ResearchRun.")
    start.add_argument("run_id")
    _add_workflow_options(start)
    resume = subcommands.add_parser("resume", help="Resume a running ResearchRun.")
    resume.add_argument("run_id")
    resume.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL", ""))
    retry = subcommands.add_parser("retry", help="Retry a run with a retryable failure.")
    retry.add_argument("run_id")
    retry.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL", ""))
    cancel = subcommands.add_parser("cancel", help="Cancel a created, running, or waiting run.")
    cancel.add_argument("run_id")
    resolve = subcommands.add_parser("resolve", help="Resolve a pending human action.")
    resolve.add_argument("run_id")
    resolve.add_argument("action_id")
    resolve.add_argument(
        "--decision",
        required=True,
        choices=[item.value for item in HumanDecisionType],
    )
    resolve.add_argument("--fulltext", type=Path, default=None)
    resolve.add_argument("--note", default="")
    return parser.parse_args(argv)


def _add_workflow_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-per-query", type=int, default=15)
    parser.add_argument("--max-screen", type=int, default=30)
    parser.add_argument("--screen-all", action="store_true")
    parser.add_argument("--screen-batch-size", type=int, default=10)
    parser.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL", ""))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = research_run_store(args.store_root)
    if args.action == "create":
        run = create_research_run(args.research_question, config_reference=args.config_reference)
        try:
            store.create(run)
        except (RunAlreadyExistsError, RunPersistenceError) as error:
            print(f"Could not create research run: {error}")
            return 2
        print(f"Run ID: {run.run_id}")
        print(f"Status: {run.status.value}")
        print(f"Stage: {run.stage.value}")
        return 0

    if args.action == "show":
        try:
            run = store.load(args.run_id)
        except (RunNotFoundError, RunPersistenceError) as error:
            print(f"Could not load research run: {error}")
            return 2
        print(run.model_dump_json(indent=2))
        return 0

    if args.action in {"start", "resume", "retry"}:
        if args.action == "start":
            settings = WorkflowSettings(
                max_per_query=args.max_per_query,
                max_screen=args.max_screen,
                screen_all=args.screen_all,
                screen_batch_size=args.screen_batch_size,
                unpaywall_email=args.unpaywall_email,
            )
        else:
            settings = WorkflowSettings(unpaywall_email=args.unpaywall_email)
        try:
            with research_run_lock(args.store_root, args.run_id):
                run = store.load(args.run_id)
                if args.action == "retry":
                    RunStateMachine().retry(run)
                agent = evidence_agent(args.store_root, run.run_id, settings=settings)
                agent.run_until_blocked(run)
        except RunBusyError as error:
            print(f"Could not run research workflow: {error}")
            return 2
        except RunNotFoundError as error:
            print(f"Could not load research run: {error}")
            return 2
        except (OSError, ValueError, RunPersistenceError, RuntimeError) as error:
            print(f"Could not run research workflow: {error}")
            return 1
        print(f"Run ID: {run.run_id}")
        print(f"Status: {run.status.value}")
        print(f"Stage: {run.stage.value}")
        if run.failure is not None:
            print(f"Failure: {run.failure.error_code}")
        return 1 if run.status is RunStatus.FAILED else 0

    if args.action == "cancel":
        try:
            with research_run_lock(args.store_root, args.run_id):
                run = store.load(args.run_id)
                RunStateMachine().transition_status(run, RunStatus.CANCELLED)
                store.save(run)
        except RunBusyError as error:
            print(f"Could not cancel research run: {error}")
            return 2
        except RunNotFoundError as error:
            print(f"Could not load research run: {error}")
            return 2
        except (OSError, ValueError, RunPersistenceError, InvalidStateTransitionError) as error:
            print(f"Could not cancel research run: {error}")
            return 1
        print(f"Run ID: {run.run_id}")
        print(f"Status: {run.status.value}")
        print(f"Stage: {run.stage.value}")
        return 0

    if args.action == "resolve":
        if args.decision == HumanDecisionType.PROVIDE_FULLTEXT.value and args.fulltext is None:
            print("--fulltext is required for provide_fulltext decisions.")
            return 2
        if args.decision != HumanDecisionType.PROVIDE_FULLTEXT.value and args.fulltext is not None:
            print("--fulltext is only valid with provide_fulltext.")
            return 2
        try:
            with research_run_lock(args.store_root, args.run_id):
                run = store.load(args.run_id)
                provided_reference = None
                if args.fulltext is not None:
                    source = args.fulltext.expanduser()
                    if not source.is_file() or source.suffix.lower() not in {".pdf", ".md", ".txt"}:
                        print("--fulltext must point to an existing .pdf, .md, or .txt file.")
                        return 2
                    if source.stat().st_size > 50 * 1024 * 1024:
                        print("--fulltext exceeds the 50 MB safety limit.")
                        return 2
                    run_artifacts = artifact_store(
                        args.store_root / "runs" / run.run_id / "artifacts"
                    )
                    logical_key = f"documents/manual_{args.action_id}{source.suffix.lower()}"
                    run_artifacts.save_bytes(logical_key, source.read_bytes())
                    provided_reference = run_artifacts.reference(
                        logical_key,
                        ArtifactType.FULLTEXT_DOCUMENT,
                        metadata={"source": "human_provided"},
                    )
                decision = HumanDecision(
                    action_id=args.action_id,
                    decision_type=HumanDecisionType(args.decision),
                    note=args.note,
                    provided_artifact_reference=provided_reference,
                )
                resolved = human_gate(args.store_root).resolve(run, decision)
        except RunBusyError as error:
            print(f"Could not resolve human action: {error}")
            return 2
        except (OSError, ValueError, RunPersistenceError, RuntimeError) as error:
            print(f"Could not resolve human action: {error}")
            return 2
        print(f"Resolved action: {resolved.action_id}")
        print(f"Status: {run.status.value}")
        print(f"Stage: {run.stage.value}")
        return 0
    raise AssertionError(f"Unhandled run action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
