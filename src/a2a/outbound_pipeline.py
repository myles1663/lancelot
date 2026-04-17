# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Outbound A2A Governance Pipeline - 10-stage gate for outbound delegations.

Every outbound A2A delegation routes through a governed client boundary.
The Lancelot agent only talks to preregistered external peers and resolves
credentials from the vault at dispatch time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier
from src.a2a.types import (
    A2ATaskStatus,
    AgentFramework,
    RemoteAgent,
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

    PII_PATTERNS = [
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"\b\d{16}\b",
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    ]

    def __init__(
        self,
        registry: Any,
        receipt_service: Any,
        soul: Any,
        vault: Any = None,
        a2a_client: Any = None,
    ):
        self._registry = registry
        self._receipt_service = receipt_service
        self._soul = soul
        self._vault = vault
        self._a2a_client = a2a_client

    def _get_soul(self) -> Any:
        """Resolve the live Soul object."""
        soul = self._soul
        if soul is None:
            return None
        if hasattr(soul, "inbound_a2a_permissions") or hasattr(soul, "outbound_a2a_permissions"):
            return soul
        return soul() if callable(soul) else soul

    def delegate(
        self,
        target_agent_id: str,
        task_content: str,
        task_type: str = "general",
        delegation_depth: int = 0,
        quest_id: Optional[str] = None,
        operator_id: str = "",
        session_id: str = "",
    ) -> DelegationResult:
        """Run the full outbound governance pipeline."""
        task_id = str(uuid.uuid4())

        agent = self._resolve_agent(target_agent_id)
        if agent is None:
            return self._block(
                task_id,
                target_agent_id,
                "agent_resolution",
                "AGENT_NOT_REGISTERED",
                "Target agent not registered in A2A registry.",
                operator_id=operator_id,
                session_id=session_id,
            )

        if agent.status != "active":
            return self._block(
                task_id,
                target_agent_id,
                "agent_resolution",
                "AGENT_SUSPENDED",
                f"Target agent is {agent.status}.",
                operator_id=operator_id,
                session_id=session_id,
            )

        soul_result = self._soul_evaluation(agent, task_type)
        if not soul_result["allowed"]:
            return self._block(
                task_id,
                target_agent_id,
                "soul_evaluation",
                "SOUL_DENIED",
                soul_result.get("reason", ""),
                operator_id=operator_id,
                session_id=session_id,
            )

        verification_result = self._verify_remote_agent_card(agent)
        if not verification_result["allowed"]:
            return self._block(
                task_id,
                target_agent_id,
                "agent_card_verification",
                verification_result.get("reason_code", "AGENT_CARD_VERIFICATION_FAILED"),
                verification_result.get("reason", "Remote Agent Card verification failed."),
                operator_id=operator_id,
                session_id=session_id,
            )

        soul = self._get_soul()
        outbound_perms = getattr(soul, "outbound_a2a_permissions", None)
        max_depth = outbound_perms.max_delegation_depth if outbound_perms else 2
        if delegation_depth >= max_depth:
            return self._block(
                task_id,
                target_agent_id,
                "delegation_depth",
                "DEPTH_EXCEEDED",
                f"Delegation depth {delegation_depth} >= max {max_depth}.",
                operator_id=operator_id,
                session_id=session_id,
            )

        allowlist_result = self._check_allowlist(agent)
        if not allowlist_result["allowed"]:
            return self._block(
                task_id,
                target_agent_id,
                "network_allowlist",
                "NETWORK_BLOCKED",
                "Target endpoint not in Network Allowlist.",
                operator_id=operator_id,
                session_id=session_id,
            )

        scrubbed_content = self._scrub_outbound(task_content)
        risk_tier = self._classify_risk(agent, task_type)

        if risk_tier >= 3:
            approval = self._t3_approval_gate(
                task_id,
                agent,
                task_type,
                operator_id=operator_id,
                session_id=session_id,
            )
            if approval["requires_wait"]:
                return DelegationResult(
                    success=False,
                    task_id=task_id,
                    target_agent_id=target_agent_id,
                    status=A2ATaskStatus.SUBMITTED.value,
                    requires_approval=True,
                )

        credentials = self._resolve_credentials(agent)
        if (agent.auth_type or "").strip().lower() in {"bearer_token", "api_key"} and credentials is None:
            return self._block(
                task_id,
                target_agent_id,
                "credential_injection",
                "MISSING_CREDENTIALS",
                "Target agent credentials are not available in the vault.",
                operator_id=operator_id,
                session_id=session_id,
            )

        self._emit_receipt(
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
            operator_id=operator_id,
            session_id=session_id,
        )

        try:
            response = self._execute_delegation(agent, scrubbed_content, task_id, credentials)
        except Exception as exc:
            self._emit_receipt(
                ActionType.A2A_DELEGATION_FAILED,
                "a2a_delegation_failed",
                {"task_id": task_id, "target_agent_id": target_agent_id},
                {"error": str(exc)},
                quest_id=quest_id,
                operator_id=operator_id,
                session_id=session_id,
            )
            if self._registry:
                self._registry.update_interaction(target_agent_id, "failed", "outbound")
            return DelegationResult(
                success=False,
                task_id=task_id,
                target_agent_id=target_agent_id,
                error=str(exc),
                status=A2ATaskStatus.FAILED.value,
            )

        if response.get("artifacts"):
            response["artifacts"] = self._scrub_response_artifacts(response["artifacts"])

        self._emit_receipt(
            ActionType.A2A_DELEGATION_COMPLETED,
            "a2a_delegation_completed",
            {"task_id": task_id, "target_agent_id": target_agent_id},
            {"status": response.get("status", "completed"), "risk_tier": risk_tier},
            quest_id=quest_id,
            operator_id=operator_id,
            session_id=session_id,
        )
        if self._registry:
            self._registry.update_interaction(target_agent_id, "completed", "outbound")

        return DelegationResult(
            success=True,
            task_id=response.get("id", task_id),
            target_agent_id=target_agent_id,
            status=response.get("status", A2ATaskStatus.COMPLETED.value),
            artifacts=response.get("artifacts", []),
        )

    def _resolve_agent(self, agent_id: str) -> Optional[RemoteAgent]:
        if not self._registry:
            return None
        return self._registry.get(agent_id)

    def _soul_evaluation(self, agent: RemoteAgent, task_type: str) -> Dict[str, Any]:
        soul = self._get_soul()
        outbound_perms = getattr(soul, "outbound_a2a_permissions", None)

        if outbound_perms is None:
            return {"allowed": False, "reason": "No outbound A2A permissions in Soul"}

        if not outbound_perms.allow_outbound:
            return {"allowed": False, "reason": "Outbound A2A disabled in Soul"}

        if outbound_perms.allowed_targets:
            allowed = False
            for target in outbound_perms.allowed_targets:
                id_match = target.get("agent_id") == agent.agent_id
                fw_match = target.get("agent_framework") == agent.agent_framework
                if id_match or fw_match:
                    allowed_types = target.get("allowed_task_types", ["*"])
                    if "*" in allowed_types or task_type in allowed_types:
                        allowed = True
                        break
            if not allowed:
                return {"allowed": False, "reason": f"Target {agent.agent_id} not in allowed_targets"}

        return {"allowed": True}

    def _verify_remote_agent_card(self, agent: RemoteAgent) -> Dict[str, Any]:
        soul = self._get_soul()
        outbound_perms = getattr(soul, "outbound_a2a_permissions", None)
        require_verification = bool(
            outbound_perms and getattr(outbound_perms, "require_agent_card_verification", True)
        )
        if not require_verification:
            return {"allowed": True}

        if self._a2a_client is None:
            return {"allowed": False, "reason": "A2A client not initialized."}
        result = self._a2a_client.assess_agent_card(agent, allow_repin=False)
        if not result["allowed"]:
            return {"allowed": False, "reason": result["reason"]}
        return {"allowed": True}

    def _check_allowlist(self, agent: RemoteAgent) -> Dict[str, Any]:
        if not agent.agent_card_url:
            return {"allowed": False}
        if not agent.network_allowlist_entries:
            return {"allowed": False}

        parsed = urlparse(agent.agent_card_url)
        if not parsed.hostname:
            return {"allowed": False}

        host = parsed.hostname.lower()
        scheme = parsed.scheme.lower()
        origin = f"{scheme}://{host}"
        origin_with_port = f"{origin}:{parsed.port}" if parsed.port is not None else origin
        host_with_port = f"{host}:{parsed.port}" if parsed.port is not None else host

        candidates = {host, host_with_port, origin, origin_with_port}
        for entry in agent.network_allowlist_entries:
            pattern = (entry or "").strip().lower()
            if not pattern:
                continue
            if any(fnmatch(candidate, pattern) for candidate in candidates):
                return {"allowed": True}
        return {"allowed": False}

    def _scrub_outbound(self, content: str) -> str:
        import re

        scrubbed = content
        for pattern in self.PII_PATTERNS:
            scrubbed = re.sub(pattern, "[REDACTED]", scrubbed)
        return scrubbed

    def _classify_risk(self, agent: RemoteAgent, task_type: str) -> int:
        risk = agent.outbound_trust_tier
        financial_types = {"payment", "transfer", "billing", "financial"}
        if task_type.lower() in financial_types:
            risk = max(risk, 3)
        if agent.agent_framework == AgentFramework.UNKNOWN.value:
            risk = max(risk, 2)
        return risk

    def _t3_approval_gate(
        self,
        task_id: str,
        agent: RemoteAgent,
        task_type: str,
        *,
        operator_id: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
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
            operator_id=operator_id,
            session_id=session_id,
        )
        return {"requires_wait": True}

    def _execute_delegation(
        self,
        agent: RemoteAgent,
        content: str,
        task_id: str,
        credentials: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        if self._a2a_client is None:
            raise RuntimeError("A2A client not initialized")

        response = self._a2a_client.send_task(agent, content, task_id, credentials=credentials)
        remote_task_id = response.get("id", task_id)
        status = response.get("status", A2ATaskStatus.FAILED.value)

        if status in {
            A2ATaskStatus.SUBMITTED.value,
            A2ATaskStatus.WORKING.value,
            A2ATaskStatus.INPUT_REQUIRED.value,
        }:
            for _ in range(5):
                time.sleep(0.2)
                response = self._a2a_client.poll_task_status(
                    agent,
                    remote_task_id,
                    credentials=credentials,
                )
                status = response.get("status", A2ATaskStatus.FAILED.value)
                if status not in {
                    A2ATaskStatus.SUBMITTED.value,
                    A2ATaskStatus.WORKING.value,
                    A2ATaskStatus.INPUT_REQUIRED.value,
                }:
                    break

        return response

    def _resolve_credentials(self, agent: RemoteAgent) -> Optional[Dict[str, str]]:
        auth_type = (agent.auth_type or "").strip().lower()
        if auth_type in {"", "none"}:
            return None
        if auth_type not in {"bearer_token", "api_key"}:
            raise RuntimeError(f"Unsupported A2A auth_type: {agent.auth_type}")
        if not agent.credentials_ref or self._vault is None:
            return None

        try:
            raw = self._vault.retrieve(agent.credentials_ref)
        except Exception as exc:
            logger.warning("A2A outbound credential resolution failed for %s: %s", agent.agent_id, exc)
            return None

        parsed = None
        try:
            maybe = json.loads(raw)
            if isinstance(maybe, dict):
                parsed = maybe
        except Exception:
            parsed = None

        if auth_type == "bearer_token":
            token = str(parsed.get("token") or parsed.get("bearer_token") or "") if parsed else str(raw)
            return {"type": "bearer_token", "token": token} if token else None

        key = str(parsed.get("key") or parsed.get("api_key") or "") if parsed else str(raw)
        return {"type": "api_key", "key": key} if key else None

    def _scrub_response_artifacts(self, artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        import re

        scrubbed = []
        for artifact in artifacts:
            item = dict(artifact)
            for part in item.get("parts", []):
                if isinstance(part, dict) and part.get("text"):
                    for pattern in self.PII_PATTERNS:
                        part["text"] = re.sub(pattern, "[REDACTED]", part["text"])
            scrubbed.append(item)
        return scrubbed

    def _block(
        self,
        task_id: str,
        target_agent_id: str,
        stage: str,
        reason_code: str,
        reason: str,
        *,
        operator_id: str = "",
        session_id: str = "",
    ) -> DelegationResult:
        self._emit_receipt(
            ActionType.A2A_OUTBOUND_BLOCKED,
            f"a2a_outbound_blocked_{stage}",
            {"task_id": task_id, "target_agent_id": target_agent_id, "stage": stage},
            {"reason_code": reason_code},
            operator_id=operator_id,
            session_id=session_id,
        )
        return DelegationResult(
            success=False,
            task_id=task_id,
            target_agent_id=target_agent_id,
            error=reason,
            stage_blocked=stage,
            block_reason=reason_code,
            status=A2ATaskStatus.FAILED.value,
        )

    def _emit_receipt(
        self,
        action_type: ActionType,
        action_name: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        quest_id: Optional[str] = None,
        operator_id: str = "",
        session_id: str = "",
    ) -> Optional[Receipt]:
        try:
            receipt = Receipt(
                action_type=action_type.value,
                action_name=action_name,
                inputs=inputs,
                outputs=outputs,
                status=ReceiptStatus.SUCCESS.value,
                tier=CognitionTier.DETERMINISTIC.value,
                quest_id=quest_id,
                operator_id=operator_id or None,
                session_id=session_id or None,
                metadata={"subsystem": "a2a"},
            )
            if self._receipt_service:
                self._receipt_service.create(receipt)
            return receipt
        except Exception as exc:
            logger.warning("A2A outbound receipt emission failed: %s", exc)
            return None
