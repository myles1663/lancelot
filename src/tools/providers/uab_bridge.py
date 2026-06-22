"""
Bridge Lancelot's tool fabric to the Universal App Bridge daemon.

This provider translates governed desktop-control requests into UAB JSON-RPC
calls and normalizes the results back into Lancelot provider contracts.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.core.execution_authority import UABAuthorityGrant
from src.core.governance.risk_terminology import (
    uab_label_for_tool_risk,
    validate_action_risk_manifest,
)
from src.tools.contracts import (
    AppActionResult,
    AppState,
    BaseProvider,
    Capability,
    ConnectionResult,
    DetectedApp,
    ProviderHealth,
    ProviderState,
    RiskLevel,
    UIElement,
)
from src.tools.receipts_uab import build_uab_receipt_metadata, emit_uab_canonical_receipt

logger = logging.getLogger(__name__)

_UAB_GRANT_SECRET_ENV = "UAB_AUTHORITY_GRANT_SECRET"
_UAB_AUTHORITY_GRANT_PARAM = "uabAuthorityGrant"
_UAB_RECEIPT_CONTEXT_PARAM = "uabReceiptContext"
_ACTION_RISK_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "uab"
    / "data"
    / "action-risk.json"
)


def _load_action_risk_manifest() -> dict[str, list[str]]:
    """Load the shared UAB action-risk taxonomy used by Python and TypeScript."""
    try:
        with _ACTION_RISK_MANIFEST_PATH.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except OSError as exc:
        raise RuntimeError(
            f"UAB action risk manifest could not be read at "
            f"{_ACTION_RISK_MANIFEST_PATH}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"UAB action risk manifest is invalid JSON at "
            f"{_ACTION_RISK_MANIFEST_PATH}: {exc}"
        ) from exc

    try:
        validate_action_risk_manifest(manifest)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return manifest


_ACTION_RISK_MANIFEST = _load_action_risk_manifest()
_READ_ONLY_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["read_only"])
_SAFE_INTROSPECTION_ACTIONS = frozenset(
    {
        "spatialMap",
        "textMap",
        "findByDescription",
        "focused",
        "findByPath",
        "watchChanges",
    }
)
_MUTATING_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["mutating"])
_DESTRUCTIVE_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["destructive"])
_SENSITIVE_APP_PATTERNS = frozenset(
    _ACTION_RISK_MANIFEST["sensitive_app_patterns"]
)
_SENSITIVE_READ_ACTIONS = frozenset(
    {
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
    }
)
_CLASSIFICATION_REQUIRED_READS = frozenset(
    {
        "readEmails",
        "getCookies",
        "getLocalStorage",
        "getSessionStorage",
        "getTabs",
    }
)
_CREDENTIAL_SENSITIVE_ACTIONS = frozenset(
    {
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
    }
)
_EXTERNAL_SUBMISSION_ACTIONS = frozenset({"sendEmail", "submitForm", "upload"})


def _read_http_error_body(error: urllib.error.HTTPError, source: str) -> str:
    """Read a bounded HTTP error body without silently swallowing decode errors."""
    try:
        return error.read().decode("utf-8", errors="replace")[:200]
    except Exception as exc:
        logger.warning("%s error body could not be decoded: %s", source, exc)
        return ""


def classify_action_risk(action: str, app_name: str = "") -> RiskLevel:
    """Classify the risk level of a UAB action."""
    if action in _DESTRUCTIVE_ACTIONS:
        return RiskLevel.HIGH

    app_lower = app_name.lower()
    for pattern in _SENSITIVE_APP_PATTERNS:
        if pattern in app_lower:
            return RiskLevel.HIGH if action in _MUTATING_ACTIONS else RiskLevel.MEDIUM

    if action in _MUTATING_ACTIONS:
        return RiskLevel.MEDIUM

    if action in _READ_ONLY_ACTIONS or action in _SAFE_INTROSPECTION_ACTIONS:
        return RiskLevel.LOW

    logger.warning("Unknown UAB action risk classification; defaulting to high", extra={"action": action})
    return RiskLevel.HIGH

@dataclass
class UABConfig:
    """Configuration for the UAB provider."""

    daemon_url: str = ""
    connect_timeout_s: int = 5
    read_timeout_s: int = 30
    authority_grant_secret: Optional[str] = None

    rpc_version: str = "2.0"
    next_id: int = 1

    max_elements: int = 5000
    max_element_depth: int = 20

    def __post_init__(self):
        if not self.daemon_url:
            self.daemon_url = os.environ.get(
                "UAB_DAEMON_URL", "http://host.docker.internal:7900"
            )
        if self.authority_grant_secret is None:
            self.authority_grant_secret = os.environ.get(_UAB_GRANT_SECRET_ENV)

class UABProvider(BaseProvider):
    """
    Universal App Bridge provider for framework-level desktop app control.

    Communicates with the UAB daemon via JSON-RPC 2.0 over TCP to detect,
    connect, enumerate, query, and act on desktop applications.
    """

    def __init__(self, config: Optional[UABConfig] = None, receipt_service: Optional[Any] = None):
        self.config = config or UABConfig()
        self._connected_apps: Dict[int, Dict[str, Any]] = {}
        self._denial_events: List[Dict[str, Any]] = []
        self._seen_authority_grant_nonces: set[str] = set()
        self._receipt_service = receipt_service

    @property
    def provider_id(self) -> str:
        return "uab_bridge"

    @property
    def capabilities(self) -> List[Capability]:
        return [Capability.APP_CONTROL]

    def _normalize_connection(self, item: Any) -> Dict[str, Any]:
        """Normalize daemon connection metadata across daemon versions."""
        if not isinstance(item, dict):
            return {}
        return {
            "pid": item.get("pid", 0),
            "name": item.get("name", "unknown"),
            "framework": item.get("framework", "unknown"),
            "connection_method": item.get("connectionMethod") or item.get("method"),
            "element_count": item.get("elementCount", 0),
            "window_title": item.get("windowTitle"),
        }

    def _normalize_status(self, info: Any) -> Dict[str, Any]:
        """Normalize daemon status metadata across compatibility layers."""
        if not isinstance(info, dict):
            return {
                "version": "unknown",
                "connected_apps": 0,
                "supported_frameworks": [],
                "transport": "json-rpc",
                "standalone_features": [],
                "connections": [],
            }

        connections = info.get("connections", [])
        normalized_connections = []
        if isinstance(connections, list):
            normalized_connections = [
                conn for conn in (self._normalize_connection(item) for item in connections)
                if conn
            ]

        return {
            "version": info.get("version", "unknown"),
            "connected_apps": info.get("connectedApps", len(normalized_connections)),
            "supported_frameworks": info.get("supportedFrameworks", []) or [],
            "transport": info.get("transport", "json-rpc"),
            "standalone_features": info.get("standaloneFeatures", []) or [],
            "connections": normalized_connections,
        }

    def _app_name_for_pid(self, pid: int) -> str:
        return self._connected_apps.get(pid, {}).get("name", "unknown")

    def _classification_available(self, action: str, app_name: str) -> bool:
        if action not in _CLASSIFICATION_REQUIRED_READS:
            return True
        return bool(app_name and app_name != "unknown")

    def _requires_authority_grant(
        self,
        action: str,
        risk: RiskLevel,
        app_name: str,
    ) -> bool:
        return (
            risk != RiskLevel.LOW
            or action in _SENSITIVE_READ_ACTIONS
            or action in _CREDENTIAL_SENSITIVE_ACTIONS
            or action in _EXTERNAL_SUBMISSION_ACTIONS
            or not self._classification_available(action, app_name)
        )

    def _read_requires_classification_or_grant(
        self,
        action: str,
        risk: RiskLevel,
        app_name: str,
    ) -> bool:
        return (
            risk != RiskLevel.LOW
            or action in _SENSITIVE_READ_ACTIONS
            or not self._classification_available(action, app_name)
        )

    @staticmethod
    def _uab_risk_label(risk: RiskLevel) -> str:
        return uab_label_for_tool_risk(risk)

    @staticmethod
    def _receipt_context_value(
        context: Dict[str, Any],
        snake_key: str,
        camel_key: str,
        default: Any = "",
    ) -> Any:
        return context.get(snake_key, context.get(camel_key, default))

    def _receipt_selector_scope(
        self,
        *,
        action: str,
        element_id: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        explicit = self._receipt_context_value(
            context,
            "selector_scope",
            "selectorScope",
            params.get("selectorScope") or params.get("selector_scope") or element_id or "",
        )
        if explicit:
            return str(explicit)
        if params.get("key"):
            return str(params["key"])
        keys = params.get("keys")
        if isinstance(keys, list) and keys:
            return "+".join(str(key) for key in keys)
        if params.get("label"):
            return str(params["label"])
        return action

    def _emit_canonical_receipt_for_action(
        self,
        *,
        outcome: str,
        pid: int,
        action: str,
        element_id: str,
        params: Optional[Dict[str, Any]],
        duration_ms: int,
        denial_reason: Optional[str] = None,
        error_reason: Optional[str] = None,
    ) -> Optional[str]:
        if self._receipt_service is None:
            return None

        checked_params = dict(params or {})
        context = checked_params.get(_UAB_RECEIPT_CONTEXT_PARAM) or {}
        if not isinstance(context, dict):
            context = {}

        app_name = str(
            self._receipt_context_value(
                context,
                "app_name",
                "appName",
                self._app_name_for_pid(pid),
            )
        )
        risk = classify_action_risk(action, app_name)
        selector_scope = self._receipt_selector_scope(
            action=action,
            element_id=element_id,
            params=checked_params,
            context=context,
        )
        grant_payload = checked_params.get(_UAB_AUTHORITY_GRANT_PARAM)
        try:
            metadata = build_uab_receipt_metadata(
                outcome=outcome,
                app_name=app_name,
                app_pid=int(self._receipt_context_value(context, "app_pid", "appPid", pid)),
                action=action,
                selector_scope=selector_scope,
                grant=grant_payload,
                risk_tier=str(
                    self._receipt_context_value(
                        context,
                        "risk_tier",
                        "riskTier",
                        "T3_IRREVERSIBLE" if risk is RiskLevel.HIGH else "T2_CONTROLLED",
                    )
                ),
                uab_risk=str(
                    self._receipt_context_value(
                        context,
                        "uab_risk",
                        "uabRisk",
                        self._uab_risk_label(risk),
                    )
                ),
                mutating=bool(
                    self._receipt_context_value(
                        context,
                        "mutating",
                        "mutating",
                        action in _MUTATING_ACTIONS,
                    )
                ),
                destructive=bool(
                    self._receipt_context_value(
                        context,
                        "destructive",
                        "destructive",
                        action in _DESTRUCTIVE_ACTIONS,
                    )
                ),
                sensitive_read=bool(
                    self._receipt_context_value(
                        context,
                        "sensitive_read",
                        "sensitiveRead",
                        action in _SENSITIVE_READ_ACTIONS,
                    )
                ),
                external_submission=bool(
                    self._receipt_context_value(
                        context,
                        "external_submission",
                        "externalSubmission",
                        action in _EXTERNAL_SUBMISSION_ACTIONS,
                    )
                ),
                credential_sensitive=bool(
                    self._receipt_context_value(
                        context,
                        "credential_sensitive",
                        "credentialSensitive",
                        action in _CREDENTIAL_SENSITIVE_ACTIONS,
                    )
                ),
                approval_id=self._receipt_context_value(
                    context,
                    "approval_id",
                    "approvalId",
                    None,
                ),
                parent_receipt_id=self._receipt_context_value(
                    context,
                    "parent_receipt_id",
                    "parentReceiptId",
                    None,
                ),
                workflow_id=str(
                    self._receipt_context_value(context, "workflow_id", "workflowId", "")
                ),
                run_id=str(self._receipt_context_value(context, "run_id", "runId", "")),
                denial_reason=denial_reason,
                error_reason=error_reason,
            )
            stored = emit_uab_canonical_receipt(
                metadata,
                receipt_service=self._receipt_service,
                duration_ms=duration_ms,
            )
            return stored.id
        except ValueError as exc:
            logger.debug(
                "Skipping UAB canonical receipt for action '%s': %s",
                action,
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Failed to emit UAB canonical receipt for action '%s': %s",
                action,
                exc,
            )
            return None

    def _canonical_outcome_for_app_result(self, result: AppActionResult) -> str:
        if result.success:
            return "success"
        if isinstance(result.result_data, dict) and "denial" in result.result_data:
            return "denied"
        return "failed"

    def _canonical_outcome_for_dict_result(self, result: Dict[str, Any]) -> str:
        if result.get("success") is True:
            return "success"
        if "denial" in result:
            return "denied"
        return "failed"

    def _canonical_failure_reason(
        self,
        *,
        action: str,
        result: Any,
    ) -> str:
        if isinstance(result, AppActionResult) and result.error_message:
            return result.error_message
        if isinstance(result, dict) and result.get("error"):
            return str(result["error"])
        return f"UAB action '{action}' failed without daemon error detail"

    def _finalize_app_action_result(
        self,
        result: AppActionResult,
        *,
        pid: int,
        action: str,
        element_id: str = "",
        params: Optional[Dict[str, Any]],
    ) -> AppActionResult:
        outcome = self._canonical_outcome_for_app_result(result)
        self._emit_canonical_receipt_for_action(
            outcome=outcome,
            pid=pid,
            action=action,
            element_id=element_id,
            params=params,
            duration_ms=result.duration_ms,
            denial_reason=result.error_message if outcome == "denied" else None,
            error_reason=(
                self._canonical_failure_reason(action=action, result=result)
                if outcome == "failed"
                else None
            ),
        )
        return result

    def _finalize_dict_action_result(
        self,
        result: Dict[str, Any],
        *,
        pid: int,
        action: str,
        params: Optional[Dict[str, Any]],
        duration_ms: int,
        selector_scope: str = "",
    ) -> Dict[str, Any]:
        outcome = self._canonical_outcome_for_dict_result(result)
        error = result.get("error")
        self._emit_canonical_receipt_for_action(
            outcome=outcome,
            pid=pid,
            action=action,
            element_id=selector_scope,
            params=params,
            duration_ms=duration_ms,
            denial_reason=error if outcome == "denied" else None,
            error_reason=(
                self._canonical_failure_reason(action=action, result=result)
                if outcome == "failed"
                else None
            ),
        )
        return result

    def _record_denial(
        self,
        *,
        pid: int,
        action: str,
        risk: RiskLevel,
        reason_code: str,
        reason: str,
        app_name: str,
        element_id: str = "",
        selector_scope: str = "",
    ) -> Dict[str, Any]:
        event = {
            "event_type": "uab_provider_denial",
            "provider_id": self.provider_id,
            "pid": pid,
            "app_name": app_name,
            "action": action,
            "element_id": element_id,
            "selector_scope": selector_scope,
            "risk": risk.value,
            "reason_code": reason_code,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._denial_events.append(event)
        return event

    def _denied_action_result(
        self,
        *,
        pid: int,
        action: str,
        risk: RiskLevel,
        reason_code: str,
        reason: str,
        app_name: str,
        element_id: str = "",
        selector_scope: str = "",
        duration_ms: int = 0,
    ) -> AppActionResult:
        event = self._record_denial(
            pid=pid,
            action=action,
            risk=risk,
            reason_code=reason_code,
            reason=reason,
            app_name=app_name,
            element_id=element_id,
            selector_scope=selector_scope,
        )
        return AppActionResult(
            success=False,
            action=action,
            element_id=element_id,
            error_message=reason,
            duration_ms=duration_ms,
            result_data={"denial": event},
        )

    def _denied_dict_result(
        self,
        *,
        pid: int,
        action: str,
        risk: RiskLevel,
        reason_code: str,
        reason: str,
        app_name: str,
        selector_scope: str = "",
    ) -> Dict[str, Any]:
        event = self._record_denial(
            pid=pid,
            action=action,
            risk=risk,
            reason_code=reason_code,
            reason=reason,
            app_name=app_name,
            selector_scope=selector_scope,
        )
        return {"success": False, "error": reason, "denial": event}

    @staticmethod
    def _params_with_grant(
        params: Optional[Dict[str, Any]] = None,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if params is None and uab_authority_grant is None:
            return None
        merged = dict(params or {})
        if uab_authority_grant is not None:
            merged[_UAB_AUTHORITY_GRANT_PARAM] = uab_authority_grant
        return merged

    def _authorize_action(
        self,
        *,
        pid: int,
        action: str,
        params: Optional[Dict[str, Any]],
        element_id: str = "",
        selector_scope: str = "",
    ) -> Tuple[Optional[AppActionResult], Dict[str, Any]]:
        checked_params = dict(params or {})
        app_name = self._app_name_for_pid(pid)
        risk = classify_action_risk(action, app_name)
        selector = selector_scope or str(
            checked_params.get("selectorScope")
            or checked_params.get("selector_scope")
            or element_id
            or ""
        )

        if not self._requires_authority_grant(action, risk, app_name):
            return None, checked_params

        grant_payload = checked_params.get(_UAB_AUTHORITY_GRANT_PARAM)
        if not grant_payload:
            reason_code = (
                "classification_required"
                if not self._classification_available(action, app_name)
                else "missing_authority_grant"
            )
            reason = (
                f"UAB provider classification required for action '{action}'"
                if reason_code == "classification_required"
                else f"UAB authority grant required for provider action '{action}'"
            )
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code=reason_code,
                reason=reason,
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params

        if not self.config.authority_grant_secret:
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code="missing_authority_secret",
                reason="UAB authority grant secret is not configured",
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params

        if app_name == "unknown":
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code="missing_app_classification",
                reason=f"UAB provider app classification unavailable for PID {pid}",
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params

        try:
            grant = UABAuthorityGrant.from_dict(grant_payload)
            validation = grant.validate(
                self.config.authority_grant_secret,
                app_name=app_name,
                app_pid=pid,
                action=action,
                selector_scope=selector or None,
            )
        except Exception as exc:
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code="invalid_authority_grant",
                reason=f"UAB authority grant rejected: {exc}",
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params

        if not validation.valid:
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code="invalid_authority_grant",
                reason=f"UAB authority grant rejected: {validation.reason}",
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params

        if grant.nonce in self._seen_authority_grant_nonces:
            return self._denied_action_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code="replayed_authority_grant",
                reason="UAB authority grant rejected: replayed nonce",
                app_name=app_name,
                element_id=element_id,
                selector_scope=selector,
            ), checked_params
        self._seen_authority_grant_nonces.add(grant.nonce)

        return None, checked_params

    def _authorize_dict_action(
        self,
        *,
        pid: int,
        action: str,
        params: Optional[Dict[str, Any]],
        selector_scope: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        denied, checked_params = self._authorize_action(
            pid=pid,
            action=action,
            params=params,
            selector_scope=selector_scope,
        )
        if denied is None:
            return None, checked_params
        event = denied.result_data["denial"] if isinstance(denied.result_data, dict) else {}
        return {"success": False, "error": denied.error_message, "denial": event}, checked_params

    def _authorize_read(
        self,
        *,
        pid: int,
        action: str,
        params: Optional[Dict[str, Any]],
        selector_scope: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        checked_params = dict(params or {})
        app_name = self._app_name_for_pid(pid)
        risk = classify_action_risk(action, app_name)
        if not self._read_requires_classification_or_grant(action, risk, app_name):
            return None, checked_params

        grant_payload = checked_params.get(_UAB_AUTHORITY_GRANT_PARAM)
        if not grant_payload:
            reason_code = (
                "classification_required"
                if not self._classification_available(action, app_name)
                else "missing_authority_grant"
            )
            reason = (
                f"UAB provider classification required for action '{action}'"
                if reason_code == "classification_required"
                else f"UAB authority grant required for provider action '{action}'"
            )
            return self._denied_dict_result(
                pid=pid,
                action=action,
                risk=risk,
                reason_code=reason_code,
                reason=reason,
                app_name=app_name,
                selector_scope=selector_scope,
            ), checked_params

        denied, authorized_params = self._authorize_dict_action(
            pid=pid,
            action=action,
            params=checked_params,
            selector_scope=selector_scope,
        )
        return denied, authorized_params

    def get_denial_events(self) -> List[Dict[str, Any]]:
        """Return provider-local denial events for the interim receipt/event path."""
        return list(self._denial_events)

    def _rpc_call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        """Make a JSON-RPC 2.0 call to the UAB daemon."""
        request_id = self.config.next_id
        self.config.next_id += 1

        payload = {
            "jsonrpc": self.config.rpc_version,
            "method": method,
            "params": params or {},
            "id": request_id,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config.daemon_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        effective_timeout = timeout or self.config.read_timeout_s

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if "error" in result and result["error"] is not None:
                error = result["error"]
                raise RuntimeError(
                    f"UAB RPC error {error.get('code', -1)}: {error.get('message', 'Unknown error')}"
                )

            return result.get("result")

        except urllib.error.HTTPError as e:
            error_body = _read_http_error_body(e, "UAB daemon HTTP response")
            raise ConnectionError(
                f"UAB daemon returned HTTP {e.code}: {error_body}"
            ) from e
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot reach UAB daemon at {self.config.daemon_url}: {e.reason}"
            ) from e
        except json.JSONDecodeError as e:
            raise ConnectionError(
                f"UAB daemon returned invalid JSON: {str(e)[:100]}"
            ) from e

    def health_check(self) -> ProviderHealth:
        """Check if the UAB daemon is reachable and operational."""
        try:
            info = self._normalize_status(
                self._rpc_call("getStatus", timeout=self.config.connect_timeout_s)
            )

            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version=info["version"],
                last_check=datetime.now(timezone.utc).isoformat(),
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=[],
                error_message=None,
                metadata={
                    "mode": "uab_bridge",
                    "daemon_url": self.config.daemon_url,
                    "connected_apps": info["connected_apps"],
                    "supported_frameworks": info["supported_frameworks"],
                    "transport": info["transport"],
                    "standalone_features": info["standalone_features"],
                },
            )
        except Exception as e:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="uab_bridge",
                last_check=datetime.now(timezone.utc).isoformat(),
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=[f"UAB daemon unreachable: {str(e)[:100]}"],
                error_message=str(e)[:200],
                metadata={
                    "mode": "uab_bridge",
                    "daemon_url": self.config.daemon_url,
                },
            )

    def summarize_health(self, health: Optional[ProviderHealth] = None) -> Dict[str, Any]:
        """Return the provider status fields used by gateway startup wiring."""
        snapshot = health or self.health_check()
        metadata = getattr(snapshot, "metadata", {}) or {}
        state = getattr(getattr(snapshot, "state", None), "value", "unknown")
        return {
            "state": state,
            "daemon_url": metadata.get("daemon_url") or self.config.daemon_url,
            "error": getattr(snapshot, "error_message", None),
        }

    def get_daemon_status(self) -> Dict[str, Any]:
        """Return normalized daemon status metadata."""
        try:
            return self._normalize_status(
                self._rpc_call("getStatus", timeout=self.config.connect_timeout_s)
            )
        except Exception as e:
            logger.warning("UAB status query failed: %s", e)
            return self._normalize_status(None)

    def detect(self) -> List[DetectedApp]:
        """Detect controllable desktop applications on the host."""
        try:
            result = self._rpc_call("detect")
            if not isinstance(result, list):
                return []

            apps = []
            for item in result:
                apps.append(DetectedApp(
                    pid=item.get("pid", 0),
                    name=item.get("name", "unknown"),
                    path=item.get("path"),
                    framework=item.get("framework", "unknown"),
                    confidence=item.get("confidence", 0.0),
                    window_title=item.get("windowTitle"),
                    connection_info=item.get("connectionInfo"),
                ))
            return apps

        except Exception as e:
            logger.warning("UAB detect failed: %s", e)
            return []

    def connect(self, target: Union[int, str]) -> ConnectionResult:
        """Connect to an application by PID or name."""
        try:
            params = {"pid": target} if isinstance(target, int) else {"name": target}
            result = self._rpc_call("connect", params)

            if not isinstance(result, dict):
                return ConnectionResult(success=False, error_message="Invalid response")

            pid = result.get("pid", 0)
            success = result.get("success", False)
            connection_method = result.get("connectionMethod") or result.get("method")

            if success:
                self._connected_apps[pid] = {
                    "name": result.get("name", "unknown"),
                    "framework": result.get("framework"),
                    "connection_method": connection_method,
                    "connected_at": datetime.now(timezone.utc).isoformat(),
                }

            return ConnectionResult(
                success=success,
                pid=pid,
                framework=result.get("framework"),
                connection_method=connection_method,
                error_message=result.get("error"),
            )

        except Exception as e:
            logger.warning("UAB connect failed: %s", e)
            return ConnectionResult(
                success=False,
                error_message=str(e)[:200],
            )

    def enumerate(self, pid: int) -> List[UIElement]:
        """Enumerate all UI elements in a connected application."""
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="enumerate",
            params={"pid": pid},
        )
        if denied is not None:
            logger.warning("UAB enumerate denied for PID %d: %s", pid, denied.get("error"))
            return []
        try:
            result = self._rpc_call("enumerate", checked_params)
            if not isinstance(result, list):
                return []

            return [self._parse_element(elem) for elem in result[:self.config.max_elements]]

        except Exception as e:
            logger.warning("UAB enumerate failed for PID %d: %s", pid, e)
            return []

    def query(self, pid: int, selector: Dict[str, Any]) -> List[UIElement]:
        """Search for UI elements matching a selector."""
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="query",
            params={"pid": pid, "selector": selector},
            selector_scope=json.dumps(selector, sort_keys=True),
        )
        if denied is not None:
            logger.warning("UAB query denied for PID %d: %s", pid, denied.get("error"))
            return []
        try:
            result = self._rpc_call("query", checked_params)
            if not isinstance(result, list):
                return []

            return [self._parse_element(elem) for elem in result[:self.config.max_elements]]

        except Exception as e:
            logger.warning("UAB query failed for PID %d: %s", pid, e)
            return []

    def act(
        self,
        pid: int,
        element_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Perform an action on a UI element."""
        start_time = time.time()
        denial, checked_params = self._authorize_action(
            pid=pid,
            action=action,
            params=params,
            element_id=element_id,
        )
        if denial is not None:
            denial.duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_app_action_result(
                denial,
                pid=pid,
                action=action,
                element_id=element_id,
                params=checked_params,
            )

        try:
            result = self._rpc_call("act", {
                "pid": pid,
                "elementId": element_id,
                "action": action,
                "params": checked_params,
            })

            duration_ms = int((time.time() - start_time) * 1000)

            if not isinstance(result, dict):
                action_result = AppActionResult(
                    success=False,
                    action=action,
                    element_id=element_id,
                    error_message="Invalid response from UAB daemon",
                    duration_ms=duration_ms,
                )
                return self._finalize_app_action_result(
                    action_result,
                    pid=pid,
                    action=action,
                    element_id=element_id,
                    params=checked_params,
                )

            action_result = AppActionResult(
                success=result.get("success", False),
                action=action,
                element_id=element_id,
                state_changes=result.get("stateChanges", []),
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )
            return self._finalize_app_action_result(
                action_result,
                pid=pid,
                action=action,
                element_id=element_id,
                params=checked_params,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning("UAB act failed for PID %d, element %s: %s", pid, element_id, e)
            action_result = AppActionResult(
                success=False,
                action=action,
                element_id=element_id,
                error_message=str(e)[:200],
                duration_ms=duration_ms,
            )
            return self._finalize_app_action_result(
                action_result,
                pid=pid,
                action=action,
                element_id=element_id,
                params=checked_params,
            )

    def state(self, pid: int) -> AppState:
        """Get current application state."""
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="state",
            params={"pid": pid},
        )
        if denied is not None:
            logger.warning("UAB state denied for PID %d: %s", pid, denied.get("error"))
            return AppState(pid=pid)
        try:
            result = self._rpc_call("state", checked_params)

            if not isinstance(result, dict):
                return AppState(pid=pid)

            return AppState(
                pid=pid,
                window_title=result.get("window", {}).get("title"),
                window_size=result.get("window", {}).get("size"),
                window_position=result.get("window", {}).get("position"),
                focused=result.get("window", {}).get("focused", False),
                active_element=result.get("activeElement"),
                modals=result.get("modals", []),
                menus=result.get("menus", []),
                clipboard=result.get("clipboard"),
            )

        except Exception as e:
            logger.warning("UAB state failed for PID %d: %s", pid, e)
            return AppState(pid=pid)

    def disconnect(self, pid: int) -> bool:
        """Disconnect from a connected application."""
        try:
            self._rpc_call("disconnect", {"pid": pid})
            self._connected_apps.pop(pid, None)
            return True
        except Exception as e:
            logger.warning("UAB disconnect failed for PID %d: %s", pid, e)
            return False

    def keypress(
        self,
        pid: int,
        key: str,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Send a single keypress to a connected app."""
        start_time = time.time()
        params = self._params_with_grant({"pid": pid, "key": key}, uab_authority_grant)
        denial, checked_params = self._authorize_action(
            pid=pid,
            action="keypress",
            params=params,
        )
        if denial is not None:
            denial.duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_app_action_result(
                denial,
                pid=pid,
                action="keypress",
                params=checked_params,
            )
        try:
            result = self._rpc_call("keypress", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return self._finalize_app_action_result(
                    AppActionResult(success=False, action="keypress",
                                    error_message="Invalid response", duration_ms=duration_ms),
                    pid=pid,
                    action="keypress",
                    params=checked_params,
                )
            return self._finalize_app_action_result(AppActionResult(
                success=result.get("success", False), action="keypress",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            ), pid=pid, action="keypress", params=checked_params)
        except Exception as e:
            return self._finalize_app_action_result(
                AppActionResult(success=False, action="keypress",
                                error_message=str(e)[:200],
                                duration_ms=int((time.time() - start_time) * 1000)),
                pid=pid,
                action="keypress",
                params=checked_params,
            )

    def hotkey(
        self,
        pid: int,
        keys: List[str],
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Send a hotkey combination (e.g., ['ctrl', 's'])."""
        start_time = time.time()
        params = self._params_with_grant({"pid": pid, "keys": keys}, uab_authority_grant)
        denial, checked_params = self._authorize_action(
            pid=pid,
            action="hotkey",
            params=params,
        )
        if denial is not None:
            denial.duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_app_action_result(
                denial,
                pid=pid,
                action="hotkey",
                params=checked_params,
            )
        try:
            result = self._rpc_call("hotkey", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return self._finalize_app_action_result(
                    AppActionResult(success=False, action="hotkey",
                                    error_message="Invalid response", duration_ms=duration_ms),
                    pid=pid,
                    action="hotkey",
                    params=checked_params,
                )
            return self._finalize_app_action_result(AppActionResult(
                success=result.get("success", False), action="hotkey",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            ), pid=pid, action="hotkey", params=checked_params)
        except Exception as e:
            return self._finalize_app_action_result(
                AppActionResult(success=False, action="hotkey",
                                error_message=str(e)[:200],
                                duration_ms=int((time.time() - start_time) * 1000)),
                pid=pid,
                action="hotkey",
                params=checked_params,
            )

    def _window_action(
        self,
        method: str,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
        **extra,
    ) -> AppActionResult:
        """Internal helper for window management RPC calls."""
        start_time = time.time()
        params = self._params_with_grant({"pid": pid, **extra}, uab_authority_grant)
        denial, checked_params = self._authorize_action(
            pid=pid,
            action=method,
            params=params,
        )
        if denial is not None:
            denial.duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_app_action_result(
                denial,
                pid=pid,
                action=method,
                params=checked_params,
            )
        try:
            result = self._rpc_call(method, checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return self._finalize_app_action_result(
                    AppActionResult(success=False, action=method,
                                    error_message="Invalid response", duration_ms=duration_ms),
                    pid=pid,
                    action=method,
                    params=checked_params,
                )
            return self._finalize_app_action_result(AppActionResult(
                success=result.get("success", False), action=method,
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            ), pid=pid, action=method, params=checked_params)
        except Exception as e:
            return self._finalize_app_action_result(
                AppActionResult(success=False, action=method,
                                error_message=str(e)[:200],
                                duration_ms=int((time.time() - start_time) * 1000)),
                pid=pid,
                action=method,
                params=checked_params,
            )

    def minimize(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Minimize a window."""
        if uab_authority_grant is None:
            return self._window_action("minimize", pid)
        return self._window_action("minimize", pid, uab_authority_grant=uab_authority_grant)

    def maximize(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Maximize a window."""
        if uab_authority_grant is None:
            return self._window_action("maximize", pid)
        return self._window_action("maximize", pid, uab_authority_grant=uab_authority_grant)

    def restore(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Restore a window from min/max."""
        if uab_authority_grant is None:
            return self._window_action("restore", pid)
        return self._window_action("restore", pid, uab_authority_grant=uab_authority_grant)

    def close_window(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Close a window gracefully (HIGH risk)."""
        if uab_authority_grant is None:
            return self._window_action("closeWindow", pid)
        return self._window_action("closeWindow", pid, uab_authority_grant=uab_authority_grant)

    def move_window(
        self,
        pid: int,
        x: int,
        y: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Move a window to (x, y)."""
        if uab_authority_grant is None:
            return self._window_action("moveWindow", pid, x=x, y=y)
        return self._window_action("moveWindow", pid, uab_authority_grant=uab_authority_grant, x=x, y=y)

    def resize_window(
        self,
        pid: int,
        width: int,
        height: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Resize a window to (width, height)."""
        if uab_authority_grant is None:
            return self._window_action("resizeWindow", pid, width=width, height=height)
        return self._window_action(
            "resizeWindow",
            pid,
            uab_authority_grant=uab_authority_grant,
            width=width,
            height=height,
        )

    def screenshot(
        self,
        pid: int,
        output_path: Optional[str] = None,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Capture a screenshot of a connected app's window."""
        start_time = time.time()
        params = self._params_with_grant({"pid": pid}, uab_authority_grant)
        if output_path:
            params["outputPath"] = output_path
        denial, checked_params = self._authorize_action(
            pid=pid,
            action="screenshot",
            params=params,
        )
        if denial is not None:
            denial.duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_app_action_result(
                denial,
                pid=pid,
                action="screenshot",
                params=checked_params,
            )
        try:
            result = self._rpc_call("screenshot", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return self._finalize_app_action_result(
                    AppActionResult(success=False, action="screenshot",
                                    error_message="Invalid response", duration_ms=duration_ms),
                    pid=pid,
                    action="screenshot",
                    params=checked_params,
                )
            return self._finalize_app_action_result(AppActionResult(
                success=result.get("success", False), action="screenshot",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            ), pid=pid, action="screenshot", params=checked_params)
        except Exception as e:
            return self._finalize_app_action_result(
                AppActionResult(success=False, action="screenshot",
                                error_message=str(e)[:200],
                                duration_ms=int((time.time() - start_time) * 1000)),
                pid=pid,
                action="screenshot",
                params=checked_params,
            )

    def execute_chain(self, chain_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a multi-step action chain. Returns ChainResult dict."""
        start_time = time.time()
        pid = int(chain_definition.get("pid") or 0)
        denied, checked_params = self._authorize_dict_action(
            pid=pid,
            action="chain",
            params=chain_definition,
        )
        if denied is not None:
            return self._finalize_dict_action_result(
                denied,
                pid=pid,
                action="chain",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
            )
        try:
            result = self._rpc_call("chain", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_dict_action_result(
                result if isinstance(result, dict) else {"success": False, "error": "Invalid response"},
                pid=pid,
                action="chain",
                params=checked_params,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.warning("UAB chain execution failed: %s", e)
            return self._finalize_dict_action_result(
                {"success": False, "error": str(e)[:200]},
                pid=pid,
                action="chain",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
            )

    def read_document(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Read document content from Word/document app."""
        return self.act(pid, "", "readDocument", self._params_with_grant(None, uab_authority_grant))

    def read_cell(
        self,
        pid: int,
        row: int,
        col: int,
        sheet: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Read a single Excel cell."""
        params: Dict[str, Any] = {"row": row, "col": col}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "readCell", self._params_with_grant(params, uab_authority_grant))

    def write_cell(
        self,
        pid: int,
        row: int,
        col: int,
        value: str,
        sheet: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Write to a single Excel cell."""
        params: Dict[str, Any] = {"row": row, "col": col, "text": value}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "writeCell", self._params_with_grant(params, uab_authority_grant))

    def read_range(
        self,
        pid: int,
        cell_range: str,
        sheet: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Read an Excel range (e.g., 'A1:B5')."""
        params: Dict[str, Any] = {"cellRange": cell_range}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "readRange", self._params_with_grant(params, uab_authority_grant))

    def write_range(
        self,
        pid: int,
        cell_range: str,
        values: List[List[str]],
        sheet: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Write to an Excel range."""
        params: Dict[str, Any] = {"cellRange": cell_range, "values": values}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "writeRange", self._params_with_grant(params, uab_authority_grant))

    def get_sheets(self, pid: int) -> AppActionResult:
        """Get list of sheets in an Excel workbook."""
        return self.act(pid, "", "getSheets")

    def read_emails(
        self,
        pid: int,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Read emails from Outlook."""
        return self.act(pid, "", "readEmails", self._params_with_grant(None, uab_authority_grant))

    def compose_email(
        self,
        pid: int,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Compose an email in Outlook (does NOT send)."""
        params: Dict[str, Any] = {"to": to, "subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        return self.act(pid, "", "composeEmail", self._params_with_grant(params, uab_authority_grant))

    def send_email(
        self,
        pid: int,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Compose and send an email (HIGH risk; irreversible)."""
        params: Dict[str, Any] = {"to": to, "subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        return self.act(pid, "", "sendEmail", self._params_with_grant(params, uab_authority_grant))

    def get_health_summary(self) -> List[Dict[str, Any]]:
        """Get connection health summary from UAB daemon."""
        try:
            result = self._rpc_call("health")
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB health summary failed: %s", e)
            return []

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get element cache statistics from UAB daemon."""
        try:
            result = self._rpc_call("cacheStats")
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.warning("UAB cache stats failed: %s", e)
            return {}

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent permission audit log from UAB daemon."""
        try:
            result = self._rpc_call("auditLog", {"limit": limit})
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB audit log failed: %s", e)
            return []

    def spatial_map(self, pid: int, format: str = "detailed") -> Dict[str, Any]:
        """Get a spatial map of an app's UI (rows, grid, text content).

        Structured data is preferred over screenshots when the route can expose
        enough UI state.

        Args:
            pid: Process ID of connected app.
            format: 'detailed', 'compact', or 'json'.

        Returns:
            CompositeResult dict with spatialMap, timing, textContent.
        """
        params = {"pid": pid, "options": {"mapFormat": format}}
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="spatialMap",
            params=params,
        )
        if denied is not None:
            return denied
        try:
            result = self._rpc_call("spatialMap", checked_params)
            return result if isinstance(result, dict) else {"error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB spatialMap failed: %s", e)
            return {"error": str(e)[:200]}

    def text_map(self, pid: int, format: str = "detailed") -> Dict[str, Any]:
        """Get a text-based UI map for AI consumption (replaces screenshots).

        Args:
            pid: Process ID of connected app.
            format: 'detailed', 'compact', or 'json'.

        Returns:
            Dict with 'text' (the map string) and 'timing' (ms).
        """
        params = {"pid": pid, "format": format}
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="textMap",
            params=params,
        )
        if denied is not None:
            return denied
        try:
            result = self._rpc_call("textMap", checked_params)
            return result if isinstance(result, dict) else {"error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB textMap failed: %s", e)
            return {"error": str(e)[:200]}

    def find_by_description(self, pid: int, description: str) -> List[Dict[str, Any]]:
        """Find UI elements by natural language description using spatial map.

        Args:
            pid: Process ID of connected app.
            description: Natural language description of the element (e.g., "Save button").

        Returns:
            List of matching SpatialElement dicts.
        """
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="findByDescription",
            params={"pid": pid, "description": description},
            selector_scope=description,
        )
        if denied is not None:
            logger.warning("UAB findByDescription denied: %s", denied.get("error"))
            return []
        try:
            result = self._rpc_call("findByDescription", checked_params)
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB findByDescription failed: %s", e)
            return []

    def focused(self, pid: int) -> Dict[str, Any]:
        """Get the currently focused element for a window."""
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="focused",
            params={"pid": pid},
        )
        if denied is not None:
            return denied
        try:
            result = self._rpc_call("focused", checked_params)
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.warning("UAB focused failed: %s", e)
            return {"error": str(e)[:200]}

    def find_by_path(
        self,
        pid: int,
        *,
        path: Optional[List[str]] = None,
        name: Optional[str] = None,
        parent: Optional[str] = None,
        element_type: Optional[str] = None,
        occurrence: Optional[Union[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Find UI elements by path or parent context."""
        params: Dict[str, Any] = {"pid": pid}
        if path:
            params["path"] = path
        if name:
            params["name"] = name
        if parent:
            params["parent"] = parent
        if element_type:
            params["type"] = element_type
        if occurrence is not None:
            params["occurrence"] = occurrence

        denied, checked_params = self._authorize_read(
            pid=pid,
            action="findByPath",
            params=params,
            selector_scope=json.dumps(params, sort_keys=True),
        )
        if denied is not None:
            logger.warning("UAB findByPath denied: %s", denied.get("error"))
            return []

        try:
            result = self._rpc_call("findByPath", checked_params)
            if isinstance(result, dict):
                elements = result.get("elements", [])
                return elements if isinstance(elements, list) else []
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB findByPath failed: %s", e)
            return []

    def watch_changes(
        self,
        pid: int,
        duration_ms: int = 3000,
        poll_ms: int = 200,
    ) -> List[Dict[str, Any]]:
        """Watch for state changes on a window over a short interval."""
        params = {"pid": pid, "durationMs": duration_ms, "pollMs": poll_ms}
        denied, checked_params = self._authorize_read(
            pid=pid,
            action="watchChanges",
            params=params,
        )
        if denied is not None:
            logger.warning("UAB watchChanges denied: %s", denied.get("error"))
            return []
        try:
            result = self._rpc_call(
                "watchChanges",
                checked_params,
                timeout=max(self.config.read_timeout_s, int(duration_ms / 1000) + 5),
            )
            if isinstance(result, dict):
                events = result.get("events", [])
                return events if isinstance(events, list) else []
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB watchChanges failed: %s", e)
            return []

    def atomic_chain(
        self,
        pid: int,
        steps: List[Dict[str, Any]],
        label: str = "atomic-chain",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an atomic multi-step input chain."""
        start_time = time.time()
        params = self._params_with_grant(
            {"pid": pid, "steps": steps, "label": label},
            uab_authority_grant,
        )
        denied, checked_params = self._authorize_dict_action(
            pid=pid,
            action="atomicChain",
            params=params,
        )
        if denied is not None:
            return self._finalize_dict_action_result(
                denied,
                pid=pid,
                action="atomicChain",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
                selector_scope=label,
            )
        try:
            result = self._rpc_call("atomicChain", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_dict_action_result(
                result if isinstance(result, dict) else {"success": False, "error": "Invalid response"},
                pid=pid,
                action="atomicChain",
                params=checked_params,
                duration_ms=duration_ms,
                selector_scope=label,
            )
        except Exception as e:
            logger.warning("UAB atomicChain failed: %s", e)
            return self._finalize_dict_action_result(
                {"success": False, "error": str(e)[:200]},
                pid=pid,
                action="atomicChain",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
                selector_scope=label,
            )

    def smart_invoke(
        self,
        pid: int,
        name: str,
        *,
        parent: Optional[str] = None,
        element_type: Optional[str] = None,
        occurrence: Optional[Union[str, int]] = None,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Invoke an element using the daemon's best-effort strategy."""
        start_time = time.time()
        params: Dict[str, Any] = {"pid": pid, "name": name}
        if parent:
            params["parent"] = parent
        if element_type:
            params["type"] = element_type
        if occurrence is not None:
            params["occurrence"] = occurrence
        params = self._params_with_grant(params, uab_authority_grant)
        denied, checked_params = self._authorize_dict_action(
            pid=pid,
            action="smartInvoke",
            params=params,
            selector_scope=name,
        )
        if denied is not None:
            return self._finalize_dict_action_result(
                denied,
                pid=pid,
                action="smartInvoke",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
                selector_scope=name,
            )

        try:
            result = self._rpc_call("smartInvoke", checked_params)
            duration_ms = int((time.time() - start_time) * 1000)
            return self._finalize_dict_action_result(
                result if isinstance(result, dict) else {"success": False, "error": "Invalid response"},
                pid=pid,
                action="smartInvoke",
                params=checked_params,
                duration_ms=duration_ms,
                selector_scope=name,
            )
        except Exception as e:
            logger.warning("UAB smartInvoke failed: %s", e)
            return self._finalize_dict_action_result(
                {"success": False, "error": str(e)[:200]},
                pid=pid,
                action="smartInvoke",
                params=checked_params,
                duration_ms=int((time.time() - start_time) * 1000),
                selector_scope=name,
            )

    def navigate(
        self,
        pid: int,
        url: str,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Navigate browser to a URL."""
        return self.act(pid, "", "navigate", self._params_with_grant({"url": url}, uab_authority_grant))

    def get_tabs(self, pid: int) -> AppActionResult:
        """List all browser tabs."""
        return self.act(pid, "", "getTabs")

    def switch_tab(
        self,
        pid: int,
        tab_id: str,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Switch to a browser tab by ID or index."""
        return self.act(pid, "", "switchTab", self._params_with_grant({"tabId": tab_id}, uab_authority_grant))

    def execute_script(
        self,
        pid: int,
        script: str,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Execute JavaScript in the browser."""
        return self.act(pid, "", "executeScript", self._params_with_grant({"script": script}, uab_authority_grant))

    def get_cookies(
        self,
        pid: int,
        url: str = "",
        domain: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Get browser cookies, optionally filtered by URL or domain."""
        params: Dict[str, Any] = {}
        if url:
            params["url"] = url
        if domain:
            params["domain"] = domain
        return self.act(pid, "", "getCookies", self._params_with_grant(params, uab_authority_grant))

    def set_cookie(self, pid: int, name: str, value: str,
                   domain: str = "", url: str = "",
                   uab_authority_grant: Optional[Dict[str, Any]] = None) -> AppActionResult:
        """Set a browser cookie."""
        params: Dict[str, Any] = {"cookieName": name, "cookieValue": value}
        if domain:
            params["domain"] = domain
        if url:
            params["url"] = url
        return self.act(pid, "", "setCookie", self._params_with_grant(params, uab_authority_grant))

    def get_local_storage(
        self,
        pid: int,
        key: str = "",
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Get localStorage value(s) from browser."""
        params: Dict[str, Any] = {}
        if key:
            params["storageKey"] = key
        return self.act(pid, "", "getLocalStorage", self._params_with_grant(params, uab_authority_grant))

    def set_local_storage(
        self,
        pid: int,
        key: str,
        value: str,
        uab_authority_grant: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """Set a localStorage value in the browser."""
        return self.act(
            pid,
            "",
            "setLocalStorage",
            self._params_with_grant(
                {"storageKey": key, "storageValue": value},
                uab_authority_grant,
            ),
        )

    def _parse_element(self, data: Dict[str, Any], depth: int = 0) -> UIElement:
        """Parse a raw UAB element dict into a UIElement, with depth limit."""
        children = []
        if depth < self.config.max_element_depth:
            for child in data.get("children", []):
                children.append(self._parse_element(child, depth + 1))

        return UIElement(
            id=data.get("id", ""),
            type=data.get("type", "unknown"),
            label=data.get("label"),
            properties=data.get("properties", {}),
            bounds=data.get("bounds"),
            children=children,
            actions=data.get("actions", []),
            visible=data.get("visible", True),
            enabled=data.get("enabled", True),
            meta=data.get("meta"),
        )

    def get_connected_apps(self) -> Dict[int, Dict[str, Any]]:
        """Return locally tracked connected apps (for War Room panel)."""
        if self._connected_apps:
            return dict(self._connected_apps)

        status = self.get_daemon_status()
        connections = status.get("connections", [])
        result: Dict[int, Dict[str, Any]] = {}
        for item in connections:
            pid = item.get("pid", 0)
            if not pid:
                continue
            result[pid] = {
                "name": item.get("name", "unknown"),
                "framework": item.get("framework"),
                "connection_method": item.get("connection_method"),
                "element_count": item.get("element_count", 0),
                "window_title": item.get("window_title"),
            }
        return result

    def get_app_name(self, pid: int) -> str:
        """Get the name of a connected app by PID."""
        info = self._connected_apps.get(pid, {})
        return info.get("name", "unknown")
