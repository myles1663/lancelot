"""
Lancelot Update Checker — Background service that periodically checks for new versions.

Checks a version manifest URL every 6 hours (configurable).  Stores the result
in memory so the War Room can poll `/api/updates/status` cheaply.

Thread-safe: all mutable state behind a single `threading.Lock`.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lancelot.update_checker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CHECK_INTERVAL = 6 * 3600  # 6 hours
DISMISS_REAPPEAR_SECONDS = 24 * 3600  # 24 hours
NON_DISMISSIBLE_SEVERITIES = {"important", "critical"}

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

            req = urllib.request.Request(
                _VERSION_URL,
                headers={"Accept": "application/json", "User-Agent": f"Lancelot/{self._current_version}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            with self._lock:
                self._latest_version = data.get("latest", self._current_version)
                self._severity = data.get("severity", "info")
                self._message = data.get("message")
                self._changelog_url = data.get("changelog_url")
                self._released_at = data.get("released_at")
                self._checked_at = time.time()
                self._check_error = None

            if self._latest_version != self._current_version:
                logger.info(
                    "Update available: %s → %s (%s)",
                    self._current_version, self._latest_version, self._severity,
                )
            else:
                logger.debug("Version check: up to date (%s)", self._current_version)

        except Exception as exc:
            with self._lock:
                self._checked_at = time.time()
                self._check_error = str(exc)
            logger.warning("Version check failed: %s", exc)
