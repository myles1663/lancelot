"""
HIVE Task Decomposer — LLM-powered task decomposition.

Uses ModelRouter (flagship_deep lane) to decompose a high-level goal
into a structured set of subtasks with dependency ordering.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.hive.types import (
    ControlMethod,
    DecomposedTask,
    TaskPriority,
    TaskSpec,
)
from src.hive.errors import TaskDecompositionError

logger = logging.getLogger(__name__)

# Maximum subtasks per decomposition
MAX_SUBTASKS = 20

# Prompt template for task decomposition
_DECOMPOSITION_PROMPT = """\
You are a task decomposer for an autonomous agent system (HIVE).
Given a high-level goal, break it into concrete subtasks that can be
executed independently or in dependency order.

## Goal
{goal}

## Available Context
{context}

## Available UAB Applications
{available_apps}

## Constraints
- Maximum {max_subtasks} subtasks
- Each subtask must have: description, priority (critical/high/normal/low), control_method (fully_autonomous/supervised/manual_confirm)
- Group subtasks into execution_order groups (subtasks in same group run in parallel)
- Higher-risk actions should use supervised or manual_confirm control
- Read-only operations can be fully_autonomous

## Output Format
Return valid JSON with this structure:
{{
  "subtasks": [
    {{
      "description": "...",
      "priority": "normal",
      "control_method": "supervised",
      "execution_group": 0,
      "allowed_categories": ["read", "query"],
      "timeout_seconds": 300
    }}
  ],
  "execution_order": [[0], [1, 2], [3]],
  "rationale": "..."
}}

Return ONLY the JSON, no markdown fences or explanation.
"""


class TaskDecomposer:
    """Decomposes high-level goals into structured subtask plans.

    Uses the ModelRouter's flagship_deep lane for reasoning about
    task decomposition and dependency ordering.
    """

    def __init__(
        self,
        model_router=None,
        uab_bridge=None,
        max_subtasks: int = MAX_SUBTASKS,
    ):
        self._router = model_router
        self._uab_bridge = uab_bridge
        self._max_subtasks = max_subtasks

    async def decompose(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        quest_id: Optional[str] = None,
    ) -> DecomposedTask:
        """Decompose a goal into subtasks.

        Args:
            goal: High-level goal description.
            context: Additional context for decomposition.
            quest_id: Quest ID for receipt tracing.

        Returns:
            DecomposedTask with subtasks and execution order.

        Raises:
            TaskDecompositionError: If decomposition fails.
        """
        if not goal or not goal.strip():
            raise TaskDecompositionError("Goal cannot be empty")

        quest_id = quest_id or str(uuid.uuid4())

        # Discover available UAB apps
        available_apps = await self._get_available_apps()

        # Build the prompt
        prompt = _DECOMPOSITION_PROMPT.format(
            goal=goal,
            context=json.dumps(context or {}, indent=2),
            available_apps=json.dumps(available_apps, indent=2) if available_apps else "None available",
            max_subtasks=self._max_subtasks,
        )

        # Route through ModelRouter
        raw_output = await self._call_llm(prompt)

        # Parse the LLM response
        parsed = self._parse_response(raw_output)

        # Build subtasks
        subtasks = self._build_subtasks(parsed, quest_id)

        # Build execution order, filtering out indices beyond subtask count
        raw_order = parsed.get("execution_order", [[i] for i in range(len(subtasks))])
        max_idx = len(subtasks)
        execution_order = []
        for group in raw_order:
            filtered = [idx for idx in group if idx < max_idx]
            if filtered:
                execution_order.append(filtered)

        # Validate
        self._validate(subtasks, execution_order)

        # Convert execution_order indices to strings (DecomposedTask uses List[List[str]])
        str_execution_order = [
            [str(idx) for idx in group] for group in execution_order
        ]

        return DecomposedTask(
            quest_id=quest_id,
            goal=goal,
            subtasks=subtasks,
            execution_order=str_execution_order,
            context={
                "original_context": context or {},
                "rationale": parsed.get("rationale", ""),
                "available_apps": available_apps,
            },
        )

    async def _get_available_apps(self) -> List[Dict]:
        """Discover available UAB applications."""
        if self._uab_bridge is None:
            return []
        try:
            return await self._uab_bridge.get_available_apps()
        except Exception as exc:
            logger.warning("Failed to discover UAB apps: %s", exc)
            return []

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM via ModelRouter.

        Uses the flagship_deep lane for complex reasoning.
        """
        if self._router is None:
            raise TaskDecompositionError("No ModelRouter configured")

        try:
            result = self._router.route(
                task_type="plan",
                text=prompt,
            )
            if result.output is None:
                raise TaskDecompositionError("LLM returned no output")
            return result.output
        except TaskDecompositionError:
            raise
        except Exception as exc:
            raise TaskDecompositionError(f"LLM call failed: {exc}") from exc

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """Parse the LLM's JSON response."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TaskDecompositionError(
                f"Failed to parse LLM response as JSON: {exc}"
            ) from exc

        if "subtasks" not in data:
            raise TaskDecompositionError("LLM response missing 'subtasks' field")

        return data

    def _build_subtasks(
        self,
        parsed: Dict[str, Any],
        quest_id: str,
    ) -> List[TaskSpec]:
        """Build TaskSpec objects from parsed LLM response."""
        subtasks = []
        for i, raw_task in enumerate(parsed["subtasks"]):
            if i >= self._max_subtasks:
                logger.warning(
                    "Truncating subtasks at %d (max=%d)",
                    i, self._max_subtasks,
                )
                break

            # Map priority string to enum
            priority_str = raw_task.get("priority", "normal").lower()
            priority_map = {
                "critical": TaskPriority.CRITICAL,
                "high": TaskPriority.HIGH,
                "normal": TaskPriority.NORMAL,
                "low": TaskPriority.LOW,
            }
            priority = priority_map.get(priority_str, TaskPriority.NORMAL)

            # Map control method string to enum
            control_str = raw_task.get("control_method", "supervised").lower()
            control_map = {
                "fully_autonomous": ControlMethod.FULLY_AUTONOMOUS,
                "supervised": ControlMethod.SUPERVISED,
                "manual_confirm": ControlMethod.MANUAL_CONFIRM,
            }
            control = control_map.get(control_str, ControlMethod.SUPERVISED)

            spec = TaskSpec(
                description=raw_task.get("description", f"Subtask {i}"),
                priority=priority,
                control_method=control,
                timeout_seconds=raw_task.get("timeout_seconds", 300),
                max_actions=raw_task.get("max_actions", 50),
                allowed_categories=raw_task.get("allowed_categories"),
            )
            subtasks.append(spec)

        return subtasks

    def _validate(
        self,
        subtasks: List[TaskSpec],
        execution_order: List[List[int]],
    ) -> None:
        """Validate the decomposition result.

        Note: execution_order uses int indices here (pre-string-conversion).
        """
        if not subtasks:
            raise TaskDecompositionError("Decomposition produced no subtasks")

        if len(subtasks) > self._max_subtasks:
            raise TaskDecompositionError(
                f"Too many subtasks: {len(subtasks)} > {self._max_subtasks}"
            )

        # Validate execution_order references valid indices
        all_indices: set = set()
        for group in execution_order:
            for idx in group:
                int_idx = int(idx) if isinstance(idx, str) else idx
                if int_idx < 0 or int_idx >= len(subtasks):
                    raise TaskDecompositionError(
                        f"execution_order references invalid index {int_idx}"
                    )
                if int_idx in all_indices:
                    raise TaskDecompositionError(
                        f"execution_order references index {int_idx} multiple times"
                    )
                all_indices.add(int_idx)
