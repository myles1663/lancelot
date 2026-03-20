# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Inbound A2A Governance Pipeline — 8-stage gate for incoming A2A tasks.

Every inbound A2A task passes through these stages before execution.
Rejected tasks return a failed status with a governance-neutral reason code.
Internal Soul constraints and kill switch state are never exposed to the caller.

Stages:
    1. Authentication
    2. Caller Identity Resolution
    3. Skill Security Pipeline (prompt injection, malicious payloads)
    4. Soul Evaluation (inbound_a2a_permissions)
    5. Risk Classification (T0-T3)
    6. T3 Approval Gate
    7. Governed Execution (becomes a Lancelot quest)
    8. Response and Trust Update

Public API:
    InboundPipeline(registry, receipt_service, soul) — constructor
    evaluate(task, caller_info) → PipelineResult
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier
from src.a2a.types import A2ATask, A2ATaskStatus, AgentFramework

logger = logging.getLogger(__name__)


@dataclass
class CallerInfo:
    """Resolved identity of an inbound A2A caller."""
    agent_id: str = ""
    display_name: str = ""
    agent_framework: str = AgentFramework.UNKNOWN.value
    agent_card_url: str = ""
    trust_tier: int = 2
    authenticated: bool = False
    auth_method: str = "none"


@dataclass
class PipelineResult:
    """Result of the inbound governance pipeline."""
    allowed: bool
    stage_blocked: Optional[str] = None  # Which stage blocked
    block_reason: Optional[str] = None  # Internal reason (not exposed to caller)
    external_reason: str = ""  # Governance-neutral reason for the caller
    risk_tier: int = 2
    quest_id: Optional[str] = None
    requires_approval: bool = False
    approval_receipt_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "stage_blocked": self.stage_blocked,
            "block_reason": self.block_reason,
            "external_reason": self.external_reason,
            "risk_tier": self.risk_tier,
            "quest_id": self.quest_id,
            "requires_approval": self.requires_approval,
        }


class InboundPipeline:
    """8-stage inbound A2A governance pipeline."""

    def __init__(
        self,
        registry: Any,
        receipt_service: Any,
        soul: Any,
    ):
        self._registry = registry
        self._receipt_service = receipt_service
        self._soul = soul

    def evaluate(
        self,
        task: A2ATask,
        caller: CallerInfo,
    ) -> PipelineResult:
        """Run the full 8-stage pipeline on an inbound A2A task.

        Returns PipelineResult indicating whether the task is allowed.
        """
        # Emit A2A_TASK_RECEIVED
        self._emit_receipt(
            ActionType.A2A_TASK_RECEIVED,
            "a2a_task_received",
            {"task_id": task.id, "caller_agent_id": caller.agent_id},
            {},
        )

        # Stage 1: Authentication
        if not caller.authenticated:
            return self._block(task, caller, "authentication", "AUTHENTICATION_FAILED",
                               "Task submission requires authentication.")

        # Stage 2: Caller Identity Resolution
        caller = self._resolve_caller(caller)

        # Check for Lancelot-to-Lancelot (should use Federation)
        if caller.agent_framework == AgentFramework.LANCELOT.value:
            return self._block(task, caller, "caller_resolution", "LANCELOT_INSTANCE",
                               "This agent is a Lancelot instance. Use the Federation "
                               "Governance API for Lancelot-to-Lancelot communication.")

        # Stage 3: Skill Security Pipeline
        security_result = self._skill_security_check(task)
        if not security_result["passed"]:
            return self._block(task, caller, "skill_security", "INPUT_REJECTED",
                               "Task content did not pass security screening.")

        # Stage 4: Soul Evaluation
        soul_result = self._soul_evaluation(caller)
        if not soul_result["allowed"]:
            return self._block(task, caller, "soul_evaluation", "SOUL_DENIED",
                               "Task not permitted by agent governance policy.")

        # Stage 5: Risk Classification
        risk_tier = self._classify_risk(task, caller)

        # Stage 6: T3 Approval Gate
        if risk_tier >= 3:
            approval = self._t3_approval_gate(task, caller)
            if approval["requires_wait"]:
                return PipelineResult(
                    allowed=True,
                    risk_tier=risk_tier,
                    requires_approval=True,
                    approval_receipt_id=approval.get("receipt_id"),
                )

        # Stage 7: Governed Execution — create quest
        quest_id = str(uuid.uuid4())
        self._emit_receipt(
            ActionType.A2A_TASK_EXECUTING,
            "a2a_task_executing",
            {"task_id": task.id, "caller_agent_id": caller.agent_id, "risk_tier": risk_tier},
            {"quest_id": quest_id},
            quest_id=quest_id,
        )

        return PipelineResult(
            allowed=True,
            risk_tier=risk_tier,
            quest_id=quest_id,
        )

    def complete_task(
        self,
        task: A2ATask,
        caller: CallerInfo,
        quest_id: str,
    ) -> None:
        """Stage 8: Response and Trust Update after task execution."""
        self._emit_receipt(
            ActionType.A2A_TASK_COMPLETED,
            "a2a_task_completed",
            {"task_id": task.id, "caller_agent_id": caller.agent_id},
            {"quest_id": quest_id, "status": "completed"},
            quest_id=quest_id,
        )
        # Update trust ledger for caller
        if self._registry:
            self._registry.update_interaction(caller.agent_id, "completed", "inbound")

    def _resolve_caller(self, caller: CallerInfo) -> CallerInfo:
        """Stage 2: Resolve caller identity from registry."""
        if not self._registry:
            return caller

        agent = self._registry.get(caller.agent_id)
        if agent:
            caller.trust_tier = agent.inbound_trust_tier
            caller.display_name = agent.display_name
            caller.agent_framework = agent.agent_framework
            return caller

        # Auto-register if allowed
        inbound_perms = getattr(self._soul, "inbound_a2a_permissions", None)
        if inbound_perms and inbound_perms.require_preregistration:
            # Pre-registration required but caller not found
            return caller

        # Auto-register at default trust tier
        default_tier = 2
        if inbound_perms:
            tier_str = inbound_perms.default_trust_tier
            default_tier = int(tier_str[1]) if len(tier_str) == 2 else 2

        self._registry.auto_register(
            agent_id=caller.agent_id,
            display_name=caller.display_name or caller.agent_id,
            framework=caller.agent_framework,
            card_url=caller.agent_card_url,
            default_tier=default_tier,
        )
        # Emit auto-registration receipt (SYSTEM, no identity required)
        self._emit_receipt(
            ActionType.A2A_AGENT_REGISTERED,
            "a2a_agent_auto_registered",
            {"agent_id": caller.agent_id, "framework": caller.agent_framework},
            {"auto_registered": True, "default_tier": default_tier},
        )
        caller.trust_tier = default_tier
        return caller

    def _skill_security_check(self, task: A2ATask) -> Dict[str, Any]:
        """Stage 3: Check task content for injection/malicious payloads."""
        if not task.message:
            return {"passed": True}

        # Extract text content
        text_parts = []
        for part in task.message.parts:
            if part.text:
                text_parts.append(part.text)

        combined_text = " ".join(text_parts)

        # Basic injection pattern check (reuses existing patterns)
        injection_patterns = [
            "ignore previous instructions",
            "disregard all prior",
            "system prompt:",
            "you are now",
            "forget everything",
            "override your instructions",
        ]
        lower_text = combined_text.lower()
        for pattern in injection_patterns:
            if pattern in lower_text:
                return {"passed": False, "reason": "prompt_injection_detected"}

        return {"passed": True}

    def _soul_evaluation(self, caller: CallerInfo) -> Dict[str, Any]:
        """Stage 4: Evaluate caller against Soul inbound_a2a_permissions."""
        inbound_perms = getattr(self._soul, "inbound_a2a_permissions", None)

        if inbound_perms is None:
            return {"allowed": False, "reason": "No inbound A2A permissions in Soul"}

        if not inbound_perms.allow_inbound:
            return {"allowed": False, "reason": "Inbound A2A disabled in Soul"}

        # Check blocked callers
        for blocked in inbound_perms.blocked_callers:
            if blocked.get("agent_id") == caller.agent_id:
                return {"allowed": False, "reason": f"Agent {caller.agent_id} is blocked"}
            if blocked.get("agent_framework") == caller.agent_framework:
                return {"allowed": False, "reason": f"Framework {caller.agent_framework} is blocked"}

        # Check allowed callers (if list is populated, it acts as allowlist)
        if inbound_perms.allowed_callers:
            allowed = False
            for rule in inbound_perms.allowed_callers:
                if rule.get("agent_id") == caller.agent_id:
                    allowed = True
                    break
                if rule.get("agent_framework") == caller.agent_framework:
                    allowed = True
                    break
            if not allowed:
                return {"allowed": False, "reason": "Caller not in allowed_callers list"}

        # Check pre-registration requirement
        if inbound_perms.require_preregistration:
            agent = self._registry.get(caller.agent_id) if self._registry else None
            if not agent or agent.auto_registered:
                return {"allowed": False, "reason": "Pre-registration required"}

        return {"allowed": True}

    def _classify_risk(self, task: A2ATask, caller: CallerInfo) -> int:
        """Stage 5: Classify risk tier for this task."""
        # Start at caller's trust tier
        risk = caller.trust_tier

        # Escalate for unknown frameworks
        if caller.agent_framework == AgentFramework.UNKNOWN.value:
            risk = max(risk, 2)

        return risk

    def _t3_approval_gate(self, task: A2ATask, caller: CallerInfo) -> Dict[str, Any]:
        """Stage 6: T3 approval gate — emit approval request."""
        receipt = self._emit_receipt(
            ActionType.T3_A2A_INBOUND_APPROVAL_REQUEST,
            "t3_a2a_inbound_approval_request",
            {
                "task_id": task.id,
                "caller_agent_id": caller.agent_id,
                "caller_framework": caller.agent_framework,
                "risk_tier": 3,
            },
            {},
        )
        return {"requires_wait": True, "receipt_id": receipt.id if receipt else None}

    def _block(
        self,
        task: A2ATask,
        caller: CallerInfo,
        stage: str,
        reason_code: str,
        external_reason: str,
    ) -> PipelineResult:
        """Block a task and emit A2A_INBOUND_BLOCKED receipt."""
        self._emit_receipt(
            ActionType.A2A_INBOUND_BLOCKED,
            f"a2a_inbound_blocked_{stage}",
            {"task_id": task.id, "caller_agent_id": caller.agent_id, "stage": stage},
            {"reason_code": reason_code},
        )
        if self._registry and caller.agent_id:
            self._registry.update_interaction(caller.agent_id, "blocked", "inbound")

        return PipelineResult(
            allowed=False,
            stage_blocked=stage,
            block_reason=reason_code,
            external_reason=external_reason,
        )

    def _emit_receipt(
        self,
        action_type: ActionType,
        action_name: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        quest_id: Optional[str] = None,
    ) -> Optional[Receipt]:
        """Emit an A2A receipt. Never raises."""
        try:
            receipt = Receipt(
                action_type=action_type.value,
                action_name=action_name,
                inputs=inputs,
                outputs=outputs,
                status=ReceiptStatus.SUCCESS.value,
                tier=CognitionTier.DETERMINISTIC.value,
                quest_id=quest_id,
                metadata={"subsystem": "a2a"},
            )
            if self._receipt_service:
                self._receipt_service.create(receipt)
            return receipt
        except Exception as e:
            logger.warning("A2A receipt emission failed: %s", e)
            return None
