"""
War Room — Soul Panel (E1).

Displays active soul version, proposals, and approve/activate controls.
Communicates with the backend via HTTP requests — handles backend-down.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SoulPanel:
    """Soul panel data provider for the War Room."""

    def __init__(self, base_url: str = "http://localhost:8000", token: str = ""):
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def get_status(self) -> Dict[str, Any]:
        """Fetch soul status from backend."""
        try:
            resp = requests.get(
                f"{self._base_url}/soul/status",
                headers=self._headers(),
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Soul panel: backend unavailable: %s", exc)
            return {
                "active_version": "unknown",
                "available_versions": [],
                "pending_proposals": [],
                "error": "Backend unavailable",
            }

    def approve_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Approve a proposal."""
        try:
            resp = requests.post(
                f"{self._base_url}/soul/proposals/{proposal_id}/approve",
                headers=self._headers(),
                timeout=5,
            )
            return resp.json()
        except Exception as exc:
            return {"error": str(exc)}

    def activate_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Activate a proposal."""
        try:
            resp = requests.post(
                f"{self._base_url}/soul/proposals/{proposal_id}/activate",
                headers=self._headers(),
                timeout=5,
            )
            return resp.json()
        except Exception as exc:
            return {"error": str(exc)}

    def render_data(self) -> Dict[str, Any]:
        """Render panel data for the War Room UI."""
        status = self.get_status()
        return {
            "panel": "soul",
            "active_version": status.get("active_version", "unknown"),
            "available_versions": status.get("available_versions", []),
            "pending_proposals": status.get("pending_proposals", []),
            "error": status.get("error"),
        }
