"""
HIVE UAB Bridge — wraps the existing UABProvider for sub-agent use.

Every action: validate Scoped Soul → governance check → UABProvider method → receipt.
This wraps the existing provider, not a parallel RPC client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.execution_authority import create_uab_authority_grant
from src.hive.errors import ScopedSoulViolationError, UABControlError
from src.hive.integration.governance_bridge import GovernanceBridge
from src.hive.scoped_soul import (
    capability_matches_allowed_categories,
    scoped_soul_capability_decision,
    scoped_soul_enforces_capability,
)

logger = logging.getLogger(__name__)

_UAB_GRANT_SECRET_ENV = "UAB_AUTHORITY_GRANT_SECRET"
_UAB_POLICY_VERSION = "hive-uab-governance-v1"
_ACTION_RISK_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "uab"
    / "data"
    / "action-risk.json"
)


def _load_action_risk_manifest() -> Dict[str, List[str]]:
    with _ACTION_RISK_MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    return {
        "read_only": list(manifest.get("read_only", [])),
        "mutating": list(manifest.get("mutating", [])),
        "destructive": list(manifest.get("destructive", [])),
        "sensitive_app_patterns": list(manifest.get("sensitive_app_patterns", [])),
    }


_ACTION_RISK_MANIFEST = _load_action_risk_manifest()
_READ_ONLY_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["read_only"])
_MUTATING_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["mutating"])
_DESTRUCTIVE_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["destructive"])
_SENSITIVE_APP_PATTERNS = frozenset(_ACTION_RISK_MANIFEST["sensitive_app_patterns"])


def _soul_version(scoped_soul=None) -> str:
    return str(getattr(scoped_soul, "version", "") or "unspecified")


def _selector_scope_from_params(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    return str(params.get("selectorScope") or params.get("selector_scope") or "")


def _risk_label_for_action(action: str, app_name: str) -> str:
    if action in _DESTRUCTIVE_ACTIONS:
        return "destructive"

    app_lower = app_name.lower()
    for pattern in _SENSITIVE_APP_PATTERNS:
        if pattern in app_lower:
            return "destructive" if action in _MUTATING_ACTIONS else "moderate"

    if action in _MUTATING_ACTIONS:
        return "moderate"
    if action in _READ_ONLY_ACTIONS:
        return "safe"
    return "destructive"


def _risk_flags_for_action(action: str, app_name: str) -> Dict[str, bool]:
    uab_risk = _risk_label_for_action(action, app_name)
    return {
        "mutating": uab_risk != "safe",
        "destructive": uab_risk == "destructive",
        "external_submission": action == "sendEmail",
        "credential_sensitive": action
        in {
            "getCookies",
            "setCookie",
            "deleteCookie",
            "clearCookies",
            "getLocalStorage",
            "setLocalStorage",
            "deleteLocalStorage",
            "clearLocalStorage",
            "getSessionStorage",
            "setSessionStorage",
            "deleteSessionStorage",
            "clearSessionStorage",
            "executeScript",
        },
        "sensitive_read": action
        in {
            "screenshot",
            "readDocument",
            "readCell",
            "readRange",
            "readFormula",
            "readSlides",
            "readSlideText",
            "readEmails",
            "getCookies",
            "getLocalStorage",
            "getSessionStorage",
        },
    }


class UABBridge:
    """Bridge between HIVE sub-agents and the UAB desktop app control.

    Wraps the existing UABProvider with per-action governance checks
    and scoped soul validation.
    """

    def __init__(
        self,
        uab_provider=None,
        governance_bridge: Optional[GovernanceBridge] = None,
        uab_grant_secret: Optional[str] = None,
    ):
        self._uab_provider = uab_provider
        self._governance = governance_bridge
        self._uab_grant_secret = uab_grant_secret or os.environ.get(_UAB_GRANT_SECRET_ENV)

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
        grant = self._issue_authority_grant(
            capability=capability,
            app_name=app_name,
            action=action,
            params=params,
            agent_id=agent_id,
            scoped_soul=scoped_soul,
        )

        if not self._uab_provider:
            raise UABControlError("UAB provider not available")

        try:
            governed_params = dict(params or {})
            governed_params["uabAuthorityGrant"] = grant
            result = await asyncio.to_thread(
                self._uab_provider.act, app_name, action, governed_params,
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

    def _issue_authority_grant(
        self,
        *,
        capability: str,
        app_name: str,
        action: str,
        params: Optional[Dict[str, Any]],
        agent_id: str,
        scoped_soul=None,
    ) -> Dict[str, Any]:
        if self._governance is None:
            raise UABControlError(
                f"Governance bridge required for UAB action '{action}' on '{app_name}'"
            )

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
        if not self._uab_grant_secret:
            raise UABControlError("UAB authority grant secret is not configured")

        uab_risk = _risk_label_for_action(action, app_name)
        flags = _risk_flags_for_action(action, app_name)
        grant = create_uab_authority_grant(
            secret=self._uab_grant_secret,
            risk_tier=gov_result.tier or "T2",
            uab_risk=uab_risk,
            capability=capability,
            app_name=app_name,
            action=action,
            selector_scope=_selector_scope_from_params(params),
            policy_version=_UAB_POLICY_VERSION,
            soul_version=_soul_version(scoped_soul),
            approval_id=gov_result.approval_request_id,
            **flags,
        )
        return grant.to_dict()
