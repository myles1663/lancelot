"""
Federation runtime budget control helpers.

Bridges federated budget threshold changes into the persisted runtime pause
system without coupling the cost aggregation engine to the gateway bootstrap.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.core.runtime_pause import is_runtime_paused, pause_runtime


def handle_federation_cost_threshold_change(
    old_threshold: Any,
    new_threshold: Any,
    *,
    cost_aggregator=None,
    receipt_mgr=None,
    audit_engine=None,
    identity=None,
    soul_hash_provider: Optional[Callable[[], str]] = None,
    pause_runtime_fn: Optional[Callable[..., dict]] = None,
    is_runtime_paused_fn: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Apply runtime side effects for a federated cost-threshold transition.

    The hard-stop threshold is fail-closed: entering `hard_stop` pauses the
    instance-wide runtime immediately. Recovery remains operator-controlled;
    dropping below `hard_stop` does not auto-resume the system.
    """
    aggregate = cost_aggregator.get_aggregate() if cost_aggregator else None
    utilization_pct = float(getattr(aggregate, "utilization_pct", 0.0) or 0.0)

    threshold_value = getattr(new_threshold, "value", str(new_threshold))
    old_value = getattr(old_threshold, "value", str(old_threshold))

    action_taken = "none"
    if threshold_value == "warning":
        action_taken = "notify_operator"
    elif threshold_value == "spawn_restricted":
        action_taken = "require_spawn_approval"
    elif threshold_value == "spawn_gated":
        action_taken = "block_new_spawns"
    elif threshold_value == "hard_stop":
        action_taken = "pause_all_activity"

    if receipt_mgr:
        receipt_mgr.record_budget_threshold(
            threshold_level=threshold_value,
            utilization_pct=utilization_pct,
            action_taken=action_taken,
        )

    if audit_engine:
        audit_engine.record(
            event_type="cost_threshold_crossed",
            instance_id=getattr(identity, "instance_id", ""),
            soul_version_hash=soul_hash_provider() if soul_hash_provider else "",
            details={
                "old_threshold": old_value,
                "new_threshold": threshold_value,
                "utilization_pct": utilization_pct,
                "action_taken": action_taken,
            },
        )

    if threshold_value == "hard_stop":
        pause_fn = pause_runtime_fn or pause_runtime
        paused_fn = is_runtime_paused_fn or is_runtime_paused
        if not paused_fn():
            try:
                pause_fn(
                    f"Federation cost hard stop reached at {utilization_pct:.1f}% utilization",
                    source="federation_cost_hard_stop",
                    full_stop=True,
                )
            except TypeError:
                pause_fn(
                    f"Federation cost hard stop reached at {utilization_pct:.1f}% utilization",
                    source="federation_cost_hard_stop",
                )
