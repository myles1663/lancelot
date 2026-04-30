# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Budget — spawn budget enforcement and threshold governance.

Enforces Soul-defined spawn budget parameters at the federation level:
- max_concurrent_spawns ceiling
- max_spawn_model_tier ceiling
- Budget utilization tracking with T2 warning / T3 escalation thresholds

The budget tracker aggregates spawn costs across the federation and
triggers governance actions when thresholds are crossed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BudgetThreshold(str, Enum):
    """Budget threshold levels matching federation config."""
    NORMAL = "normal"      # Under warning threshold
    WARNING = "warning"    # T2: warn, restrict new spawns
    CRITICAL = "critical"  # T3: block new spawns, alert operator
    EXCEEDED = "exceeded"  # Kill switch territory


class SpawnDecision(str, Enum):
    """Result of a spawn budget check."""
    ALLOWED = "allowed"
    RESTRICTED = "restricted"  # Warning threshold — allowed with operator notice
    BLOCKED = "blocked"        # Critical threshold — spawn denied


@dataclass
class SpawnRecord:
    """Record of a federation-tracked spawn."""
    agent_id: str
    instance_id: str
    model_tier: str
    estimated_tokens: int = 0
    actual_tokens: int = 0
    spawned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    collapsed_at: Optional[str] = None
    active: bool = True


class FederationBudgetTracker:
    """Tracks and enforces federation-wide spawn budget.

    Uses Soul-defined parameters as constitutional ceilings.
    Tracks actual spawns and projects costs for threshold governance.
    """

    def __init__(
        self,
        max_concurrent_spawns: int = 10,
        max_spawn_model_tier: str = "T2",
        max_estimated_tokens_per_spawn: int = 100000,
        budget_warning_pct: float = 80.0,
        budget_critical_pct: float = 95.0,
    ):
        self._max_concurrent = max_concurrent_spawns
        self._max_tier = max_spawn_model_tier
        self._max_tokens = max_estimated_tokens_per_spawn
        self._warning_pct = budget_warning_pct
        self._critical_pct = budget_critical_pct
        self._spawns: Dict[str, SpawnRecord] = {}
        self._total_tokens_used: int = 0
        self._total_tokens_budget: int = max_concurrent_spawns * max_estimated_tokens_per_spawn
        self._lock = threading.Lock()

    def _active_count_unlocked(self) -> int:
        return sum(1 for s in self._spawns.values() if s.active)

    def _utilization_pct_unlocked(self) -> float:
        if self._total_tokens_budget <= 0:
            return 0.0
        return (self._total_tokens_used / self._total_tokens_budget) * 100.0

    def _threshold_level_unlocked(self) -> BudgetThreshold:
        pct = self._utilization_pct_unlocked()
        if pct >= 100.0:
            return BudgetThreshold.EXCEEDED
        elif pct >= self._critical_pct:
            return BudgetThreshold.CRITICAL
        elif pct >= self._warning_pct:
            return BudgetThreshold.WARNING
        return BudgetThreshold.NORMAL

    @property
    def active_spawn_count(self) -> int:
        with self._lock:
            return self._active_count_unlocked()

    @property
    def utilization_pct(self) -> float:
        with self._lock:
            return self._utilization_pct_unlocked()

    @property
    def threshold_level(self) -> BudgetThreshold:
        with self._lock:
            return self._threshold_level_unlocked()

    def check_spawn(
        self,
        model_tier: str,
        estimated_tokens: int = 0,
    ) -> tuple[SpawnDecision, str]:
        """Check if a spawn is allowed under current budget.

        Args:
            model_tier: Requested model tier (T0-T3).
            estimated_tokens: Estimated token usage for this spawn.

        Returns:
            (decision, reason_string)
        """
        tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
        requested = tier_order.get(model_tier, 2)
        max_allowed = tier_order.get(self._max_tier, 2)

        # Check model tier ceiling (no lock needed — immutable config)
        if requested > max_allowed:
            return SpawnDecision.BLOCKED, (
                f"Model tier {model_tier} exceeds ceiling {self._max_tier}"
            )

        # Check token estimate (no lock needed — immutable config)
        if estimated_tokens > self._max_tokens:
            return SpawnDecision.BLOCKED, (
                f"Estimated tokens ({estimated_tokens}) exceeds per-spawn limit ({self._max_tokens})"
            )

        with self._lock:
            # Check concurrent spawn ceiling
            active = self._active_count_unlocked()
            if active >= self._max_concurrent:
                return SpawnDecision.BLOCKED, (
                    f"Active spawns ({active}) at maximum ({self._max_concurrent})"
                )

            # Check budget threshold
            threshold = self._threshold_level_unlocked()
            pct = self._utilization_pct_unlocked()
            if threshold == BudgetThreshold.EXCEEDED:
                return SpawnDecision.BLOCKED, "Budget exceeded — all spawns blocked"
            elif threshold == BudgetThreshold.CRITICAL:
                return SpawnDecision.BLOCKED, (
                    f"Budget at critical ({pct:.1f}%) — spawns blocked"
                )
            elif threshold == BudgetThreshold.WARNING:
                return SpawnDecision.RESTRICTED, (
                    f"Budget at warning ({pct:.1f}%) — spawn allowed with notice"
                )

        return SpawnDecision.ALLOWED, "Within budget"

    def record_spawn(
        self,
        agent_id: str,
        instance_id: str,
        model_tier: str,
        estimated_tokens: int = 0,
    ) -> SpawnRecord:
        """Record a new spawn. Called after check_spawn allows it."""
        with self._lock:
            record = SpawnRecord(
                agent_id=agent_id,
                instance_id=instance_id,
                model_tier=model_tier,
                estimated_tokens=estimated_tokens,
            )
            self._spawns[agent_id] = record
            self._total_tokens_used += estimated_tokens
            return record

    def record_collapse(
        self,
        agent_id: str,
        actual_tokens: int = 0,
    ) -> bool:
        """Record a spawn collapse. Adjusts token tracking. Returns False if unknown."""
        with self._lock:
            record = self._spawns.get(agent_id)
            if not record:
                return False
            record.active = False
            record.collapsed_at = datetime.now(timezone.utc).isoformat()

            # Adjust token tracking if actual differs from estimate
            if actual_tokens > 0:
                diff = actual_tokens - record.estimated_tokens
                self._total_tokens_used += diff
                record.actual_tokens = actual_tokens

            return True

    def get_snapshot(self) -> Dict[str, Any]:
        """Return current budget state snapshot."""
        with self._lock:
            active = [s for s in self._spawns.values() if s.active]
            return {
                "active_spawns": len(active),
                "max_concurrent_spawns": self._max_concurrent,
                "max_spawn_model_tier": self._max_tier,
                "total_tokens_used": self._total_tokens_used,
                "total_tokens_budget": self._total_tokens_budget,
                "utilization_pct": self._utilization_pct_unlocked(),
                "threshold_level": self._threshold_level_unlocked().value,
                "warning_pct": self._warning_pct,
                "critical_pct": self._critical_pct,
            }
