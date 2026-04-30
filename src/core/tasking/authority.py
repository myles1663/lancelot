"""
Tasking authority helpers.

Resolves concrete tools, skills, and paths from generic task steps so
execution authority tokens are scoped to what will actually run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from src.core.tasking.schema import StepType, TaskStep


@dataclass(frozen=True)
class StepRequirementIssue:
    """A task step is missing data required by its resolved execution target."""

    step_id: str
    target: str
    missing_inputs: List[str]
    description: str = ""

    def format(self) -> str:
        missing = ", ".join(f"'{name}'" for name in self.missing_inputs)
        detail = f"Step {self.step_id} cannot run {self.target}: missing required input(s): {missing}"
        if self.description:
            detail += f". Step description: {self.description}"
        return detail


def resolve_step_authority(step: TaskStep) -> Dict[str, Optional[str]]:
    """Resolve the concrete execution target for a task step."""
    step_type = step.type
    inputs = step.inputs or {}

    tool = None
    skill = None
    path = inputs.get("path") or inputs.get("file")

    if step_type == StepType.SKILL_CALL.value:
        skill = inputs.get("skill_name") or inputs.get("skill")
    elif step_type == StepType.FILE_EDIT.value:
        tool = "repo_writer"
    elif step_type == StepType.COMMAND.value:
        tool = "command_runner"
    elif step_type == StepType.TOOL_CALL.value:
        tool = inputs.get("tool_name") or inputs.get("tool")

    return {
        "tool": tool,
        "skill": skill,
        "path": path,
    }


def _has_value(inputs: Dict[str, object], key: str) -> bool:
    value = inputs.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _params_contain(inputs: Dict[str, object], name: str) -> bool:
    for param in inputs.get("params", []) or []:
        if (
            isinstance(param, dict)
            and param.get("name") == name
            and param.get("value") not in (None, "")
        ):
            return True
    return False


def validate_step_requirements(step: TaskStep) -> List[StepRequirementIssue]:
    """Validate that a task step has the inputs required by its target.

    This catches incomplete executable plans before a governed connector or
    built-in skill is invoked.
    """
    inputs = step.inputs or {}
    authority = resolve_step_authority(step)
    tool = authority.get("tool")
    skill = authority.get("skill")
    target = skill or tool or step.type
    description = str(inputs.get("description", "")).strip()
    missing: List[str] = []

    if step.type == StepType.SKILL_CALL.value and not skill:
        missing.append("skill_name")

    if tool == "repo_writer" or skill == "repo_writer":
        if not _has_value(inputs, "action"):
            missing.append("action")
        if not _has_value(inputs, "path") and not _has_value(inputs, "file"):
            missing.append("path")

    if tool == "command_runner" or skill == "command_runner":
        if (
            not _has_value(inputs, "command")
            and not _params_contain(inputs, "command")
            and not description
        ):
            missing.append("command")

    if not missing:
        return []

    return [
        StepRequirementIssue(
            step_id=step.step_id,
            target=target or step.type,
            missing_inputs=missing,
            description=description,
        )
    ]


def validate_graph_requirements(steps: Iterable[TaskStep]) -> List[StepRequirementIssue]:
    """Validate every executable step in a task graph."""
    issues: List[StepRequirementIssue] = []
    for step in steps:
        issues.extend(validate_step_requirements(step))
    return issues


def format_step_requirement_issues(issues: Iterable[StepRequirementIssue]) -> str:
    """Return a compact, user-facing validation summary."""
    return "\n".join(f"- {issue.format()}" for issue in issues)


def list_graph_authorities(steps: list[TaskStep]) -> Dict[str, list[str]]:
    """Collect the concrete authorities needed to execute a task graph."""
    tools: list[str] = []
    skills: list[str] = []
    seen_tools = set()
    seen_skills = set()

    for step in steps:
        authority = resolve_step_authority(step)
        tool = authority.get("tool")
        skill = authority.get("skill")
        if tool and tool not in seen_tools:
            seen_tools.add(tool)
            tools.append(tool)
        if skill and skill not in seen_skills:
            seen_skills.add(skill)
            skills.append(skill)

    return {
        "tools": tools,
        "skills": skills,
    }
