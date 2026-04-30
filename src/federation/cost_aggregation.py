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

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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
            "model_tier_distribution": self.model_tier_distribution,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstanceCostData":
        return cls(
            instance_id=data.get("instance_id", ""),
            actual_today_usd=float(data.get("actual_today_usd", 0.0) or 0.0),
            projected_today_usd=float(data.get("projected_today_usd", 0.0) or 0.0),
            daily_ceiling_usd=float(data.get("daily_ceiling_usd", 10.0) or 10.0),
            active_spawns=int(data.get("active_spawns", 0) or 0),
            spawn_cost_rate_usd_hr=float(data.get("spawn_cost_rate_usd_hr", 0.0) or 0.0),
            total_tokens_today=int(data.get("total_tokens_today", 0) or 0),
            model_tier_distribution=dict(data.get("model_tier_distribution", {}) or {}),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )


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
        persistence_path: Optional[str] = None,
        stale_after_s: float = 120.0,
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
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._stale_after_s = stale_after_s
        self._lock = threading.Lock()
        self._load_from_disk()

    def update_instance(self, data: InstanceCostData) -> None:
        """Update cost data for an instance (typically from heartbeat)."""
        threshold_change: Optional[tuple[CostThreshold, CostThreshold]] = None
        with self._lock:
            self._instances[data.instance_id] = data
            old_threshold = self._current_threshold
            self._current_threshold = self._compute_threshold()

            if old_threshold != self._current_threshold:
                threshold_change = (old_threshold, self._current_threshold)
                logger.warning(
                    "Federation cost threshold changed: %s → %s",
                    old_threshold.value, self._current_threshold.value,
                )
            self._persist_to_disk_locked()

        if threshold_change and self._on_threshold_change:
            try:
                self._on_threshold_change(*threshold_change)
            except Exception as e:
                logger.error("Threshold change callback failed: %s", e)

    def remove_instance(self, instance_id: str) -> bool:
        """Remove an instance from cost tracking."""
        threshold_change: Optional[tuple[CostThreshold, CostThreshold]] = None
        with self._lock:
            if instance_id in self._instances:
                del self._instances[instance_id]
                old_threshold = self._current_threshold
                self._current_threshold = self._compute_threshold()
                if old_threshold != self._current_threshold:
                    threshold_change = (old_threshold, self._current_threshold)
                self._persist_to_disk_locked()
                removed = True
            else:
                removed = False

        if threshold_change and self._on_threshold_change:
            try:
                self._on_threshold_change(*threshold_change)
            except Exception as e:
                logger.error("Threshold change callback failed: %s", e)

        return removed

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

    def get_stale_instance_ids(self) -> List[str]:
        with self._lock:
            return self._get_stale_instance_ids_locked()

    def check_spawn_allowed(self, instance_id: str) -> tuple[bool, str]:
        """Check if spawning is allowed given current cost governance.

        Returns (allowed, reason).
        """
        with self._lock:
            stale_remote = [
                peer_id
                for peer_id in self._get_stale_instance_ids_locked()
                if peer_id != instance_id
            ]
            if stale_remote:
                return (
                    False,
                    "Federation cost data stale for peer(s): "
                    + ", ".join(sorted(stale_remote)),
                )

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
        active_instances = [
            data
            for data in self._instances.values()
            if not self._is_stale_locked(data)
        ]

        if not active_instances:
            return FederationCostAggregate()

        agg = FederationCostAggregate(instance_count=len(active_instances))

        highest_pct = 0.0
        highest_id = ""

        for data in active_instances:
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

    def _persist_to_disk_locked(self) -> None:
        if self._persistence_path is None:
            return
        payload = {
            "instances": [item.to_dict() for item in self._instances.values()],
            "current_threshold": self._current_threshold.value,
        }
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_from_disk(self) -> None:
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load federated cost aggregation state: %s", exc)
            return

        instances: Dict[str, InstanceCostData] = {}
        for item in payload.get("instances", []) or []:
            try:
                data = InstanceCostData.from_dict(item)
            except Exception as exc:
                logger.warning("Skipping invalid federated cost instance state during load: %s", exc)
                continue
            if data.instance_id:
                instances[data.instance_id] = data

        self._instances = instances
        self._current_threshold = self._compute_threshold()

    def _get_stale_instance_ids_locked(self) -> List[str]:
        return [
            instance_id
            for instance_id, data in self._instances.items()
            if self._is_stale_locked(data)
        ]

    def _is_stale_locked(self, data: InstanceCostData) -> bool:
        if self._stale_after_s <= 0:
            return False
        try:
            updated_at = datetime.fromisoformat(data.updated_at)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        age_s = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_s > self._stale_after_s
