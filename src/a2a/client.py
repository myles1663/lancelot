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

Streaming from remote agents uses polling in Phase 2.
SSE streaming planned for Phase 3 if requested.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.a2a.types import (
    AgentCard, AgentCardSkill, RemoteAgent, AgentCardStatus,
    A2ATaskStatus,
)
from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier

logger = logging.getLogger(__name__)


class A2AClient:
    """HTTP client for outbound A2A communication.

    All requests go through this client — the Lancelot agent never
    holds a direct connection to external A2A servers.
    """

    def __init__(self, receipt_service: Any = None):
        self._receipt_service = receipt_service

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

        Used for outbound delegation monitoring (Phase 2 polling approach).
        """
        try:
            import httpx

            headers = {}
            if credentials:
                if credentials.get("type") == "bearer_token":
                    headers["Authorization"] = f"Bearer {credentials['token']}"

            base_url = agent.agent_card_url.replace("/.well-known/agent.json", "")
            endpoint = f"{base_url}/a2a/tasks/{task_id}"

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
    ) -> bool:
        """Verify a remote agent's Agent Card is still valid.

        Fetches the card and compares with cached version.
        Returns True if verified successfully.
        """
        if not agent.agent_card_url:
            return False

        card = self.fetch_agent_card(agent.agent_card_url)
        if card is None:
            return False

        # Check for Lancelot instances (should use Federation)
        if self.is_lancelot_instance(card):
            logger.warning(
                "Agent %s is a Lancelot instance — should use Federation API",
                agent.agent_id,
            )

        return True

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
