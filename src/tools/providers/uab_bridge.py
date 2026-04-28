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

    required_keys = (
        "read_only",
        "mutating",
        "destructive",
        "sensitive_app_patterns",
    )
    for key in required_keys:
        if not isinstance(manifest.get(key), list):
            raise RuntimeError(
                f"UAB action risk manifest missing array: {key}"
            )
    return manifest


_ACTION_RISK_MANIFEST = _load_action_risk_manifest()
_READ_ONLY_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["read_only"])
_MUTATING_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["mutating"])
_DESTRUCTIVE_ACTIONS = frozenset(_ACTION_RISK_MANIFEST["destructive"])
_SENSITIVE_APP_PATTERNS = frozenset(
    _ACTION_RISK_MANIFEST["sensitive_app_patterns"]
)


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

    return RiskLevel.LOW

@dataclass
class UABConfig:
    """Configuration for the UAB provider."""

    daemon_url: str = ""
    connect_timeout_s: int = 5
    read_timeout_s: int = 30

    rpc_version: str = "2.0"
    next_id: int = 1

    max_elements: int = 5000
    max_element_depth: int = 20

    def __post_init__(self):
        if not self.daemon_url:
            self.daemon_url = os.environ.get(
                "UAB_DAEMON_URL", "http://host.docker.internal:7900"
            )

class UABProvider(BaseProvider):
    """
    Universal App Bridge provider for framework-level desktop app control.

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

    def disconnect(self, pid: int) -> bool:
        """Disconnect from a connected application."""
        try:
            self._rpc_call("disconnect", {"pid": pid})
            self._connected_apps.pop(pid, None)
            return True
        except Exception as e:
            logger.warning("UAB disconnect failed for PID %d: %s", pid, e)
            return False

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

    def execute_chain(self, chain_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a multi-step action chain. Returns ChainResult dict."""
        try:
            result = self._rpc_call("chain", chain_definition)
            return result if isinstance(result, dict) else {"success": False, "error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB chain execution failed: %s", e)
            return {"success": False, "error": str(e)[:200]}

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
        """Compose and send an email (HIGH risk; irreversible)."""
        params: Dict[str, Any] = {"to": to, "subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        return self.act(pid, "", "sendEmail", params)

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
        try:
            result = self._rpc_call("spatialMap", {"pid": pid, "options": {"mapFormat": format}})
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
        try:
            result = self._rpc_call("textMap", {"pid": pid, "format": format})
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
        try:
            result = self._rpc_call("findByDescription", {"pid": pid, "description": description})
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("UAB findByDescription failed: %s", e)
            return []

    def focused(self, pid: int) -> Dict[str, Any]:
        """Get the currently focused element for a window."""
        try:
            result = self._rpc_call("focused", {"pid": pid})
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

        try:
            result = self._rpc_call("findByPath", params)
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
        try:
            result = self._rpc_call(
                "watchChanges",
                {"pid": pid, "durationMs": duration_ms, "pollMs": poll_ms},
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
    ) -> Dict[str, Any]:
        """Execute an atomic multi-step input chain."""
        try:
            result = self._rpc_call("atomicChain", {"pid": pid, "steps": steps, "label": label})
            return result if isinstance(result, dict) else {"success": False, "error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB atomicChain failed: %s", e)
            return {"success": False, "error": str(e)[:200]}

    def smart_invoke(
        self,
        pid: int,
        name: str,
        *,
        parent: Optional[str] = None,
        element_type: Optional[str] = None,
        occurrence: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """Invoke an element using the daemon's best-effort strategy."""
        params: Dict[str, Any] = {"pid": pid, "name": name}
        if parent:
            params["parent"] = parent
        if element_type:
            params["type"] = element_type
        if occurrence is not None:
            params["occurrence"] = occurrence

        try:
            result = self._rpc_call("smartInvoke", params)
            return result if isinstance(result, dict) else {"success": False, "error": "Invalid response"}
        except Exception as e:
            logger.warning("UAB smartInvoke failed: %s", e)
            return {"success": False, "error": str(e)[:200]}

    def navigate(self, pid: int, url: str) -> AppActionResult:
        """Navigate browser to a URL."""
        return self.act(pid, "", "navigate", {"url": url})

    def get_tabs(self, pid: int) -> AppActionResult:
        """List all browser tabs."""
        return self.act(pid, "", "getTabs")

    def switch_tab(self, pid: int, tab_id: str) -> AppActionResult:
        """Switch to a browser tab by ID or index."""
        return self.act(pid, "", "switchTab", {"tabId": tab_id})

    def execute_script(self, pid: int, script: str) -> AppActionResult:
        """Execute JavaScript in the browser."""
        return self.act(pid, "", "executeScript", {"script": script})

    def get_cookies(self, pid: int, url: str = "", domain: str = "") -> AppActionResult:
        """Get browser cookies, optionally filtered by URL or domain."""
        params: Dict[str, Any] = {}
        if url:
            params["url"] = url
        if domain:
            params["domain"] = domain
        return self.act(pid, "", "getCookies", params)

    def set_cookie(self, pid: int, name: str, value: str,
                   domain: str = "", url: str = "") -> AppActionResult:
        """Set a browser cookie."""
        params: Dict[str, Any] = {"cookieName": name, "cookieValue": value}
        if domain:
            params["domain"] = domain
        if url:
            params["url"] = url
        return self.act(pid, "", "setCookie", params)

    def get_local_storage(self, pid: int, key: str = "") -> AppActionResult:
        """Get localStorage value(s) from browser."""
        params: Dict[str, Any] = {}
        if key:
            params["storageKey"] = key
        return self.act(pid, "", "getLocalStorage", params)

    def set_local_storage(self, pid: int, key: str, value: str) -> AppActionResult:
        """Set a localStorage value in the browser."""
        return self.act(pid, "", "setLocalStorage", {"storageKey": key, "storageValue": value})

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
