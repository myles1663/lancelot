"""
Response Assembler — produces clean chat output + War Room artifacts.

The assembler is the final stage before returning a response to the user.
It separates verbose scaffolding (assumptions, risks, decision points, traces)
into War Room artifacts and returns only clean, concise chat output.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.response.policies import OutputPolicy
from src.core.response.war_room_artifact import ArtifactType, WarRoomArtifact

logger = logging.getLogger(__name__)


@dataclass
class AssembledResponse:
    """The final assembled response with chat + War Room split."""
    chat_response: str = ""
    war_room_artifacts: List[WarRoomArtifact] = field(default_factory=list)
    ui_events: List[Dict[str, Any]] = field(default_factory=list)


class ResponseAssembler:
    """Produces clean chat_response + war_room_artifacts from raw pipeline output.

    Enforces:
    - Chat is concise: goal + plan summary + status + next actions
    - Verbose content routed to War Room artifacts
    - Permission prompts formatted consistently
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id

    def assemble(
        self,
        raw_planner_output: Optional[str] = None,
        plan_artifact=None,
        task_graph=None,
        task_run=None,
        receipts: Optional[List[Any]] = None,
        honesty_status: Optional[str] = None,
    ) -> AssembledResponse:
        """Assemble a response from pipeline outputs.

        Args:
            raw_planner_output: Raw markdown from planning pipeline
            plan_artifact: PlanArtifact dataclass (optional)
            task_graph: TaskGraph dataclass (optional)
            task_run: TaskRun dataclass (optional)
            receipts: List of Receipt objects (optional)
            honesty_status: Honesty gate status string (optional)

        Returns:
            AssembledResponse with clean chat and War Room artifacts.
        """
        artifacts: List[WarRoomArtifact] = []

        # If we have a PlanArtifact, assemble from structured data
        if plan_artifact is not None:
            chat, arts = self._assemble_from_artifact(plan_artifact)
            artifacts.extend(arts)
        elif raw_planner_output:
            chat, arts = self._assemble_from_markdown(raw_planner_output)
            artifacts.extend(arts)
        else:
            chat = ""

        # If we have a task_run, add status
        if task_run is not None:
            status_line = self._format_task_run_status(task_run)
            chat = chat + "\n\n" + status_line if chat else status_line

            # Add task run timeline to War Room
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.TASK_RUN_TIMELINE.value,
                content={"task_run_id": task_run.id, "status": task_run.status,
                         "receipts": task_run.receipts_index if hasattr(task_run, 'receipts_index') else []},
                session_id=self.session_id,
            ))

        # If we have a task_graph, add to War Room
        if task_graph is not None:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.TASK_GRAPH.value,
                content={"graph_id": task_graph.id, "goal": task_graph.goal,
                         "steps": len(task_graph.steps)},
                session_id=self.session_id,
            ))

        # If we have receipts, add tool traces
        if receipts:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.TOOL_TRACE.value,
                content={"receipt_count": len(receipts),
                         "receipt_ids": [r.id for r in receipts]},
                session_id=self.session_id,
            ))

        # Enforce chat limits
        chat = OutputPolicy.enforce_chat_limits(chat)

        return AssembledResponse(
            chat_response=chat.strip(),
            war_room_artifacts=artifacts,
        )

    def assemble_permission_request(
        self,
        what_i_will_do: list,
        tools_enabled: set,
        risk_tier: str,
        limits: Dict[str, Any],
    ) -> str:
        """Format a permission request for the user.

        Args:
            what_i_will_do: List of TaskStep or description strings
            tools_enabled: Set of tool/step type names
            risk_tier: "LOW", "MED", or "HIGH"
            limits: Dict with "duration" and "actions" keys

        Returns:
            Formatted permission request string.
        """
        lines = ["**Permission required:**\n"]
        lines.append("**What I will do:**")
        for item in what_i_will_do[:OutputPolicy.MAX_NEXT_ACTIONS]:
            if hasattr(item, 'inputs'):
                desc = item.inputs.get("description", str(item.type))
            elif isinstance(item, str):
                desc = item
            else:
                desc = str(item)
            lines.append(f"- {desc}")

        lines.append(f"\n**What will be enabled:** {', '.join(sorted(tools_enabled))}")
        lines.append(f"**Risk tier:** {risk_tier}")
        lines.append(f"**Limits:** {limits.get('duration', 300)}s / {limits.get('actions', 50)} actions")
        lines.append("\nApprove or Deny?")

        return "\n".join(lines)

    def _assemble_from_artifact(self, artifact) -> tuple:
        """Build chat + artifacts from a PlanArtifact."""
        artifacts = []

        # Chat: Goal + Plan Summary + Next Action
        chat_lines = [f"**Goal:** {artifact.goal}\n"]
        chat_lines.append("**Plan:**")
        plan_steps = OutputPolicy.trim_plan_summary(artifact.plan_steps)
        for i, step in enumerate(plan_steps, 1):
            if step.startswith("  *("):
                chat_lines.append(step)
            else:
                chat_lines.append(f"{i}. {step}")

        chat_lines.append(f"\n**Status:** PLANNED")
        chat_lines.append(f"\n**Next action:**\n- {artifact.next_action}")

        # War Room: Full artifact + verbose sections
        artifacts.append(WarRoomArtifact(
            type=ArtifactType.PLAN_ARTIFACT_FULL.value,
            content={
                "goal": artifact.goal,
                "context": artifact.context,
                "plan_steps": artifact.plan_steps,
                "next_action": artifact.next_action,
            },
            session_id=self.session_id,
        ))

        if artifact.assumptions:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.ASSUMPTIONS.value,
                content={"assumptions": artifact.assumptions},
                session_id=self.session_id,
            ))

        if artifact.decision_points:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.DECISION_POINTS.value,
                content={"decision_points": artifact.decision_points},
                session_id=self.session_id,
            ))

        if artifact.risks:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.RISKS.value,
                content={"risks": [{"risk": r.risk, "mitigation": r.mitigation}
                                   for r in artifact.risks]},
                session_id=self.session_id,
            ))

        return "\n".join(chat_lines), artifacts

    def _assemble_from_markdown(self, markdown: str) -> tuple:
        """Split raw markdown into chat + War Room artifacts."""
        chat_md, verbose_md = OutputPolicy.extract_verbose_sections(markdown)
        artifacts = []

        if verbose_md:
            artifacts.append(WarRoomArtifact(
                type=ArtifactType.PLAN_ARTIFACT_FULL.value,
                content={"verbose_markdown": verbose_md},
                session_id=self.session_id,
            ))

        return chat_md, artifacts

    def _format_task_run_status(self, task_run) -> str:
        """Format a TaskRun status line for chat."""
        status = task_run.status
        if hasattr(status, 'value'):
            status = status.value

        line = f"**Status:** `{status}`"
        if hasattr(task_run, 'current_step_id') and task_run.current_step_id:
            line += f" (step: {task_run.current_step_id})"
        if hasattr(task_run, 'last_error') and task_run.last_error:
            line += f"\n**Error:** {task_run.last_error}"
        return line
