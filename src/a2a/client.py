# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Client — HTTP client for outbound A2A delegations.

HTTP-only transport (no stdio process spawning). Implements:
    - Agent Card fetching and verification
    - Task submission via POST /a2a/tasks/send
    - Status polling via GET /a2a/tasks/{task_id}
    - Credential injection from Vault references

Streaming from remote agents currently uses polling.
SSE streaming can be added if latency requires it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.a2a.types import (
    AgentCard, AgentCardSkill, RemoteAgent, AgentCardStatus,
    A2ATaskStatus,
)
from src.core.outbound_http import assert_url_allowed
from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier

logger = logging.getLogger(__name__)


class A2AClient:
    """HTTP client for outbound A2A communication.

    All requests go through this client — the Lancelot agent never
    holds a direct connection to external A2A servers.
    """

    def __init__(self, receipt_service: Any = None, network_interceptor=None):
        self._receipt_service = receipt_service
        self._network_interceptor = network_interceptor

    def fetch_agent_card(
        self,
        card_url: str,
        timeout: float = 10.0,
    ) -> Optional[AgentCard]:
        """Fetch and parse a remote Agent Card.

        Args:
            card_url: URL of the Agent Card (typically /.well-known/agent.json).
            timeout: HTTP request timeout in seconds.

        Returns:
            AgentCard if successfully fetched and parsed, None otherwise.
        """
        try:
            import httpx
            assert_url_allowed(
                card_url,
                component="A2A agent card fetch",
                network_interceptor=self._network_interceptor,
            )
            with httpx.Client(timeout=timeout) as client:
                response = client.get(card_url)
                response.raise_for_status()
                data = response.json()

            card = AgentCard(
                name=data.get("name", ""),
                description=data.get("description", ""),
                url=data.get("url", ""),
                version=data.get("version", ""),
                a2a_protocol_version=data.get("a2a_protocol_version", "0.2"),
                skills=[
                    AgentCardSkill(**s) for s in data.get("skills", [])
                ],
                authentication=data.get("authentication", {}),
                capabilities=data.get("capabilities", {}),
                governance_declaration=data.get("governance_declaration"),
            )

            # Emit fetch receipt
            self._emit_receipt(
                ActionType.A2A_AGENT_CARD_FETCHED,
                "a2a_agent_card_fetched",
                {"card_url": card_url},
                {
                    "agent_name": card.name,
                    "skills_count": len(card.skills),
                    "has_governance": card.governance_declaration is not None,
                },
            )

            return card

        except Exception as e:
            logger.warning("Failed to fetch Agent Card from %s: %s", card_url, e)
            return None

    def is_lancelot_instance(self, card: AgentCard) -> bool:
        """Check if a remote Agent Card indicates a Lancelot instance."""
        if card.governance_declaration:
            return card.governance_declaration.get("governance_framework") == "lancelot"
        return False

    def assess_agent_card(
        self,
        agent: RemoteAgent,
        *,
        allow_repin: bool = False,
    ) -> Dict[str, Any]:
        """Verify a remote agent card against the pinned registry contract.

        When ``allow_repin`` is False, the fetched card must match the pinned
        cached card content. When True, a successful verification updates the
        in-memory cache on the provided agent object so the caller can persist it.
        """
        if not agent.agent_card_url:
            return {"allowed": False, "reason": "Agent Card URL is not configured."}

        card = self.fetch_agent_card(agent.agent_card_url)
        if card is None:
            return {"allowed": False, "reason": "Remote Agent Card fetch failed."}

        if self.is_lancelot_instance(card):
            return {"allowed": False, "reason": "Lancelot instances must use Federation."}

        expected_origin = self._origin(agent.agent_card_url)
        actual_origin = self._origin(card.url or agent.agent_card_url)
        if expected_origin and actual_origin and expected_origin != actual_origin:
            return {
                "allowed": False,
                "reason": f"Agent Card origin mismatch: expected {expected_origin}, got {actual_origin}.",
            }

        framework_claim = self._framework_claim(card)
        expected_framework = (agent.agent_framework or "").strip().lower()
        if expected_framework and expected_framework != "unknown" and framework_claim and framework_claim != expected_framework:
            return {
                "allowed": False,
                "reason": f"Agent framework mismatch: expected {expected_framework}, got {framework_claim}.",
            }

        declared_auth = str(card.authentication.get("type", "")).strip().lower() if card.authentication else ""
        expected_auth = (agent.auth_type or "").strip().lower()
        if expected_auth and expected_auth != "none" and declared_auth and declared_auth != expected_auth:
            return {
                "allowed": False,
                "reason": f"Agent authentication mismatch: expected {expected_auth}, got {declared_auth}.",
            }

        claimed_agent_id = self._agent_id_claim(card)
        if claimed_agent_id and claimed_agent_id != agent.agent_id:
            return {
                "allowed": False,
                "reason": f"Agent Card identity mismatch: expected {agent.agent_id}, got {claimed_agent_id}.",
            }

        normalized = self._normalized_card_payload(card)
        record = {
            "card": normalized,
            "card_hash": self._hash_payload(normalized),
            "card_url": agent.agent_card_url,
            "card_origin": expected_origin,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "framework_claim": framework_claim,
            "auth_type": declared_auth,
        }

        existing = agent.agent_card_cache or {}
        existing_hash = existing.get("card_hash")
        if existing_hash:
            if existing_hash != record["card_hash"]:
                if not allow_repin:
                    return {"allowed": False, "reason": "Pinned Agent Card content changed and requires operator re-verification."}
        elif not allow_repin:
            return {"allowed": False, "reason": "Agent Card is not pinned; operator verification is required."}

        agent.agent_card_cache = record
        return {"allowed": True, "card": card, "cache_record": record}

    def send_task(
        self,
        agent: RemoteAgent,
        content: str,
        task_id: str,
        credentials: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Send a task to a remote A2A agent.

        Args:
            agent: Target remote agent with endpoint URL.
            content: Task content (already PII-scrubbed).
            task_id: Pre-generated task ID.
            credentials: Auth credentials from Vault (injected by proxy).
            timeout: HTTP request timeout.

        Returns:
            A2A task response dict with status and artifacts.
        """
        try:
            import httpx

            # Build A2A task payload
            payload = {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": content}],
                },
                "metadata": {"task_id": task_id},
            }

            # Build headers with credential injection
            headers = {"Content-Type": "application/json"}
            if credentials:
                if credentials.get("type") == "bearer_token":
                    headers["Authorization"] = f"Bearer {credentials['token']}"
                elif credentials.get("type") == "api_key":
                    headers["X-API-Key"] = credentials["key"]

            # Determine endpoint
            base_url = agent.agent_card_url.replace("/.well-known/agent.json", "")
            endpoint = f"{base_url}/a2a/tasks/send"
            assert_url_allowed(
                endpoint,
                component="A2A task send",
                network_interceptor=self._network_interceptor,
            )

            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error("A2A task send failed for %s: %s", agent.agent_id, e)
            return {
                "id": task_id,
                "status": A2ATaskStatus.FAILED.value,
                "error": str(e),
            }

    def poll_task_status(
        self,
        agent: RemoteAgent,
        task_id: str,
        credentials: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Poll a remote agent for task status.

        Used for outbound delegation monitoring with polling.
        """
        try:
            import httpx

            headers = {}
            if credentials:
                if credentials.get("type") == "bearer_token":
                    headers["Authorization"] = f"Bearer {credentials['token']}"
                elif credentials.get("type") == "api_key":
                    headers["X-API-Key"] = credentials["key"]

            base_url = agent.agent_card_url.replace("/.well-known/agent.json", "")
            endpoint = f"{base_url}/a2a/tasks/{task_id}"
            assert_url_allowed(
                endpoint,
                component="A2A task status poll",
                network_interceptor=self._network_interceptor,
            )

            with httpx.Client(timeout=timeout) as client:
                response = client.get(endpoint, headers=headers)
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.warning("A2A status poll failed for task %s: %s", task_id, e)
            return {"id": task_id, "status": A2ATaskStatus.FAILED.value}

    def verify_agent_card(
        self,
        agent: RemoteAgent,
        *,
        allow_repin: bool = False,
    ) -> bool:
        """Verify a remote agent's Agent Card is still valid.

        Fetches the card and compares with cached version.
        Returns True if verified successfully.
        """
        result = self.assess_agent_card(agent, allow_repin=allow_repin)
        if not result["allowed"]:
            logger.warning("A2A Agent Card verification failed for %s: %s", agent.agent_id, result["reason"])
        return result["allowed"]

    @staticmethod
    def _origin(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return ""
        host = parsed.hostname.lower()
        if parsed.port is not None:
            return f"{parsed.scheme.lower()}://{host}:{parsed.port}"
        return f"{parsed.scheme.lower()}://{host}"

    @staticmethod
    def _framework_claim(card: AgentCard) -> str:
        if card.governance_declaration:
            framework = card.governance_declaration.get("governance_framework")
            if framework:
                return str(framework).strip().lower()
        metadata = card.metadata or {}
        for key in ("agent_framework", "framework"):
            value = metadata.get(key)
            if value:
                return str(value).strip().lower()
        return ""

    @staticmethod
    def _agent_id_claim(card: AgentCard) -> str:
        metadata = card.metadata or {}
        for key in ("agent_id", "id"):
            value = metadata.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _normalized_card_payload(card: AgentCard) -> Dict[str, Any]:
        return card.to_dict()

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _emit_receipt(
        self,
        action_type: ActionType,
        action_name: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
    ) -> None:
        """Emit a receipt. Never raises."""
        try:
            if self._receipt_service:
                receipt = Receipt(
                    action_type=action_type.value,
                    action_name=action_name,
                    inputs=inputs,
                    outputs=outputs,
                    status=ReceiptStatus.SUCCESS.value,
                    tier=CognitionTier.DETERMINISTIC.value,
                    metadata={"subsystem": "a2a"},
                )
                self._receipt_service.create(receipt)
        except Exception as e:
            logger.warning("A2A client receipt emission failed: %s", e)
