"""CLI for the bounded plan/observe/execute/re-plan research agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .adapters.persistence.artifact_store import FileSystemArtifactStore
from .adapters.persistence.event_store import FileSystemAgentEventStore
from .bootstrap import agent_controller, research_run_lock, research_run_store
from .domain.agent import AgentGoal, ExecutionBudget, ResearchBrief
from .domain.enums import RunStatus
from .domain.errors import RunBusyError, RunNotFoundError, RunPersistenceError
from .domain.run import ResearchRun, create_research_run
from .runtime.agent_controller import (
    BUDGET_ARTIFACT,
    GOAL_ARTIFACT,
    PLAN_ARTIFACT,
)
from .runtime.state_machine import RunStateMachine
from .services.workflow import WorkflowSettings

AGENT_OPTIONS_ARTIFACT = "agent/options.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bounded Psychology Evidence Agent.")
    subcommands = parser.add_subparsers(dest="action", required=True)

    run = subcommands.add_parser("run", help="Create and execute a new agent run.")
    run.add_argument("objective")
    _add_store_root(run)
    _add_workflow_options(run)
    _add_budget_options(run)
    _add_research_brief_options(run)
    run.add_argument("--config-reference", type=Path, default=None)
    run.add_argument(
        "--deterministic-planner",
        action="store_true",
        help="Use the local planner instead of Codex for planning-layer smoke tests.",
    )

    for name, help_text in (
        ("resume", "Resume a run after a resolved Human Gate."),
        ("retry", "Retry a run with a retryable structured failure."),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("run_id")
        _add_store_root(command)
        command.add_argument(
            "--deterministic-planner",
            action="store_true",
            help="Use the local safe planner for this continuation.",
        )

    for name, help_text in (
        ("status", "Show agent status, budget, and plan summary."),
        ("plan", "Show the persisted agent plan."),
        ("events", "Show the append-only agent event trace."),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("run_id")
        _add_store_root(command)
    return parser.parse_args(argv)


def _add_store_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store-root", type=Path, default=Path("data"))


def _add_workflow_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-per-query", type=int, default=15)
    parser.add_argument("--max-screen", type=int, default=30)
    parser.add_argument("--screen-all", action="store_true")
    parser.add_argument("--screen-batch-size", type=int, default=10)
    parser.add_argument("--unpaywall-email", default="")


def _add_budget_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--max-replans", type=int, default=10)
    parser.add_argument("--max-model-calls", type=int, default=20)
    parser.add_argument("--max-attempts-per-step", type=int, default=2)


def _add_research_brief_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--research-question", default=None)
    parser.add_argument("--population", default="")
    parser.add_argument("--intervention-or-exposure", default="")
    parser.add_argument("--comparison", default="")
    parser.add_argument("--outcome", action="append", default=[])
    parser.add_argument("--include", dest="inclusion_criteria", action="append", default=[])
    parser.add_argument("--exclude", dest="exclusion_criteria", action="append", default=[])
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--study-type", action="append", default=[])
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action == "run":
            return _create_and_run(args)
        if args.action in {"resume", "retry"}:
            return _continue_run(args)
        if args.action == "status":
            return _show_status(args)
        if args.action == "plan":
            return _show_artifact(args, PLAN_ARTIFACT, "No persisted agent plan.")
        if args.action == "events":
            return _show_events(args)
    except RunBusyError as error:
        print(f"Could not modify research run: {error}")
        return 2
    except RunNotFoundError as error:
        print(f"Could not load research run: {error}")
        return 2
    except (OSError, ValueError, RunPersistenceError, RuntimeError) as error:
        print(f"Agent command failed: {error}")
        return 1
    raise AssertionError(f"Unhandled agent action: {args.action}")


def _create_and_run(args: argparse.Namespace) -> int:
    root = args.store_root
    store = research_run_store(root)
    config_reference = str(args.config_reference) if args.config_reference is not None else None
    research_question = args.research_question or args.objective
    run = create_research_run(research_question, config_reference=config_reference)
    store.create(run)
    brief = ResearchBrief(
        research_question=research_question,
        population=args.population,
        intervention_or_exposure=args.intervention_or_exposure,
        comparison=args.comparison,
        outcomes=args.outcome,
        inclusion_criteria=args.inclusion_criteria,
        exclusion_criteria=args.exclusion_criteria,
        languages=args.language,
        study_types=args.study_type,
        year_from=args.year_from,
        year_to=args.year_to,
    )
    goal = AgentGoal(
        objective=args.objective,
        research_question=research_question,
        brief=brief,
    )
    artifact_store = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
    artifact_store.save_json(
        AGENT_OPTIONS_ARTIFACT,
        {"deterministic_planner": bool(args.deterministic_planner)},
    )
    settings = WorkflowSettings(
        max_per_query=args.max_per_query,
        max_screen=args.max_screen,
        screen_all=args.screen_all,
        screen_batch_size=args.screen_batch_size,
        unpaywall_email=args.unpaywall_email,
    )
    budget = ExecutionBudget(
        max_steps=args.max_steps,
        max_replans=args.max_replans,
        max_model_calls=args.max_model_calls,
        max_attempts_per_step=args.max_attempts_per_step,
    )
    with research_run_lock(root, run.run_id):
        controller = agent_controller(
            root,
            run.run_id,
            settings=settings,
            budget=budget,
            deterministic_planner=args.deterministic_planner,
        )
        controller.run_until_blocked(run, goal)
    return _print_run_result(run)


def _continue_run(args: argparse.Namespace) -> int:
    root = args.store_root
    store = research_run_store(root)
    with research_run_lock(root, args.run_id):
        run = store.load(args.run_id)
        artifact_store = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
        goal = _load_required(artifact_store, GOAL_ARTIFACT, AgentGoal)
        options = _load_options(artifact_store)
        deterministic = bool(args.deterministic_planner or options.get("deterministic_planner"))
        if args.action == "retry":
            RunStateMachine().retry(run)
        controller = agent_controller(
            root,
            run.run_id,
            deterministic_planner=deterministic,
        )
        controller.run_until_blocked(run, goal)
    return _print_run_result(run)


def _show_status(args: argparse.Namespace) -> int:
    run = research_run_store(args.store_root).load(args.run_id)
    artifact_store = FileSystemArtifactStore(args.store_root / "runs" / run.run_id / "artifacts")
    budget = _load_optional(artifact_store, BUDGET_ARTIFACT, ExecutionBudget)
    status: dict[str, Any] = {
        "run": run.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json") if budget is not None else None,
        "plan": _load_optional(artifact_store, PLAN_ARTIFACT),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def _show_artifact(args: argparse.Namespace, name: str, missing_message: str) -> int:
    research_run_store(args.store_root).load(args.run_id)
    artifact_store = FileSystemArtifactStore(args.store_root / "runs" / args.run_id / "artifacts")
    payload = _load_optional(artifact_store, name)
    if payload is None:
        print(missing_message)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _show_events(args: argparse.Namespace) -> int:
    research_run_store(args.store_root).load(args.run_id)
    path = args.store_root / "runs" / args.run_id / "events.jsonl"
    for event in FileSystemAgentEventStore(path).read():
        print(event.model_dump_json())
    return 0


def _load_required(artifact_store: FileSystemArtifactStore, name: str, model: type[Any]) -> Any:
    payload = _load_optional(artifact_store, name, model)
    if payload is None:
        raise ValueError(f"Required agent artifact is missing or invalid: {name}")
    return payload


def _load_optional(
    artifact_store: FileSystemArtifactStore, name: str, model: type[Any] | None = None
) -> Any:
    try:
        payload = artifact_store.load_json(name)
    except (FileNotFoundError, OSError, ValueError):
        return None
    if model is None:
        return payload
    try:
        return model.model_validate(payload)
    except (TypeError, ValueError):
        return None


def _load_options(artifact_store: FileSystemArtifactStore) -> dict[str, Any]:
    payload = _load_optional(artifact_store, AGENT_OPTIONS_ARTIFACT)
    return payload if isinstance(payload, dict) else {}


def _print_run_result(run: ResearchRun) -> int:
    print(f"Run ID: {run.run_id}")
    print(f"Status: {run.status.value}")
    print(f"Stage: {run.stage.value}")
    pending = [action.action_id for action in run.human_actions if action.decision is None]
    if pending:
        print(f"Pending human action(s): {', '.join(pending)}")
    if run.failure is not None:
        print(f"Failure: {run.failure.error_code}")
    return 1 if run.status is RunStatus.FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
