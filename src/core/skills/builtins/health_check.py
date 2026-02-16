# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Built-in skill: health_check — periodic system health sweep.

Called by the scheduler every 60 seconds (health_sweep job).
Retrieves the latest HealthSnapshot from the health monitor and
returns a summary dict suitable for receipt emission.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "health_check",
    "version": "1.0.0",
    "description": "Run a system health sweep and return the current health snapshot",
    "risk": "LOW",
    "permissions": [],
    "inputs": [],
}


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run health sweep by reading the latest HealthSnapshot.

    Returns:
        Dict with ready status, degraded reasons, and component states.
    """
    try:
        from health.api import _get_snapshot
    except ImportError:
        try:
            from src.core.health.api import _get_snapshot
        except ImportError:
            logger.warning("health_check: cannot import health snapshot provider")
            return {"status": "unavailable", "error": "Health API not importable"}

    try:
        snapshot = _get_snapshot()

        return {
            "status": "healthy" if snapshot.ready else "degraded",
            "ready": snapshot.ready,
            "onboarding_state": snapshot.onboarding_state,
            "local_llm_ready": snapshot.local_llm_ready,
            "scheduler_running": snapshot.scheduler_running,
            "last_tick": snapshot.last_health_tick_at,
            "degraded_reasons": snapshot.degraded_reasons,
        }
    except Exception as e:
        logger.error("health_check failed: %s", e)
        return {
            "status": "error",
            "error": str(e),
        }
