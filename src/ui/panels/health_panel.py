"""
War Room — Health Panel (E3).

Displays live/ready status, degraded reasons, and last tick.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)


class HealthPanel:
    """Health panel data provider for the War Room."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self._base_url = base_url.rstrip("/")

    def get_live(self) -> Dict[str, Any]:
        try:
            resp = requests.get(f"{self._base_url}/health/live", timeout=5)
            return resp.json()
        except Exception:
            return {"status": "unreachable"}

    def get_ready(self) -> Dict[str, Any]:
        try:
            resp = requests.get(f"{self._base_url}/health/ready", timeout=5)
            return resp.json()
        except Exception:
            return {
                "ready": False,
                "degraded_reasons": ["Backend unreachable"],
                "last_health_tick_at": None,
            }

    def render_data(self) -> Dict[str, Any]:
        live = self.get_live()
        ready = self.get_ready()
        return {
            "panel": "health",
            "live_status": live.get("status", "unknown"),
            "ready": ready.get("ready", False),
            "degraded_reasons": ready.get("degraded_reasons", []),
            "last_health_tick_at": ready.get("last_health_tick_at"),
            "onboarding_state": ready.get("onboarding_state", "UNKNOWN"),
            "local_llm_ready": ready.get("local_llm_ready", False),
            "scheduler_running": ready.get("scheduler_running", False),
        }
