"""
Health Types — HealthSnapshot model (Prompt 9 / C1-C2).

Public API:
    HealthSnapshot — Pydantic model for system health state
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class HealthSnapshot(BaseModel):
    """A point-in-time snapshot of system health."""
    ready: bool = False
    onboarding_state: str = "UNKNOWN"
    local_llm_ready: bool = False
    scheduler_running: bool = False
    last_health_tick_at: Optional[str] = None
    last_scheduler_tick_at: Optional[str] = None
    degraded_reasons: List[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
