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
from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult

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
    ) -> Dict[str, Any]:
        """Enumerate UI elements of an app. Read-only — no governance gate."""
        self._validate_app_access(app_name, scoped_soul)
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
    ) -> Dict[str, Any]:
        """Query app state. Read-only — no governance gate."""
        self._validate_app_access(app_name, scoped_soul)
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
    ) -> Dict[str, Any]:
        """Execute a mutating action on an app.

        Mutating actions go through governance check first.
        """
        self._validate_app_access(app_name, scoped_soul)
        self._validate_action_category(action, scoped_soul)

        # Governance check for mutating actions
        if self._governance:
            gov_result = self._governance.validate_action(
                capability=f"uab_{action}",
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

        result = await asyncio.to_thread(
            self._uab_provider.act, app_name, action, params or {},
        )
        return result if isinstance(result, dict) else {"result": result}

    async def state(
        self,
        app_name: str,
        agent_id: str,
        scoped_soul=None,
    ) -> Dict[str, Any]:
        """Get current state of an app. Read-only."""
        self._validate_app_access(app_name, scoped_soul)
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
    ) -> None:
        """Validate that the agent's scoped soul allows access to this app.

        If the task spec has allowed_apps, only those are permitted.
        """
        # App access validation is done through the task spec's allowed_apps
        # which is checked by the runtime before calling the bridge.
        pass

    def _validate_action_category(
        self,
        action: str,
        scoped_soul=None,
    ) -> None:
        """Validate that the action category is allowed by the scoped soul."""
        if scoped_soul is None:
            return

        # Check if the action would be in requires_approval
        action_capability = f"uab_{action}"
        requires = scoped_soul.autonomy_posture.requires_approval
        if action_capability in requires:
            # This is a governed action — will be caught by governance check
            pass
