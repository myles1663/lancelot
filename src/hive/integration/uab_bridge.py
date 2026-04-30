"""
HIVE UAB Bridge — wraps the existing UABProvider for sub-agent use.

Every action: validate Scoped Soul → governance check → UABProvider method → receipt.
This wraps the existing provider, not a parallel RPC client.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src.hive.errors import ScopedSoulViolationError, UABControlError
from src.hive.integration.governance_bridge import GovernanceBridge
from src.hive.scoped_soul import (
    capability_matches_allowed_categories,
    scoped_soul_capability_decision,
    scoped_soul_enforces_capability,
)

logger = logging.getLogger(__name__)


class UABBridge:
    """Bridge between HIVE sub-agents and the UAB desktop app control.

    Wraps the existing UABProvider with per-action governance checks
    and scoped soul validation.
    """

    def __init__(
        self,
        uab_provider=None,
        governance_bridge: Optional[GovernanceBridge] = None,
    ):
        self._uab_provider = uab_provider
        self._governance = governance_bridge

    @property
    def available(self) -> bool:
        """Whether the UAB provider is connected."""
        return self._uab_provider is not None

    # ── UAB Operations (wrapped with governance) ─────────────────────

    async def get_available_apps(self) -> List[Dict[str, Any]]:
        """List connected desktop applications."""
        if not self._uab_provider:
            return []
        result = await asyncio.to_thread(self._uab_provider.detect)
        return result if isinstance(result, list) else []

    async def enumerate(
        self,
        app_name: str,
        agent_id: str,
        scoped_soul=None,
        allowed_apps: Optional[List[str]] = None,
        allowed_categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Enumerate UI elements of an app. Read-only — no governance gate."""
        self._validate_app_access(app_name, scoped_soul, allowed_apps=allowed_apps)
        self._validate_scope_capability(
            "uab_query",
            scoped_soul=scoped_soul,
            allowed_categories=allowed_categories,
            agent_id=agent_id,
        )
        if not self._uab_provider:
            raise UABControlError("UAB provider not available")
        result = await asyncio.to_thread(
            self._uab_provider.enumerate, app_name,
        )
        return result if isinstance(result, dict) else {"elements": result}

    async def query(
        self,
        app_name: str,
        query: str,
        agent_id: str,
        scoped_soul=None,
        allowed_apps: Optional[List[str]] = None,
        allowed_categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Query app state. Read-only — no governance gate."""
        self._validate_app_access(app_name, scoped_soul, allowed_apps=allowed_apps)
        self._validate_scope_capability(
            "uab_query",
            scoped_soul=scoped_soul,
            allowed_categories=allowed_categories,
            agent_id=agent_id,
        )
        if not self._uab_provider:
            raise UABControlError("UAB provider not available")
        result = await asyncio.to_thread(
            self._uab_provider.query, app_name, query,
        )
        return result if isinstance(result, dict) else {"result": result}

    async def act(
        self,
        app_name: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        agent_id: str = "",
        scoped_soul=None,
        allowed_apps: Optional[List[str]] = None,
        allowed_categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute a mutating action on an app.

        Mutating actions go through governance check first.
        """
        self._validate_app_access(app_name, scoped_soul, allowed_apps=allowed_apps)
        capability = f"uab_{action}"
        self._validate_scope_capability(
            capability,
            scoped_soul=scoped_soul,
            allowed_categories=allowed_categories,
            agent_id=agent_id,
        )

        # Governance check for mutating actions
        if self._governance:
            gov_result = self._governance.validate_action(
                capability=capability,
                scope=app_name,
                target=action,
                agent_id=agent_id,
            )
            if not gov_result.approved:
                raise UABControlError(
                    f"Governance denied UAB action '{action}' on '{app_name}': "
                    f"{gov_result.reason}"
                )

        if not self._uab_provider:
            raise UABControlError("UAB provider not available")

        try:
            result = await asyncio.to_thread(
                self._uab_provider.act, app_name, action, params or {},
            )
        except Exception:
            self._record_trust_outcome(capability, app_name, success=False)
            raise

        payload = result if isinstance(result, dict) else {"result": result}
        success = bool(payload.get("success", True))
        self._record_trust_outcome(capability, app_name, success=success)
        return payload

    async def state(
        self,
        app_name: str,
        agent_id: str,
        scoped_soul=None,
        allowed_apps: Optional[List[str]] = None,
        allowed_categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get current state of an app. Read-only."""
        self._validate_app_access(app_name, scoped_soul, allowed_apps=allowed_apps)
        self._validate_scope_capability(
            "uab_state",
            scoped_soul=scoped_soul,
            allowed_categories=allowed_categories,
            agent_id=agent_id,
        )
        if not self._uab_provider:
            raise UABControlError("UAB provider not available")
        result = await asyncio.to_thread(
            self._uab_provider.state, app_name,
        )
        return result if isinstance(result, dict) else {"state": result}

    # ── Validation Helpers ───────────────────────────────────────────

    def _validate_app_access(
        self,
        app_name: str,
        scoped_soul=None,
        allowed_apps: Optional[List[str]] = None,
    ) -> None:
        """Validate that the agent's scoped soul allows access to this app.

        If the task spec has allowed_apps, only those are permitted.
        """
        allowed = {app.lower() for app in (allowed_apps or []) if app}
        normalized_app = str(app_name or "").strip().lower()
        if allowed and normalized_app and normalized_app not in allowed:
            raise ScopedSoulViolationError(
                agent_id="hive-uab",
                action=normalized_app,
                reason=f"Scoped Soul forbids desktop app '{normalized_app}'",
            )

    def _validate_scope_capability(
        self,
        capability: str,
        scoped_soul=None,
        allowed_categories: Optional[List[str]] = None,
        agent_id: str = "",
    ) -> None:
        """Validate a UAB capability against scoped Soul and task categories."""
        if scoped_soul_enforces_capability(scoped_soul, capability):
            decision = scoped_soul_capability_decision(scoped_soul, capability)
            if decision == "requires_approval":
                raise ScopedSoulViolationError(
                    agent_id=agent_id or "hive-uab",
                    action=capability,
                    reason=(
                        f"Scoped Soul requires operator approval for UAB capability "
                        f"'{capability}'"
                    ),
                )
            if decision == "deny":
                raise ScopedSoulViolationError(
                    agent_id=agent_id or "hive-uab",
                    action=capability,
                    reason=f"Scoped Soul does not permit UAB capability '{capability}'",
                )

        if allowed_categories and not capability_matches_allowed_categories(
            capability,
            allowed_categories,
        ):
            raise ScopedSoulViolationError(
                agent_id=agent_id or "hive-uab",
                action=capability,
                reason=(
                    f"UAB capability '{capability}' is outside scoped categories "
                    f"{allowed_categories}"
                ),
            )

    def _record_trust_outcome(self, capability: str, scope: str, success: bool) -> None:
        """Record the execution outcome for trust graduation/revocation."""
        if self._governance is None:
            return
        self._governance.update_trust(capability, scope, success=success)
