"""Health types for liveness, readiness, and degraded-state snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthSnapshot(BaseModel):
    """A point-in-time snapshot of system health."""
    ready: bool = False
    onboarding_state: str = "UNKNOWN"
    local_llm_ready: bool = False
    local_llm_loaded: bool = False
    local_llm_status: str = "unavailable"
    local_llm_last_verified_at: Optional[str] = None
    local_llm_last_checked_at: Optional[str] = None
    local_llm_last_error: Optional[str] = None
    local_llm_consecutive_failures: int = 0
    local_llm_last_smoke_elapsed_ms: Optional[float] = None
    startup_validation_ready: bool = True
    startup_validation: Dict[str, Any] = Field(default_factory=dict)
    scheduler_running: bool = False
    last_health_tick_at: Optional[str] = None
    last_scheduler_tick_at: Optional[str] = None
    degraded_reasons: List[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
