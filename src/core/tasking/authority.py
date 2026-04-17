"""
Tasking authority helpers.

Resolves concrete tools, skills, and paths from generic task steps so
execution authority tokens are scoped to what will actually run.
"""

from __future__ import annotations

from typing import Dict, Optional

from src.core.tasking.schema import StepType, TaskStep


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
