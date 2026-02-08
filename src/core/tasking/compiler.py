"""
Plan Compiler — converts PlanArtifact or Plan → TaskGraph.

Maps plan steps to executable TaskSteps with types, inputs,
acceptance checks, and inferred dependencies.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.core.tasking.schema import StepType, TaskGraph, TaskStep

logger = logging.getLogger(__name__)

# Keywords that hint at step types
_FILE_KEYWORDS = {"create file", "edit file", "write file", "modify file", "delete file",
                  "update file", "add file", "patch"}
_COMMAND_KEYWORDS = {"run", "execute", "install", "build", "test", "deploy", "start",
                     "stop", "restart", "docker", "npm", "pip", "make", "git"}
_VERIFY_KEYWORDS = {"verify", "validate", "check", "confirm", "test", "assert"}
_HUMAN_KEYWORDS = {"ask user", "human input", "wait for", "approval", "permission",
                   "confirm with", "user decides"}


def _infer_step_type(description: str) -> str:
    """Infer StepType from a step description string."""
    desc_lower = description.lower()
    for kw in _HUMAN_KEYWORDS:
        if kw in desc_lower:
            return StepType.HUMAN_INPUT.value
    for kw in _FILE_KEYWORDS:
        if kw in desc_lower:
            return StepType.FILE_EDIT.value
    for kw in _VERIFY_KEYWORDS:
        if kw in desc_lower:
            return StepType.VERIFY.value
    for kw in _COMMAND_KEYWORDS:
        if kw in desc_lower:
            return StepType.COMMAND.value
    return StepType.TOOL_CALL.value


def _infer_risk_level(step_type: str, description: str) -> str:
    """Infer risk level from step type and description."""
    desc_lower = description.lower()
    if step_type == StepType.COMMAND.value:
        if any(w in desc_lower for w in ("deploy", "production", "delete", "rm ")):
            return "HIGH"
        return "MED"
    if step_type == StepType.FILE_EDIT.value:
        if any(w in desc_lower for w in ("delete", "remove", "config")):
            return "MED"
        return "LOW"
    if step_type == StepType.VERIFY.value:
        return "LOW"
    return "LOW"


class PlanCompiler:
    """Converts PlanArtifact or Plan → TaskGraph."""

    def compile_plan_artifact(self, artifact, session_id: str = "") -> TaskGraph:
        """Compile a PlanArtifact (from planning pipeline) into a TaskGraph.

        Args:
            artifact: PlanArtifact with .goal, .plan_steps, .done_when
            session_id: Session to associate the graph with.

        Returns:
            TaskGraph with inferred step types and sequential dependencies.
        """
        steps = []
        prev_id = None

        for i, step_desc in enumerate(artifact.plan_steps):
            step_type = _infer_step_type(step_desc)
            risk = _infer_risk_level(step_type, step_desc)
            step_id = f"step-{i+1}"

            step = TaskStep(
                step_id=step_id,
                type=step_type,
                inputs={"description": step_desc},
                expected_outputs={},
                acceptance_check=artifact.done_when[i] if i < len(artifact.done_when) else "",
                risk_level=risk,
                dependencies=[prev_id] if prev_id else [],
            )
            steps.append(step)
            prev_id = step_id

        return TaskGraph(
            goal=artifact.goal,
            planner_version="plan_artifact_v1",
            steps=steps,
            session_id=session_id,
        )

    def compile_agent_plan(self, plan, session_id: str = "") -> TaskGraph:
        """Compile an Agent Plan (from planner.py) into a TaskGraph.

        Args:
            plan: Plan with .goal, .steps (PlanStep with .id, .description,
                  .tool, .params, .dependencies)
            session_id: Session to associate the graph with.

        Returns:
            TaskGraph with step types based on PlanStep.tool.
        """
        # Map tool names to step types
        tool_type_map = {
            "read_file": StepType.FILE_EDIT.value,
            "write_file": StepType.FILE_EDIT.value,
            "edit_file": StepType.FILE_EDIT.value,
            "execute_command": StepType.COMMAND.value,
            "grep_search": StepType.TOOL_CALL.value,
            "verify": StepType.VERIFY.value,
        }

        steps = []
        id_map = {}  # old PlanStep.id → new step_id

        for ps in plan.steps:
            step_id = f"step-{ps.id}"
            id_map[ps.id] = step_id
            step_type = tool_type_map.get(ps.tool, StepType.TOOL_CALL.value)
            risk = _infer_risk_level(step_type, ps.description)

            deps = [id_map[d] for d in ps.dependencies if d in id_map]

            step = TaskStep(
                step_id=step_id,
                type=step_type,
                inputs={
                    "description": ps.description,
                    "tool": ps.tool,
                    "params": [{"name": p.name, "value": p.value} for p in ps.params]
                    if hasattr(ps, 'params') and ps.params else [],
                },
                expected_outputs={},
                acceptance_check="",
                risk_level=risk,
                dependencies=deps,
            )
            steps.append(step)

        return TaskGraph(
            goal=plan.goal,
            planner_version="agent_plan_v1",
            steps=steps,
            session_id=session_id,
        )
