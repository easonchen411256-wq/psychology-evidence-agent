"""Deterministic authorization checks for planner proposals."""

from __future__ import annotations

from ..domain.agent import (
    AgentGoal,
    AgentObservation,
    AgentPlan,
    PlanArgumentValue,
    PlanStepStatus,
    ToolArgumentType,
)
from ..domain.errors import AgentPlanRejectedError
from ..ports.agent_tools import AgentToolRegistry


class PlanPolicyGuard:
    """Reject plans that exceed the registered capability or current run state."""

    def validate(
        self,
        plan: AgentPlan,
        goal: AgentGoal,
        observation: AgentObservation,
        registry: AgentToolRegistry,
    ) -> None:
        if plan.goal_id != goal.goal_id or observation.goal_id != goal.goal_id:
            raise AgentPlanRejectedError("Plan and observation must belong to the active goal.")
        if not plan.steps:
            raise AgentPlanRejectedError("An agent plan must contain at least one step.")

        descriptors = {item.name: item for item in registry.descriptors()}
        step_ids = {step.step_id for step in plan.steps}
        if len(step_ids) != len(plan.steps):
            raise AgentPlanRejectedError("An agent plan cannot contain duplicate step IDs.")
        for step in plan.steps:
            descriptor = descriptors.get(step.tool_name)
            if descriptor is None:
                raise AgentPlanRejectedError(f"Tool '{step.tool_name}' is not registered.")
            argument_specs = {spec.name: spec for spec in descriptor.argument_specs}
            allowed_arguments = set(argument_specs) or set(descriptor.argument_names)
            unknown_arguments = set(step.arguments) - allowed_arguments
            if unknown_arguments:
                names = ", ".join(sorted(unknown_arguments))
                raise AgentPlanRejectedError(
                    f"Tool '{step.tool_name}' received unsupported argument(s): {names}."
                )
            if argument_specs:
                missing_arguments = {
                    name
                    for name, spec in argument_specs.items()
                    if spec.required and name not in step.arguments
                }
                if missing_arguments:
                    names = ", ".join(sorted(missing_arguments))
                    raise AgentPlanRejectedError(
                        f"Tool '{step.tool_name}' is missing required argument(s): {names}."
                    )
                for name, value in step.arguments.items():
                    if not _argument_matches(value, argument_specs[name].value_type):
                        expected = argument_specs[name].value_type.value
                        raise AgentPlanRejectedError(
                            f"Tool '{step.tool_name}' argument '{name}' must be {expected}."
                        )
            if step.max_attempts > observation.budget.max_attempts_per_step:
                raise AgentPlanRejectedError(
                    f"Step {step.step_id} exceeds the configured attempt budget."
                )
            if any(dependency not in step_ids for dependency in step.depends_on):
                raise AgentPlanRejectedError(f"Step {step.step_id} has an unknown dependency.")

        if _has_cycle(plan):
            raise AgentPlanRejectedError("An agent plan cannot contain dependency cycles.")
        current_steps = [
            step
            for step in plan.steps
            if step.status is PlanStepStatus.PENDING
            and descriptors[step.tool_name].stage is observation.current_stage
        ]
        if not current_steps:
            raise AgentPlanRejectedError(
                f"The plan has no pending tool for current stage {observation.current_stage.value}."
            )
        if any(step.requires_human_approval for step in plan.steps):
            raise AgentPlanRejectedError(
                "Explicit plan approval is not an executable tool; use the existing Human Gate."
            )
        if not any(
            not step.depends_on
            or all(
                any(
                    dependency.step_id == dependency_id
                    and dependency.status is PlanStepStatus.COMPLETED
                    for dependency in plan.steps
                )
                for dependency_id in step.depends_on
            )
            for step in current_steps
        ):
            raise AgentPlanRejectedError(
                f"The current stage {observation.current_stage.value} has no executable step."
            )


def _has_cycle(plan: AgentPlan) -> bool:
    dependencies = {step.step_id: set(step.depends_on) for step in plan.steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in visited:
            return False
        visiting.add(step_id)
        if any(visit(dependency) for dependency in dependencies[step_id]):
            return True
        visiting.remove(step_id)
        visited.add(step_id)
        return False

    return any(visit(step_id) for step_id in dependencies)


def _argument_matches(value: PlanArgumentValue, value_type: ToolArgumentType) -> bool:
    if value_type is ToolArgumentType.STRING:
        return isinstance(value, str)
    if value_type is ToolArgumentType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is ToolArgumentType.BOOLEAN:
        return isinstance(value, bool)
    if value_type is ToolArgumentType.STRING_LIST:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False
