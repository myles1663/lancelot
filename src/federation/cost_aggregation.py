# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federated Cost Aggregation Engine — real-time cost governance.

Aggregates cost data from all federation instances via heartbeat payloads
and enforces governance thresholds:
- 75%: Warning — operator notified
- 85%: Spawn Restriction — new spawns require approval
- 95%: Spawn Gate — new spawns blocked
- 100%: Hard Stop — all activity paused

Per-instance hard budget enforcement operates independently of federation state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class CostThreshold(str, Enum):
    """Federation cost governance thresholds."""
    NORMAL = "normal"              # Under 75%
    WARNING = "warning"            # 75% — notify operator
    SPAWN_RESTRICTED = "spawn_restricted"  # 85% — spawns need approval
    SPAWN_GATED = "spawn_gated"    # 95% — spawns blocked
    HARD_STOP = "hard_stop"        # 100% — all activity paused


# Default threshold percentages
DEFAULT_THRESHOLDS = {
    CostThreshold.WARNING: 75.0,
    CostThreshold.SPAWN_RESTRICTED: 85.0,
    CostThreshold.SPAWN_GATED: 95.0,
    CostThreshold.HARD_STOP: 100.0,
}


@dataclass
class InstanceCostData:
    """Cost data from a single instance (received via heartbeat)."""
    instance_id: str
    actual_today_usd: float = 0.0
    projected_today_usd: float = 0.0
    daily_ceiling_usd: float = 10.0
    active_spawns: int = 0
    spawn_cost_rate_usd_hr: float = 0.0
    total_tokens_today: int = 0
    model_tier_distribution: Dict[str, int] = field(default_factory=dict)
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def utilization_pct(self) -> float:
        if self.daily_ceiling_usd <= 0:
            return 0.0
        return (self.actual_today_usd / self.daily_ceiling_usd) * 100.0

    @property
    def projected_utilization_pct(self) -> float:
        if self.daily_ceiling_usd <= 0:
            return 0.0
        return (self.projected_today_usd / self.daily_ceiling_usd) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "actual_today_usd": self.actual_today_usd,
            "projected_today_usd": self.projected_today_usd,
            "daily_ceiling_usd": self.daily_ceiling_usd,
            "utilization_pct": round(self.utilization_pct, 1),
            "projected_utilization_pct": round(self.projected_utilization_pct, 1),
            "active_spawns": self.active_spawns,
            "spawn_cost_rate_usd_hr": self.spawn_cost_rate_usd_hr,
            "total_tokens_today": self.total_tokens_today,
            "updated_at": self.updated_at,
        }


@dataclass
class FederationCostAggregate:
    """Aggregate cost metrics across the federation."""
    total_actual_usd: float = 0.0
    total_projected_usd: float = 0.0
    total_ceiling_usd: float = 0.0
    total_active_spawns: int = 0
    total_spawn_cost_rate_usd_hr: float = 0.0
    instance_count: int = 0
    highest_utilization_instance: str = ""
    highest_utilization_pct: float = 0.0
    threshold: CostThreshold = CostThreshold.NORMAL

    @property
    def utilization_pct(self) -> float:
        if self.total_ceiling_usd <= 0:
            return 0.0
        return (self.total_actual_usd / self.total_ceiling_usd) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_actual_usd": round(self.total_actual_usd, 4),
            "total_projected_usd": round(self.total_projected_usd, 4),
            "total_ceiling_usd": round(self.total_ceiling_usd, 2),
            "utilization_pct": round(self.utilization_pct, 1),
            "total_active_spawns": self.total_active_spawns,
            "total_spawn_cost_rate_usd_hr": round(self.total_spawn_cost_rate_usd_hr, 4),
            "instance_count": self.instance_count,
            "highest_utilization_instance": self.highest_utilization_instance,
            "highest_utilization_pct": round(self.highest_utilization_pct, 1),
            "threshold": self.threshold.value,
        }


class FederatedCostAggregator:
    """Aggregates cost data from all federation instances.

    Receives per-instance cost snapshots (typically via heartbeat payload),
    computes federation-wide aggregates, and determines governance thresholds.
    """

    def __init__(
        self,
        thresholds: Optional[Dict[CostThreshold, float]] = None,
        on_threshold_change: Optional[Callable[[CostThreshold, CostThreshold], None]] = None,
    ):
        """
        Args:
            thresholds: Custom threshold percentages.
            on_threshold_change: Callback when threshold changes (old, new).
        """
        self._thresholds = thresholds or dict(DEFAULT_THRESHOLDS)
        self._on_threshold_change = on_threshold_change
        self._instances: Dict[str, InstanceCostData] = {}
        self._current_threshold = CostThreshold.NORMAL
        self._lock = threading.Lock()

    def update_instance(self, data: InstanceCostData) -> None:
        """Update cost data for an instance (typically from heartbeat)."""
        with self._lock:
            self._instances[data.instance_id] = data
            old_threshold = self._current_threshold
            self._current_threshold = self._compute_threshold()

            if old_threshold != self._current_threshold:
                logger.warning(
                    "Federation cost threshold changed: %s → %s",
                    old_threshold.value, self._current_threshold.value,
                )
                if self._on_threshold_change:
                    try:
                        self._on_threshold_change(
                            old_threshold, self._current_threshold
                        )
                    except Exception as e:
                        logger.error("Threshold change callback failed: %s", e)

    def remove_instance(self, instance_id: str) -> bool:
        """Remove an instance from cost tracking."""
        with self._lock:
            if instance_id in self._instances:
                del self._instances[instance_id]
                self._current_threshold = self._compute_threshold()
                return True
            return False

    @property
    def current_threshold(self) -> CostThreshold:
        with self._lock:
            return self._current_threshold

    def get_aggregate(self) -> FederationCostAggregate:
        """Compute current federation-wide aggregate."""
        with self._lock:
            return self._compute_aggregate()

    def get_instance_data(self, instance_id: str) -> Optional[InstanceCostData]:
        """Get cost data for a specific instance."""
        with self._lock:
            return self._instances.get(instance_id)

    def get_all_instances(self) -> List[InstanceCostData]:
        """Get cost data for all instances."""
        with self._lock:
            return list(self._instances.values())

    def check_spawn_allowed(self, instance_id: str) -> tuple[bool, str]:
        """Check if spawning is allowed given current cost governance.

        Returns (allowed, reason).
        """
        with self._lock:
            threshold = self._current_threshold

            if threshold == CostThreshold.HARD_STOP:
                return False, "Federation cost hard stop — all activity paused"
            if threshold == CostThreshold.SPAWN_GATED:
                return False, "Federation cost at 95%+ — spawns blocked"
            if threshold == CostThreshold.SPAWN_RESTRICTED:
                return False, "Federation cost at 85%+ — spawns require approval"

            # Also check per-instance ceiling
            data = self._instances.get(instance_id)
            if data and data.utilization_pct >= 100.0:
                return False, (
                    f"Instance {instance_id} at or over daily ceiling "
                    f"(${data.actual_today_usd:.2f}/${data.daily_ceiling_usd:.2f})"
                )

            return True, "Within budget"

    def _compute_threshold(self) -> CostThreshold:
        """Compute current threshold from aggregate. Caller must hold lock."""
        agg = self._compute_aggregate()
        pct = agg.utilization_pct

        if pct >= self._thresholds[CostThreshold.HARD_STOP]:
            return CostThreshold.HARD_STOP
        if pct >= self._thresholds[CostThreshold.SPAWN_GATED]:
            return CostThreshold.SPAWN_GATED
        if pct >= self._thresholds[CostThreshold.SPAWN_RESTRICTED]:
            return CostThreshold.SPAWN_RESTRICTED
        if pct >= self._thresholds[CostThreshold.WARNING]:
            return CostThreshold.WARNING
        return CostThreshold.NORMAL

    def _compute_aggregate(self) -> FederationCostAggregate:
        """Compute aggregate from all instances. Caller must hold lock."""
        if not self._instances:
            return FederationCostAggregate()

        agg = FederationCostAggregate(instance_count=len(self._instances))

        highest_pct = 0.0
        highest_id = ""

        for data in self._instances.values():
            agg.total_actual_usd += data.actual_today_usd
            agg.total_projected_usd += data.projected_today_usd
            agg.total_ceiling_usd += data.daily_ceiling_usd
            agg.total_active_spawns += data.active_spawns
            agg.total_spawn_cost_rate_usd_hr += data.spawn_cost_rate_usd_hr

            if data.utilization_pct > highest_pct:
                highest_pct = data.utilization_pct
                highest_id = data.instance_id

        agg.highest_utilization_instance = highest_id
        agg.highest_utilization_pct = highest_pct
        agg.threshold = self._current_threshold

        return agg
