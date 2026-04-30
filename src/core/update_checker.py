"""
Lancelot Update Checker — Background service that periodically checks for new versions.

Checks a version manifest URL every 6 hours (configurable).  Stores the result
in memory so the War Room can poll `/api/updates/status` cheaply.

Thread-safe: all mutable state behind a single `threading.Lock`.
"""

import json
import logging
import os
import socket
import threading
import time
import urllib.error
from pathlib import Path
from typing import Optional

from src.core.outbound_http import OutboundNetworkError, assert_url_allowed

logger = logging.getLogger("lancelot.update_checker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CHECK_INTERVAL = 6 * 3600  # 6 hours
DISMISS_REAPPEAR_SECONDS = 24 * 3600  # 24 hours
NON_DISMISSIBLE_SEVERITIES = {"important", "critical"}
EXPECTED_NETWORK_ERRNOS = {-3, -2, 101, 110, 111}

_VERSION_URL = os.getenv(
    "LANCELOT_VERSION_URL",
    "https://api.projectlancelot.dev/v1/version",
)

# ---------------------------------------------------------------------------
# Version file reader
# ---------------------------------------------------------------------------

def read_current_version() -> str:
    """Read the current version from the VERSION file."""
    for path in [Path("/app/VERSION"), Path("VERSION")]:
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            continue
    return "unknown"


# ---------------------------------------------------------------------------
# Update Checker
# ---------------------------------------------------------------------------

class UpdateChecker:
    """Background daemon that checks for new Lancelot versions."""

    def __init__(self, check_interval: int = DEFAULT_CHECK_INTERVAL):
        self._lock = threading.Lock()
        self._check_interval = check_interval
        self._current_version = read_current_version()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Latest check result
        self._latest_version: Optional[str] = None
        self._severity: Optional[str] = None  # info | recommended | important | critical
        self._message: Optional[str] = None
        self._changelog_url: Optional[str] = None
        self._released_at: Optional[str] = None
        self._checked_at: Optional[float] = None
        self._check_error: Optional[str] = None
        self._check_error_kind: Optional[str] = None
        self._check_state: str = "unchecked"
        self._next_check_after: Optional[float] = None

        # Dismissal state
        self._dismissed_at: Optional[float] = None

    # -- Public API ---------------------------------------------------------

    @property
    def current_version(self) -> str:
        return self._current_version

    def start(self) -> None:
        """Start the background check thread (daemon)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="update-checker", daemon=True
        )
        self._thread.start()
        logger.info(
            "Update checker started (interval=%ds, url=%s, version=%s)",
            self._check_interval, _VERSION_URL, self._current_version,
        )

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()

    def force_check(self) -> dict:
        """Run a version check immediately and return the result."""
        self._do_check()
        return self.get_update_status()

    def dismiss(self) -> bool:
        """Dismiss the update banner.  Returns False if non-dismissible."""
        with self._lock:
            if self._severity in NON_DISMISSIBLE_SEVERITIES:
                return False
            self._dismissed_at = time.time()
            return True

    def get_update_status(self) -> dict:
        """Return the current update status for the API."""
        with self._lock:
            update_available = (
                self._latest_version is not None
                and self._latest_version != self._current_version
            )

            show_banner = False
            if update_available:
                if self._severity in NON_DISMISSIBLE_SEVERITIES:
                    show_banner = True
                elif self._dismissed_at is None:
                    show_banner = True
                elif time.time() - self._dismissed_at > DISMISS_REAPPEAR_SECONDS:
                    show_banner = True

            operator_message = None
            if self._check_state == "offline":
                operator_message = (
                    "Version check is offline; the local instance is running with "
                    "the installed version and will retry automatically."
                )
            elif self._check_state == "failed":
                operator_message = (
                    "Version check failed; review check_error_kind and retry from "
                    "the updates panel when network policy is configured."
                )

            return {
                "current_version": self._current_version,
                "latest_version": self._latest_version,
                "update_available": update_available,
                "severity": self._severity,
                "message": self._message,
                "changelog_url": self._changelog_url,
                "released_at": self._released_at,
                "checked_at": self._checked_at,
                "check_error": self._check_error,
                "check_error_kind": self._check_error_kind,
                "check_state": self._check_state,
                "next_check_after": self._next_check_after,
                "operator_message": operator_message,
                "show_banner": show_banner,
            }

    # -- Internal -----------------------------------------------------------

    def _run_loop(self) -> None:
        """Background loop: check immediately, then every N seconds."""
        self._do_check()
        while not self._stop_event.wait(timeout=self._check_interval):
            self._do_check()

    def _do_check(self) -> None:
        """Perform a single version check against the manifest URL."""
        try:
            import urllib.request

            version_url = assert_url_allowed(
                _VERSION_URL,
                component="Update checker manifest fetch",
            )
            req = urllib.request.Request(
                version_url,
                headers={"Accept": "application/json", "User-Agent": f"Lancelot/{self._current_version}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            with self._lock:
                checked_at = time.time()
                self._latest_version = data.get("latest", self._current_version)
                self._severity = data.get("severity", "info")
                self._message = data.get("message")
                self._changelog_url = data.get("changelog_url")
                self._released_at = data.get("released_at")
                self._checked_at = checked_at
                self._check_error = None
                self._check_error_kind = None
                self._check_state = (
                    "update_available"
                    if self._latest_version != self._current_version
                    else "up_to_date"
                )
                self._next_check_after = checked_at + self._check_interval

            if self._latest_version != self._current_version:
                logger.info(
                    "Update available: %s → %s (%s)",
                    self._current_version, self._latest_version, self._severity,
                )
            else:
                logger.debug("Version check: up to date (%s)", self._current_version)

        except Exception as exc:
            error_kind = _classify_check_error(exc)
            check_state = "offline" if error_kind == "network_unreachable" else "failed"
            with self._lock:
                checked_at = time.time()
                self._checked_at = checked_at
                self._check_error = str(exc)
                self._check_error_kind = error_kind
                self._check_state = check_state
                self._next_check_after = checked_at + self._check_interval
            if error_kind == "network_unreachable":
                logger.debug(
                    "Version check deferred; update manifest service is unreachable. "
                    "Will retry in %ds (url=%s, error=%s)",
                    self._check_interval,
                    _VERSION_URL,
                    exc,
                )
            else:
                logger.warning("Version check failed: %s", exc)


def _classify_check_error(exc: Exception) -> str:
    """Return the operator-facing error class for a failed version check."""
    if isinstance(exc, OutboundNetworkError):
        return "blocked_by_policy"
    if _is_expected_network_failure(exc):
        return "network_unreachable"
    if isinstance(exc, urllib.error.HTTPError):
        return "manifest_http_error"
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
        return "manifest_parse_error"
    return "unexpected_error"


def _is_expected_network_failure(exc: Exception) -> bool:
    """Treat offline/DNS failures as informational so startup logs stay actionable."""
    if isinstance(exc, (socket.gaierror, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.gaierror, TimeoutError, ConnectionError)):
            return True
        if isinstance(reason, OSError):
            return reason.errno in EXPECTED_NETWORK_ERRNOS
    if isinstance(exc, OSError):
        return exc.errno in EXPECTED_NETWORK_ERRNOS
    return False

