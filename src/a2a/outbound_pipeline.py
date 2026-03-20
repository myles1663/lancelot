# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Outbound A2A Governance Pipeline — 10-stage gate for outbound delegations.

Every outbound A2A delegation routes through the governed connector proxy.
The Lancelot agent holds no direct connection to any external A2A server.

Stages:
    1. Remote Agent Resolution (registry lookup)
    2. Soul Evaluation (outbound_a2a_permissions)
    3. Network Allowlist Check
    4. Skill Security Pipeline (PII scrubbing)
    5. Risk Classification
    6. T3 Approval Gate
    7. Credential Injection (from Vault)
    8. Delegation and Polling
    9. Response Inspection (PII scrubbing)
    10. Receipt Generation + Trust Update

Public API:
    OutboundPipeline(registry, receipt_service, soul) — constructor
    delegate(target_agent_id, task_content, delegation_depth) → DelegationResult
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier
from src.a2a.types import (
    A2ATask, A2AMessage, A2AMessagePart, A2AArtifact, A2ATaskStatus,
    AgentFramework, RemoteAgent,
)

logger = logging.getLogger(__name__)


@dataclass
class DelegationResult:
    """Result of an outbound A2A delegation."""
    success: bool
    task_id: Optional[str] = None
    target_agent_id: Optional[str] = None
    status: str = A2ATaskStatus.FAILED.value
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    stage_blocked: Optional[str] = None
    block_reason: Optional[str] = None
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "task_id": self.task_id,
            "target_agent_id": self.target_agent_id,
            "status": self.status,
            "artifacts": self.artifacts,
            "error": self.error,
            "stage_blocked": self.stage_blocked,
            "block_reason": self.block_reason,
        }


class OutboundPipeline:
    """10-stage outbound A2A governance pipeline."""

    # PII patterns to scrub from outbound content
    PII_PATTERNS = [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{16}\b",              # Credit card
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
    ]

    def __init__(
        self,
        registry: Any,
        receipt_service: Any,
        soul: Any,
    ):
        self._registry = registry
        self._receipt_service = receipt_service
        self._soul = soul

    def delegate(
        self,
        target_agent_id: str,
        task_content: str,
        task_type: str = "general",
        delegation_depth: int = 0,
        quest_id: Optional[str] = None,
    ) -> DelegationResult:
        """Run the full 10-stage outbound pipeline.

        Args:
            target_agent_id: Registered remote agent ID.
            task_content: Text content to delegate.
            task_type: Task type for Soul permission matching.
            delegation_depth: Current chain depth (0 = first delegation).
            quest_id: Parent quest ID for receipt linkage.
        """
        task_id = str(uuid.uuid4())

        # Stage 1: Remote Agent Resolution
        agent = self._resolve_agent(target_agent_id)
        if agent is None:
            return self._block(task_id, target_agent_id, "agent_resolution",
                               "AGENT_NOT_REGISTERED",
                               "Target agent not registered in A2A registry.")

        if agent.status != "active":
            return self._block(task_id, target_agent_id, "agent_resolution",
                               "AGENT_SUSPENDED",
                               f"Target agent is {agent.status}.")

        # Stage 2: Soul Evaluation
        soul_result = self._soul_evaluation(agent, task_type)
        if not soul_result["allowed"]:
            return self._block(task_id, target_agent_id, "soul_evaluation",
                               "SOUL_DENIED", soul_result.get("reason", ""))

        # Check delegation depth
        outbound_perms = getattr(self._soul, "outbound_a2a_permissions", None)
        max_depth = outbound_perms.max_delegation_depth if outbound_perms else 2
        if delegation_depth >= max_depth:
            return self._block(task_id, target_agent_id, "delegation_depth",
                               "DEPTH_EXCEEDED",
                               f"Delegation depth {delegation_depth} >= max {max_depth}.")

        # Stage 3: Network Allowlist Check
        allowlist_result = self._check_allowlist(agent)
        if not allowlist_result["allowed"]:
            return self._block(task_id, target_agent_id, "network_allowlist",
                               "NETWORK_BLOCKED", "Target endpoint not in Network Allowlist.")

        # Stage 4: Skill Security Pipeline (PII scrubbing)
        scrubbed_content = self._scrub_outbound(task_content)

        # Stage 5: Risk Classification
        risk_tier = self._classify_risk(agent, task_type)

        # Stage 6: T3 Approval Gate
        if risk_tier >= 3:
            approval = self._t3_approval_gate(task_id, agent, task_type)
            if approval["requires_wait"]:
                return DelegationResult(
                    success=False,
                    task_id=task_id,
                    target_agent_id=target_agent_id,
                    status=A2ATaskStatus.SUBMITTED.value,
                    requires_approval=True,
                )

        # Stage 7: Credential Injection (stub — proxy handles)
        # In production, the connector proxy injects credentials from Vault.
        # The Lancelot agent never holds credentials for external servers.

        # Stage 8: Delegation (via HTTP client)
        delegation_receipt = self._emit_receipt(
            ActionType.A2A_DELEGATION_SENT,
            "a2a_delegation_sent",
            {
                "task_id": task_id,
                "target_agent_id": target_agent_id,
                "task_type": task_type,
                "content_hash": hashlib.sha256(scrubbed_content.encode()).hexdigest()[:16],
                "delegation_depth": delegation_depth,
                "risk_tier": risk_tier,
            },
            {},
            quest_id=quest_id,
        )

        # Execute delegation via HTTP (stub for now — actual HTTP in client.py)
        try:
            response = self._execute_delegation(agent, scrubbed_content, task_id)
        except Exception as e:
            self._emit_receipt(
                ActionType.A2A_DELEGATION_FAILED,
                "a2a_delegation_failed",
                {"task_id": task_id, "target_agent_id": target_agent_id},
                {"error": str(e)},
                quest_id=quest_id,
            )
            if self._registry:
                self._registry.update_interaction(target_agent_id, "failed", "outbound")
            return DelegationResult(
                success=False, task_id=task_id, target_agent_id=target_agent_id,
                error=str(e), status=A2ATaskStatus.FAILED.value,
            )

        # Stage 9: Response Inspection (PII scrubbing)
        if response.get("artifacts"):
            response["artifacts"] = self._scrub_response_artifacts(response["artifacts"])

        # Stage 10: Receipt Generation + Trust Update
        self._emit_receipt(
            ActionType.A2A_DELEGATION_COMPLETED,
            "a2a_delegation_completed",
            {"task_id": task_id, "target_agent_id": target_agent_id},
            {"status": response.get("status", "completed"), "risk_tier": risk_tier},
            quest_id=quest_id,
        )
        if self._registry:
            self._registry.update_interaction(target_agent_id, "completed", "outbound")

        return DelegationResult(
            success=True,
            task_id=task_id,
            target_agent_id=target_agent_id,
            status=response.get("status", A2ATaskStatus.COMPLETED.value),
            artifacts=response.get("artifacts", []),
        )

    def _resolve_agent(self, agent_id: str) -> Optional[RemoteAgent]:
        """Stage 1: Resolve target from registry."""
        if not self._registry:
            return None
        return self._registry.get(agent_id)

    def _soul_evaluation(self, agent: RemoteAgent, task_type: str) -> Dict[str, Any]:
        """Stage 2: Evaluate against outbound_a2a_permissions."""
        outbound_perms = getattr(self._soul, "outbound_a2a_permissions", None)

        if outbound_perms is None:
            return {"allowed": False, "reason": "No outbound A2A permissions in Soul"}

        if not outbound_perms.allow_outbound:
            return {"allowed": False, "reason": "Outbound A2A disabled in Soul"}

        # Check allowed_targets
        if outbound_perms.allowed_targets:
            allowed = False
            for target in outbound_perms.allowed_targets:
                id_match = target.get("agent_id") == agent.agent_id
                fw_match = target.get("agent_framework") == agent.agent_framework

                if id_match or fw_match:
                    # Check task type
                    allowed_types = target.get("allowed_task_types", ["*"])
                    if "*" in allowed_types or task_type in allowed_types:
                        allowed = True
                        break

            if not allowed:
                return {"allowed": False, "reason": f"Target {agent.agent_id} not in allowed_targets"}

        return {"allowed": True}

    def _check_allowlist(self, agent: RemoteAgent) -> Dict[str, Any]:
        """Stage 3: Check Network Allowlist."""
        # In production, check against the actual Network Allowlist
        # For now, verify agent has a card URL
        if not agent.agent_card_url:
            return {"allowed": False}

        # Agent's domains should be in allowlist (auto-populated on registration)
        if not agent.network_allowlist_entries:
            return {"allowed": False}

        return {"allowed": True}

    def _scrub_outbound(self, content: str) -> str:
        """Stage 4: Scrub PII from outbound content."""
        import re
        scrubbed = content
        for pattern in self.PII_PATTERNS:
            scrubbed = re.sub(pattern, "[REDACTED]", scrubbed)
        return scrubbed

    def _classify_risk(self, agent: RemoteAgent, task_type: str) -> int:
        """Stage 5: Classify risk tier."""
        risk = agent.outbound_trust_tier

        # Financial operations default to T3
        financial_types = {"payment", "transfer", "billing", "financial"}
        if task_type.lower() in financial_types:
            risk = max(risk, 3)

        # Unknown framework escalation
        if agent.agent_framework == AgentFramework.UNKNOWN.value:
            risk = max(risk, 2)

        return risk

    def _t3_approval_gate(
        self, task_id: str, agent: RemoteAgent, task_type: str,
    ) -> Dict[str, Any]:
        """Stage 6: T3 approval gate."""
        self._emit_receipt(
            ActionType.T3_A2A_OUTBOUND_APPROVAL_REQUEST,
            "t3_a2a_outbound_approval_request",
            {
                "task_id": task_id,
                "target_agent_id": agent.agent_id,
                "task_type": task_type,
                "risk_tier": 3,
            },
            {},
        )
        return {"requires_wait": True}

    def _execute_delegation(
        self, agent: RemoteAgent, content: str, task_id: str,
    ) -> Dict[str, Any]:
        """Stage 8: Execute delegation via HTTP.

        Stub implementation — in production, uses A2A client with
        credential injection from Vault and polling for completion.
        """
        # This would use src/a2a/client.py in production
        # For now, return a stub response
        return {
            "status": A2ATaskStatus.COMPLETED.value,
            "artifacts": [],
        }

    def _scrub_response_artifacts(
        self, artifacts: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Stage 9: Scrub PII from response artifacts."""
        import re
        scrubbed = []
        for artifact in artifacts:
            a = dict(artifact)
            for part in a.get("parts", []):
                if isinstance(part, dict) and part.get("text"):
                    for pattern in self.PII_PATTERNS:
                        part["text"] = re.sub(pattern, "[REDACTED]", part["text"])
            scrubbed.append(a)
        return scrubbed

    def _block(
        self,
        task_id: str,
        target_agent_id: str,
        stage: str,
        reason_code: str,
        reason: str,
    ) -> DelegationResult:
        """Block a delegation and emit A2A_OUTBOUND_BLOCKED receipt."""
        self._emit_receipt(
            ActionType.A2A_OUTBOUND_BLOCKED,
            f"a2a_outbound_blocked_{stage}",
            {"task_id": task_id, "target_agent_id": target_agent_id, "stage": stage},
            {"reason_code": reason_code},
        )
        return DelegationResult(
            success=False, task_id=task_id, target_agent_id=target_agent_id,
            error=reason, stage_blocked=stage, block_reason=reason_code,
            status=A2ATaskStatus.FAILED.value,
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
            logger.warning("A2A outbound receipt emission failed: %s", e)
            return None
