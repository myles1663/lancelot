"""
HIVE Sub-Agent Runtime - execution loop per agent.

Uses threading.Event for pause/resume and a collapse flag for shutdown.
Between each action: check pause, validate soul, check governance,
execute, emit receipt, and check exit conditions.
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
from src.hive.scoped_soul import (
    capability_matches_allowed_categories,
    scoped_capability_boundary,
    scoped_soul_capability_decision,
    scoped_soul_enforces_capability,
)

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
        scope_boundary=None,
        action_executor: Optional[Callable] = None,
    ):
        self._record = agent_record
        self._registry = registry
        self._receipts = receipt_manager
        self._governance = governance_bridge
        self._scoped_soul = scoped_soul
        self._scope_boundary = (
            scope_boundary
            if scope_boundary is not None
            else scoped_capability_boundary(scoped_soul)
        )
        self._task_spec = agent_record.task_spec
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

    def get_record(self) -> SubAgentRecord:
        """Return the live runtime record for lifecycle coordination."""
        return self._record

    def latest_receipt_id(self) -> Optional[str]:
        """Return the most recent receipt emitted for this runtime."""
        return getattr(self._record, "latest_receipt_id", None)

    def set_latest_receipt_id(self, receipt_id: Optional[str]) -> None:
        """Update the receipt chain pointer after lifecycle transitions."""
        self._record.latest_receipt_id = receipt_id

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def is_collapse_requested(self) -> bool:
        return self._collapse_requested

    # Control signals

    def pause(self, reason: str = "") -> None:
        """Signal the agent to pause after the current action."""
        self._pause_event.clear()
        self._receipts.record_agent_paused(
            agent_id=self.agent_id,
            reason=reason,
            quest_id=self._record.quest_id,
            operator_id=self._record.operator_id,
            session_id=self._record.session_id,
            operator_name=self._record.operator_name,
        )
        logger.info("Agent %s pause requested: %s", self.agent_id, reason)

    def resume(self) -> None:
        """Resume a paused agent."""
        self._pause_event.set()
        self._receipts.record_agent_resumed(
            agent_id=self.agent_id,
            quest_id=self._record.quest_id,
            operator_id=self._record.operator_id,
            session_id=self._record.session_id,
            operator_name=self._record.operator_name,
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

    # Execution loop

    def run(self, actions: List[Dict[str, Any]]) -> TaskResult:
        """Execute the agent's action sequence.

        Args:
            actions: List of action dicts to execute sequentially.

        Returns:
            TaskResult with execution outcome.
        """
        self._start_time = time.monotonic()
        task_spec = self._task_spec
        outputs: Dict[str, Any] = {}
        error_msg = None

        try:
            for i, action in enumerate(actions):
                if self._collapse_requested:
                    break

                self._wait_for_unpause()
                if self._collapse_requested:
                    break

                elapsed_s = time.monotonic() - self._start_time
                if elapsed_s > task_spec.timeout_seconds:
                    self.request_collapse(
                        CollapseReason.TIMEOUT,
                        f"Timeout after {elapsed_s:.0f}s",
                    )
                    break

                action_count = self._registry.increment_action_count(self.agent_id)
                if action_count > task_spec.max_actions:
                    self.request_collapse(
                        CollapseReason.MAX_ACTIONS_EXCEEDED,
                        f"Exceeded max actions: {task_spec.max_actions}",
                    )
                    break

                try:
                    self._validate_scoped_action(action)
                except ScopedSoulViolationError as exc:
                    error_msg = str(exc)
                    receipt_id = self._receipts.record_agent_action(
                        agent_id=self.agent_id,
                        action_name=action.get("action", "unknown"),
                        action_inputs=action,
                        action_result={"error": error_msg, "type": "soul_violation"},
                        quest_id=self._record.quest_id,
                        parent_receipt_id=self._record.latest_receipt_id,
                        operator_id=self._record.operator_id,
                        session_id=self._record.session_id,
                        operator_name=self._record.operator_name,
                    )
                    self._record.latest_receipt_id = receipt_id
                    self.request_collapse(CollapseReason.SOUL_VIOLATION, error_msg)
                    break

                if self._governance:
                    capability = action.get("capability", action.get("action", "unknown"))
                    gov_result = self._governance.validate_action(
                        capability=capability,
                        agent_id=self.agent_id,
                    )
                    receipt_id = self._receipts.record_governance_check(
                        parent_receipt_id=self._record.latest_receipt_id,
                        agent_id=self.agent_id,
                        capability=capability,
                        approved=gov_result.approved,
                        tier=gov_result.tier,
                        quest_id=self._record.quest_id,
                        operator_id=self._record.operator_id,
                        session_id=self._record.session_id,
                        operator_name=self._record.operator_name,
                    )
                    self._record.latest_receipt_id = receipt_id
                    if not gov_result.approved:
                        if gov_result.requires_operator_approval:
                            approval_request_id = gov_result.approval_request_id
                            if not approval_request_id:
                                self._governance.request_approval(
                                    capability=capability,
                                    agent_id=self.agent_id,
                                    context=action,
                                )
                            pause_reason = f"Governance requires approval: {capability}"
                            if approval_request_id:
                                pause_reason += f" (request_id={approval_request_id})"
                            self.pause(pause_reason)
                            self._wait_for_unpause()
                            if self._collapse_requested:
                                break
                        else:
                            self.request_collapse(
                                CollapseReason.GOVERNANCE_DENIED,
                                f"Governance denied: {gov_result.reason}",
                            )
                            break

                action_result = None
                if self._action_executor:
                    action_name = str(action.get("action", "unknown"))
                    try:
                        action_payload = self._build_execution_payload(action)
                        action_result = self._action_executor(action_payload)
                    except ScopedSoulViolationError as exc:
                        error_msg = str(exc)
                        receipt_id = self._receipts.record_agent_action(
                            agent_id=self.agent_id,
                            action_name=action_name,
                            action_inputs=action,
                            action_result={"error": error_msg, "type": "soul_violation"},
                            quest_id=self._record.quest_id,
                            parent_receipt_id=self._record.latest_receipt_id,
                            operator_id=self._record.operator_id,
                            session_id=self._record.session_id,
                            operator_name=self._record.operator_name,
                        )
                        self._record.latest_receipt_id = receipt_id
                        self.request_collapse(CollapseReason.SOUL_VIOLATION, error_msg)
                        break
                    except Exception as exc:
                        error_msg = str(exc)
                        error_detail = (
                            f"Agent {self.agent_id} action {i} ({action_name}) "
                            f"failed in executor: {exc}"
                        )
                        logger.error("%s", error_detail, exc_info=True)
                        receipt_id = self._receipts.record_agent_action(
                            agent_id=self.agent_id,
                            action_name=action_name,
                            action_inputs=action,
                            action_result={
                                "error": error_detail,
                                "exception_type": type(exc).__name__,
                                "action_index": i,
                            },
                            quest_id=self._record.quest_id,
                            parent_receipt_id=self._record.latest_receipt_id,
                            operator_id=self._record.operator_id,
                            session_id=self._record.session_id,
                            operator_name=self._record.operator_name,
                        )
                        self._record.latest_receipt_id = receipt_id
                        self.request_collapse(CollapseReason.ERROR, error_msg)
                        break

                receipt_id = self._receipts.record_agent_action(
                    agent_id=self.agent_id,
                    action_name=action.get("action", "unknown"),
                    action_inputs=action,
                    action_result=action_result,
                    quest_id=self._record.quest_id,
                    parent_receipt_id=self._record.latest_receipt_id,
                    operator_id=self._record.operator_id,
                    session_id=self._record.session_id,
                    operator_name=self._record.operator_name,
                )
                self._record.latest_receipt_id = receipt_id

                if action_result:
                    outputs[f"action_{i}"] = action_result

        except ScopedSoulViolationError as exc:
            error_msg = str(exc)
            self.request_collapse(CollapseReason.SOUL_VIOLATION, error_msg)
        except Exception as exc:
            error_msg = str(exc)
            logger.error("Agent %s runtime error: %s", self.agent_id, exc)

        elapsed_ms = int((time.monotonic() - self._start_time) * 1000)

        success = (
            error_msg is None
            and (
                not self._collapse_requested
                or self._collapse_reason == CollapseReason.COMPLETED
            )
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

    def _wait_for_unpause(self, timeout: Optional[float] = None) -> None:
        """Wait for the pause event to be set (or timeout).

        Uses the remaining task timeout by default so a paused agent
        cannot outlive its deadline.
        """
        if timeout is None:
            if self._start_time is not None:
                elapsed = time.monotonic() - self._start_time
                remaining = max(0.0, self._task_spec.timeout_seconds - elapsed)
                timeout = remaining
            else:
                timeout = float(self._task_spec.timeout_seconds)

        if not self._pause_event.wait(timeout=timeout):
            # Timeout while paused: collapse.
            self.request_collapse(
                CollapseReason.TIMEOUT,
                "Timeout while paused",
            )

    def _build_execution_payload(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp the runtime's authoritative scope onto every executed action."""
        action_payload = dict(action)
        action_payload["agent_id"] = self.agent_id
        action_payload["scoped_soul"] = self._scope_boundary
        action_payload["scoped_soul_document"] = self._scoped_soul
        action_payload["allowed_apps"] = list(self._task_spec.allowed_apps or [])
        action_payload["allowed_categories"] = list(self._task_spec.allowed_categories or [])
        return action_payload

    def _validate_scoped_action(self, action: Dict[str, Any]) -> None:
        """Enforce task-scoped boundaries before governance and execution."""
        task_spec = self._task_spec
        context = action.get("context") or {}
        capability = str(action.get("capability") or action.get("action") or "").strip().lower()

        allowed_apps = {a.lower() for a in (task_spec.allowed_apps or []) if a}
        target_app = str(
            context.get("target_app") or context.get("app_name") or ""
        ).strip().lower()
        if allowed_apps and target_app and target_app not in allowed_apps:
            raise ScopedSoulViolationError(
                agent_id=self.agent_id,
                action=capability or action.get("action", "unknown"),
                reason=f"Scoped Soul forbids app '{target_app}' for agent {self.agent_id}",
            )

        allowed_categories = list(task_spec.allowed_categories or [])
        generic_wrapper_capabilities = {
            "execute",
            "execute_subtask",
            "app_control",
        }

        if (
            capability
            and self._scope_boundary is not None
            and capability not in generic_wrapper_capabilities
            and scoped_soul_enforces_capability(self._scope_boundary, capability)
        ):
            decision = scoped_soul_capability_decision(self._scope_boundary, capability)
            if decision == "requires_approval":
                raise ScopedSoulViolationError(
                    agent_id=self.agent_id,
                    action=capability,
                    reason=(
                        f"Capability '{capability}' requires scoped Soul approval "
                        f"for agent {self.agent_id}"
                    ),
                )
            if decision == "deny":
                raise ScopedSoulViolationError(
                    agent_id=self.agent_id,
                    action=capability,
                    reason=(
                        f"Capability '{capability}' is not permitted by the scoped Soul "
                        f"for agent {self.agent_id}"
                    ),
                )

        if allowed_categories and capability and capability not in generic_wrapper_capabilities:
            if not capability_matches_allowed_categories(capability, allowed_categories):
                raise ScopedSoulViolationError(
                    agent_id=self.agent_id,
                    action=capability,
                    reason=(
                        f"Capability '{capability}' is outside scoped categories "
                        f"{allowed_categories}"
                    ),
                )
