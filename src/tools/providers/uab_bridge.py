# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183
#
# This module bridges to the Universal App Bridge (UAB) daemon.
# UAB itself is licensed separately under MIT — see packages/uab/LICENSE.

"""
UABProvider — Universal App Bridge Desktop App Control (v0.5.0)
================================================================

Framework-level desktop application control via the UAB daemon.
Hooks into 7 framework plugins to give Lancelot structured, reliable
access to any desktop app's interface.

Gated by: FEATURE_TOOLS_UAB (default: false)
Requires: FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_HOST_BRIDGE

Architecture:
    Container (Lancelot)
        |-- HTTP --> host.docker.internal:7900 (UAB daemon, JSON-RPC 2.0)
                         |-- CDP ------------> Electron apps
                         |-- Win-UIA --------> WPF, WinForms, native Win32 (fallback)
                         |-- Qt UIA ---------> Qt5/Qt6 apps
                         |-- GTK UIA --------> GTK3/GTK4 apps
                         |-- JAB → UIA ------> Java Swing/JavaFX apps
                         |-- Flutter UIA ----> Flutter Windows apps
                         |-- Office UIA -----> Word, Excel, PowerPoint, Outlook

Capabilities (v0.5.0):
    - Smart element caching with TTL (5s tree, 3s query, 2s state)
    - Connection health monitoring + auto-reconnect
    - Permission model with risk levels + audit log
    - Action chains for multi-step workflows
    - Retry with exponential backoff on transient errors
    - Office document operations (read/write cells, documents, emails)
    - Window management (minimize, maximize, move, resize, screenshot)
    - Keyboard input (keypress, hotkey combos)

Security model:
    - UAB daemon runs on host machine (started from packages/uab/)
    - All actions produce AppControl receipts for full audit trail
    - Read-only operations (detect, enumerate, query, state) = LOW risk
    - Mutating operations (click, type, select, keypress) = MEDIUM risk
    - Destructive operations (close, invoke, sendEmail) = HIGH risk
    - PolicyEngine evaluates all actions before execution
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
from typing import Any, Dict, List, Optional, Union

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

logger = logging.getLogger(__name__)


# =============================================================================
# Risk Classification for UAB Actions
# =============================================================================

# Read-only actions — no state change, safe to run autonomously
_READ_ONLY_ACTIONS = frozenset({
    "detect", "enumerate", "query", "state",
    "screenshot",
    # Office read operations
    "readDocument", "readCell", "readRange",
    "getSheets", "readFormula",
    "readSlides", "readSlideText",
    "readEmails",
})

# Mutating actions — change UI state, governed by Soul posture
_MUTATING_ACTIONS = frozenset({
    "click", "doubleclick", "rightclick", "type", "clear",
    "select", "scroll", "focus", "hover", "expand", "collapse",
    "check", "uncheck", "toggle", "keypress", "hotkey",
    "contextmenu",
    # Office write operations
    "writeCell", "writeRange",
    "composeEmail",
})

# Destructive actions — high risk, always require approval
_DESTRUCTIVE_ACTIONS = frozenset({
    "close", "invoke", "minimize", "maximize", "restore",
    "move", "resize",
    "sendEmail",  # irreversible
})

# Sensitive app patterns — auto-escalate risk when detected
_SENSITIVE_APP_PATTERNS = frozenset({
    "1password", "bitwarden", "keepass", "lastpass",    # password managers
    "bank", "chase", "wells fargo", "capital one",      # banking
    "venmo", "paypal", "stripe",                        # financial
    "outlook", "thunderbird", "gmail",                   # email
    "terminal", "powershell", "cmd",                     # shells
})


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

    return RiskLevel.LOW


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class UABConfig:
    """Configuration for the UAB provider."""

    # Daemon connection — UAB runs on the host, we reach it via Host Bridge
    daemon_url: str = ""
    connect_timeout_s: int = 5
    read_timeout_s: int = 30

    # JSON-RPC settings
    rpc_version: str = "2.0"
    next_id: int = 1

    # Output limits
    max_elements: int = 5000
    max_element_depth: int = 20

    def __post_init__(self):
        if not self.daemon_url:
            self.daemon_url = os.environ.get(
                "UAB_DAEMON_URL", "http://host.docker.internal:7900"
            )


# =============================================================================
# UABProvider
# =============================================================================


class UABProvider(BaseProvider):
    """
    Universal App Bridge provider — framework-level desktop app control.

    Communicates with the UAB daemon via JSON-RPC 2.0 over TCP to detect,
    connect, enumerate, query, and act on desktop applications.
    """

    def __init__(self, config: Optional[UABConfig] = None):
        self.config = config or UABConfig()
        self._connected_apps: Dict[int, Dict[str, Any]] = {}

    @property
    def provider_id(self) -> str:
        return "uab_bridge"

    @property
    def capabilities(self) -> List[Capability]:
        return [Capability.APP_CONTROL]

    # =========================================================================
    # JSON-RPC Communication
    # =========================================================================

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
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
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

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """Check if the UAB daemon is reachable and operational."""
        try:
            # Try a lightweight RPC call
            info = self._rpc_call("getStatus", timeout=self.config.connect_timeout_s)

            connected_count = 0
            frameworks = []
            if isinstance(info, dict):
                connected_count = info.get("connectedApps", 0)
                frameworks = info.get("supportedFrameworks", [])

            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version=info.get("version", "unknown") if isinstance(info, dict) else "unknown",
                last_check=datetime.now(timezone.utc).isoformat(),
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=[],
                error_message=None,
                metadata={
                    "mode": "uab_bridge",
                    "daemon_url": self.config.daemon_url,
                    "connected_apps": connected_count,
                    "supported_frameworks": frameworks,
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

    # =========================================================================
    # AppControl Capability
    # =========================================================================

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

            if success:
                self._connected_apps[pid] = {
                    "name": result.get("name", "unknown"),
                    "framework": result.get("framework"),
                    "connection_method": result.get("connectionMethod"),
                    "connected_at": datetime.now(timezone.utc).isoformat(),
                }

            return ConnectionResult(
                success=success,
                pid=pid,
                framework=result.get("framework"),
                connection_method=result.get("connectionMethod"),
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
        try:
            result = self._rpc_call("enumerate", {"pid": pid})
            if not isinstance(result, list):
                return []

            return [self._parse_element(elem) for elem in result[:self.config.max_elements]]

        except Exception as e:
            logger.warning("UAB enumerate failed for PID %d: %s", pid, e)
            return []

    def query(self, pid: int, selector: Dict[str, Any]) -> List[UIElement]:
        """Search for UI elements matching a selector."""
        try:
            result = self._rpc_call("query", {"pid": pid, "selector": selector})
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

        try:
            result = self._rpc_call("act", {
                "pid": pid,
                "elementId": element_id,
                "action": action,
                "params": params or {},
            })

            duration_ms = int((time.time() - start_time) * 1000)

            if not isinstance(result, dict):
                return AppActionResult(
                    success=False,
                    action=action,
                    element_id=element_id,
                    error_message="Invalid response from UAB daemon",
                    duration_ms=duration_ms,
                )

            return AppActionResult(
                success=result.get("success", False),
                action=action,
                element_id=element_id,
                state_changes=result.get("stateChanges", []),
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning("UAB act failed for PID %d, element %s: %s", pid, element_id, e)
            return AppActionResult(
                success=False,
                action=action,
                element_id=element_id,
                error_message=str(e)[:200],
                duration_ms=duration_ms,
            )

    def state(self, pid: int) -> AppState:
        """Get current application state."""
        try:
            result = self._rpc_call("state", {"pid": pid})

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

    # =========================================================================
    # v0.5.0: Disconnect
    # =========================================================================

    def disconnect(self, pid: int) -> bool:
        """Disconnect from a connected application."""
        try:
            self._rpc_call("disconnect", {"pid": pid})
            self._connected_apps.pop(pid, None)
            return True
        except Exception as e:
            logger.warning("UAB disconnect failed for PID %d: %s", pid, e)
            return False

    # =========================================================================
    # v0.5.0: Keyboard Input
    # =========================================================================

    def keypress(self, pid: int, key: str) -> AppActionResult:
        """Send a single keypress to a connected app."""
        start_time = time.time()
        try:
            result = self._rpc_call("keypress", {"pid": pid, "key": key})
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return AppActionResult(success=False, action="keypress",
                                       error_message="Invalid response", duration_ms=duration_ms)
            return AppActionResult(
                success=result.get("success", False), action="keypress",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )
        except Exception as e:
            return AppActionResult(success=False, action="keypress",
                                   error_message=str(e)[:200],
                                   duration_ms=int((time.time() - start_time) * 1000))

    def hotkey(self, pid: int, keys: List[str]) -> AppActionResult:
        """Send a hotkey combination (e.g., ['ctrl', 's'])."""
        start_time = time.time()
        try:
            result = self._rpc_call("hotkey", {"pid": pid, "keys": keys})
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return AppActionResult(success=False, action="hotkey",
                                       error_message="Invalid response", duration_ms=duration_ms)
            return AppActionResult(
                success=result.get("success", False), action="hotkey",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )
        except Exception as e:
            return AppActionResult(success=False, action="hotkey",
                                   error_message=str(e)[:200],
                                   duration_ms=int((time.time() - start_time) * 1000))

    # =========================================================================
    # v0.5.0: Window Management
    # =========================================================================

    def _window_action(self, method: str, pid: int, **extra) -> AppActionResult:
        """Internal helper for window management RPC calls."""
        start_time = time.time()
        try:
            params: Dict[str, Any] = {"pid": pid, **extra}
            result = self._rpc_call(method, params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return AppActionResult(success=False, action=method,
                                       error_message="Invalid response", duration_ms=duration_ms)
            return AppActionResult(
                success=result.get("success", False), action=method,
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )
        except Exception as e:
            return AppActionResult(success=False, action=method,
                                   error_message=str(e)[:200],
                                   duration_ms=int((time.time() - start_time) * 1000))

    def minimize(self, pid: int) -> AppActionResult:
        """Minimize a window."""
        return self._window_action("minimize", pid)

    def maximize(self, pid: int) -> AppActionResult:
        """Maximize a window."""
        return self._window_action("maximize", pid)

    def restore(self, pid: int) -> AppActionResult:
        """Restore a window from min/max."""
        return self._window_action("restore", pid)

    def close_window(self, pid: int) -> AppActionResult:
        """Close a window gracefully (HIGH risk)."""
        return self._window_action("closeWindow", pid)

    def move_window(self, pid: int, x: int, y: int) -> AppActionResult:
        """Move a window to (x, y)."""
        return self._window_action("moveWindow", pid, x=x, y=y)

    def resize_window(self, pid: int, width: int, height: int) -> AppActionResult:
        """Resize a window to (width, height)."""
        return self._window_action("resizeWindow", pid, width=width, height=height)

    # =========================================================================
    # v0.5.0: Screenshot
    # =========================================================================

    def screenshot(self, pid: int, output_path: Optional[str] = None) -> AppActionResult:
        """Capture a screenshot of a connected app's window."""
        start_time = time.time()
        try:
            params: Dict[str, Any] = {"pid": pid}
            if output_path:
                params["outputPath"] = output_path
            result = self._rpc_call("screenshot", params)
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(result, dict):
                return AppActionResult(success=False, action="screenshot",
                                       error_message="Invalid response", duration_ms=duration_ms)
            return AppActionResult(
                success=result.get("success", False), action="screenshot",
                error_message=result.get("error"),
                duration_ms=result.get("durationMs", duration_ms),
                result_data=result.get("result"),
            )
        except Exception as e:
            return AppActionResult(success=False, action="screenshot",
                                   error_message=str(e)[:200],
                                   duration_ms=int((time.time() - start_time) * 1000))

    # =========================================================================
    # v0.5.0: Action Chains
    # =========================================================================

    def execute_chain(self, chain_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a multi-step action chain. Returns ChainResult dict."""
        try:
            result = self._rpc_call("chain", chain_definition)
            return result if isinstance(result, dict) else {"success": False, "error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB chain execution failed: %s", e)
            return {"success": False, "error": str(e)[:200]}

    # =========================================================================
    # v0.5.0: Office Operations (via act() with specialized action types)
    # =========================================================================

    def read_document(self, pid: int) -> AppActionResult:
        """Read document content from Word/document app."""
        return self.act(pid, "", "readDocument")

    def read_cell(self, pid: int, row: int, col: int, sheet: str = "") -> AppActionResult:
        """Read a single Excel cell."""
        params: Dict[str, Any] = {"row": row, "col": col}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "readCell", params)

    def write_cell(self, pid: int, row: int, col: int, value: str, sheet: str = "") -> AppActionResult:
        """Write to a single Excel cell."""
        params: Dict[str, Any] = {"row": row, "col": col, "text": value}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "writeCell", params)

    def read_range(self, pid: int, cell_range: str, sheet: str = "") -> AppActionResult:
        """Read an Excel range (e.g., 'A1:B5')."""
        params: Dict[str, Any] = {"cellRange": cell_range}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "readRange", params)

    def write_range(self, pid: int, cell_range: str, values: List[List[str]], sheet: str = "") -> AppActionResult:
        """Write to an Excel range."""
        params: Dict[str, Any] = {"cellRange": cell_range, "values": values}
        if sheet:
            params["sheet"] = sheet
        return self.act(pid, "", "writeRange", params)

    def get_sheets(self, pid: int) -> AppActionResult:
        """Get list of sheets in an Excel workbook."""
        return self.act(pid, "", "getSheets")

    def read_emails(self, pid: int) -> AppActionResult:
        """Read emails from Outlook."""
        return self.act(pid, "", "readEmails")

    def compose_email(self, pid: int, to: str, subject: str, body: str, cc: str = "") -> AppActionResult:
        """Compose an email in Outlook (does NOT send)."""
        params: Dict[str, Any] = {"to": to, "subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        return self.act(pid, "", "composeEmail", params)

    def send_email(self, pid: int, to: str, subject: str, body: str, cc: str = "") -> AppActionResult:
        """Compose and send an email (HIGH risk — irreversible)."""
        params: Dict[str, Any] = {"to": to, "subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        return self.act(pid, "", "sendEmail", params)

    # =========================================================================
    # v0.5.0: Diagnostics
    # =========================================================================

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

    # =========================================================================
    # Helpers
    # =========================================================================

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
        return dict(self._connected_apps)

    def get_app_name(self, pid: int) -> str:
        """Get the name of a connected app by PID."""
        info = self._connected_apps.get(pid, {})
        return info.get("name", "unknown")
