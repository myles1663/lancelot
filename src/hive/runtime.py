"""
HIVE Sub-Agent Runtime — execution loop per agent.

Uses threading.Event for pause/resume and a collapse flag for shutdown.
Between each action: check pause → validate soul → governance check →
execute → emit receipt → check exit conditions.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src.hive.types import (
    AgentState,
    CollapseReason,
    ControlMethod,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
)
from src.hive.errors import (
    AgentCollapsedError,
    AgentPausedError,
    ScopedSoulViolationError,
    SubAgentTimeoutError,
)
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.integration.governance_bridge import GovernanceBridge

logger = logging.getLogger(__name__)


class SubAgentRuntime:
    """Execution loop for a single HIVE sub-agent.

    Runs in a thread. Between each action:
    1. Check pause event (block if paused)
    2. Validate scoped soul
    3. Governance check
    4. Execute action
    5. Emit receipt
    6. Check exit conditions (timeout, max actions, collapse)
    """

    def __init__(
        self,
        agent_record: SubAgentRecord,
        registry: AgentRegistry,
        receipt_manager: HiveReceiptManager,
        governance_bridge: Optional[GovernanceBridge] = None,
        scoped_soul=None,
        action_executor: Optional[Callable] = None,
    ):
        self._record = agent_record
        self._registry = registry
        self._receipts = receipt_manager
        self._governance = governance_bridge
        self._scoped_soul = scoped_soul
        self._action_executor = action_executor

        # Control signals
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        self._collapse_requested = False
        self._collapse_reason: Optional[CollapseReason] = None
        self._collapse_message: Optional[str] = None

        # Timing
        self._start_time: Optional[float] = None

    @property
    def agent_id(self) -> str:
        return self._record.agent_id

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def is_collapse_requested(self) -> bool:
        return self._collapse_requested

    # ── Control Signals ──────────────────────────────────────────────

    def pause(self, reason: str = "") -> None:
        """Signal the agent to pause after the current action."""
        self._pause_event.clear()
        self._receipts.record_agent_paused(
            agent_id=self.agent_id,
            reason=reason,
            quest_id=self._record.quest_id,
        )
        logger.info("Agent %s pause requested: %s", self.agent_id, reason)

    def resume(self) -> None:
        """Resume a paused agent."""
        self._pause_event.set()
        self._receipts.record_agent_resumed(
            agent_id=self.agent_id,
            quest_id=self._record.quest_id,
        )
        logger.info("Agent %s resumed", self.agent_id)

    def request_collapse(
        self,
        reason: CollapseReason,
        message: Optional[str] = None,
    ) -> None:
        """Request the agent to collapse after the current action."""
        self._collapse_requested = True
        self._collapse_reason = reason
        self._collapse_message = message
        # Also unblock if paused so it can collapse
        self._pause_event.set()
        logger.info(
            "Agent %s collapse requested: %s - %s",
            self.agent_id, reason.value, message,
        )

    # ── Execution Loop ───────────────────────────────────────────────

    def run(self, actions: List[Dict[str, Any]]) -> TaskResult:
        """Execute the agent's action sequence.

        Args:
            actions: List of action dicts to execute sequentially.

        Returns:
            TaskResult with execution outcome.
        """
        self._start_time = time.monotonic()
        task_spec = self._record.task_spec
        outputs: Dict[str, Any] = {}
        error_msg = None

        try:
            for i, action in enumerate(actions):
                # 1. Check collapse request
                if self._collapse_requested:
                    break

                # 2. Check pause (blocks until resumed or collapsed)
                self._wait_for_unpause()
                if self._collapse_requested:
                    break

                # 3. Check timeout
                elapsed_s = time.monotonic() - self._start_time
                if elapsed_s > task_spec.timeout_seconds:
                    self.request_collapse(
                        CollapseReason.TIMEOUT,
                        f"Timeout after {elapsed_s:.0f}s",
                    )
                    break

                # 4. Check max actions
                action_count = self._registry.increment_action_count(self.agent_id)
                if action_count > task_spec.max_actions:
                    self.request_collapse(
                        CollapseReason.MAX_ACTIONS_EXCEEDED,
                        f"Exceeded max actions: {task_spec.max_actions}",
                    )
                    break

                # 5. Governance check
                if self._governance:
                    capability = action.get("capability", action.get("action", "unknown"))
                    gov_result = self._governance.validate_action(
                        capability=capability,
                        agent_id=self.agent_id,
                    )
                    if not gov_result.approved:
                        self._receipts.record_governance_check(
                            agent_id=self.agent_id,
                            capability=capability,
                            approved=False,
                            tier=gov_result.tier,
                            quest_id=self._record.quest_id,
                        )
                        if gov_result.requires_operator_approval:
                            # Pause for operator approval
                            self.pause(f"Governance requires approval: {capability}")
                            self._wait_for_unpause()
                            if self._collapse_requested:
                                break
                        else:
                            self.request_collapse(
                                CollapseReason.GOVERNANCE_DENIED,
                                f"Governance denied: {gov_result.reason}",
                            )
                            break

                # 6. Execute action
                action_result = None
                if self._action_executor:
                    try:
                        action_result = self._action_executor(action)
                    except Exception as exc:
                        error_msg = str(exc)
                        self._receipts.record_agent_action(
                            agent_id=self.agent_id,
                            action_name=action.get("action", "unknown"),
                            action_inputs=action,
                            action_result={"error": error_msg},
                            quest_id=self._record.quest_id,
                        )
                        self.request_collapse(CollapseReason.ERROR, error_msg)
                        break

                # 7. Emit receipt
                self._receipts.record_agent_action(
                    agent_id=self.agent_id,
                    action_name=action.get("action", "unknown"),
                    action_inputs=action,
                    action_result=action_result,
                    quest_id=self._record.quest_id,
                )

                if action_result:
                    outputs[f"action_{i}"] = action_result

        except Exception as exc:
            error_msg = str(exc)
            logger.error("Agent %s runtime error: %s", self.agent_id, exc)

        elapsed_ms = int((time.monotonic() - self._start_time) * 1000)

        # Determine success
        success = (
            error_msg is None
            and not self._collapse_requested
            or self._collapse_reason == CollapseReason.COMPLETED
        )

        return TaskResult(
            task_id=task_spec.task_id,
            agent_id=self.agent_id,
            success=success,
            outputs=outputs,
            error_message=error_msg,
            action_count=self._record.action_count,
            duration_ms=elapsed_ms,
            collapse_reason=self._collapse_reason,
        )

    def _wait_for_unpause(self, timeout: float = 300.0) -> None:
        """Wait for the pause event to be set (or timeout)."""
        if not self._pause_event.wait(timeout=timeout):
            # Timeout while paused — collapse
            self.request_collapse(
                CollapseReason.TIMEOUT,
                "Timeout while paused",
            )
