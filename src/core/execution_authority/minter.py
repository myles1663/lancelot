"""
Permission Minter — mints ExecutionTokens when user approves requests.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from src.core.execution_authority.schema import (
    AuthResult,
    ExecutionToken,
    NetworkPolicy,
    SecretPolicy,
    TaskType,
    TokenStatus,
)
from src.core.execution_authority.store import ExecutionTokenStore

logger = logging.getLogger(__name__)


class PermissionMinter:
    """Mints ExecutionTokens when user approves a permission request.

    Also provides authority checking against minted tokens.
    """

    def __init__(self, store: ExecutionTokenStore, receipt_service=None):
        self.store = store
        self.receipt_service = receipt_service

    def mint_from_approval(
        self,
        scope: str,
        task_type: str = TaskType.OTHER.value,
        tools: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        paths: Optional[List[str]] = None,
        network: str = NetworkPolicy.OFF.value,
        network_hosts: Optional[List[str]] = None,
        risk_tier: str = "LOW",
        duration_sec: int = 300,
        max_actions: int = 50,
        requires_verifier: bool = False,
        session_id: str = "",
    ) -> ExecutionToken:
        """Mint a new ExecutionToken from an approved permission request.

        Args:
            scope: Human-readable description of what's authorized.
            task_type: TaskType enum value.
            tools: List of allowed tool IDs (empty = all).
            skills: List of allowed skill IDs (empty = all).
            paths: List of allowed path globs (empty = all).
            network: NetworkPolicy enum value.
            network_hosts: List of allowed domains if ALLOWLIST.
            risk_tier: "LOW", "MED", or "HIGH".
            duration_sec: Maximum token lifetime in seconds.
            max_actions: Maximum number of actions.
            requires_verifier: Whether each step needs verification.
            session_id: Session this token belongs to.

        Returns:
            The minted ExecutionToken.
        """
        token = ExecutionToken(
            scope=scope,
            task_type=task_type,
            allowed_tools=tools or [],
            allowed_skills=skills or [],
            allowed_paths=paths or [],
            network_policy=network,
            network_allowlist=network_hosts or [],
            risk_tier=risk_tier,
            max_duration_sec=duration_sec,
            max_actions=max_actions,
            requires_verifier=requires_verifier,
            session_id=session_id,
        )

        self.store.create(token)

        # Emit TOKEN_MINTED receipt
        if self.receipt_service:
            try:
                from src.shared.receipts import create_receipt, ActionType, CognitionTier
                receipt = create_receipt(
                    ActionType.TOKEN_MINTED,
                    "mint_execution_token",
                    inputs={"scope": scope, "task_type": task_type, "risk_tier": risk_tier},
                    tier=CognitionTier.DETERMINISTIC,
                )
                receipt = receipt.complete(
                    outputs={"token_id": token.id, "max_actions": max_actions,
                             "duration_sec": duration_sec},
                    duration_ms=0,
                )
                self.receipt_service.create(receipt)
                logger.info("TOKEN_MINTED receipt: %s for token %s", receipt.id, token.id)
            except Exception as e:
                logger.warning("Failed to emit TOKEN_MINTED receipt: %s", e)

        logger.info("Minted ExecutionToken %s: scope='%s', risk=%s", token.id, scope, risk_tier)
        return token

    def check_authority(
        self,
        token: ExecutionToken,
        tool: Optional[str] = None,
        skill: Optional[str] = None,
        path: Optional[str] = None,
        network_host: Optional[str] = None,
    ) -> AuthResult:
        """Validate an action against a token's scope.

        Args:
            token: The ExecutionToken to check against.
            tool: Tool name to validate (optional).
            skill: Skill name to validate (optional).
            path: File path to validate (optional).
            network_host: Network host to validate (optional).

        Returns:
            AuthResult with allowed=True/False and reason.
        """
        if token.is_expired():
            return AuthResult(allowed=False, reason="Token expired or exhausted")

        if token.status != TokenStatus.ACTIVE.value:
            return AuthResult(allowed=False, reason=f"Token status: {token.status}")

        if tool and not token.allows_tool(tool):
            return AuthResult(allowed=False, reason=f"Tool '{tool}' not in allowed_tools")

        if skill and not token.allows_skill(skill):
            return AuthResult(allowed=False, reason=f"Skill '{skill}' not in allowed_skills")

        if path and not token.allows_path(path):
            return AuthResult(allowed=False, reason=f"Path '{path}' not in allowed_paths")

        if network_host and not token.allows_network(network_host):
            return AuthResult(allowed=False, reason=f"Network host '{network_host}' not allowed by policy")

        return AuthResult(allowed=True, reason="Authorized")
