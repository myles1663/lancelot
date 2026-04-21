# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
OTel Metrics Instruments — 12 governance metrics from spec Section 2.4.

All metrics use the 'lancelot.' prefix. Counters reset on process restart
(documented behaviour — OTel receivers handle monotonic counter resets).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("lancelot.observability.metrics")

# Module-level instrument references (set during init)
_meter = None
_actions_total = None
_actions_blocked = None
_kill_switches_active = None
_t3_approvals_pending = None
_t3_approvals_response_time = None
_soul_version_changes = None
_trust_tier_distribution = None
_cost_usd_total = None
_cost_usd_rate = None
_mcp_tool_calls = None
_hive_active_agents = None
_receipts_chain_lag = None

# Internal tracking for gauges
_active_kill_switch_count = 0
_pending_t3_count = 0


def init_metrics(meter) -> None:
    """Initialize all 12 OTel metric instruments.

    Args:
        meter: An opentelemetry.metrics.Meter instance.
    """
    global _meter
    global _actions_total, _actions_blocked
    global _kill_switches_active, _t3_approvals_pending
    global _t3_approvals_response_time, _soul_version_changes
    global _trust_tier_distribution, _cost_usd_total, _cost_usd_rate
    global _mcp_tool_calls, _hive_active_agents, _receipts_chain_lag

    _meter = meter

    # 1. Total governed actions
    _actions_total = meter.create_counter(
        name="lancelot.actions.total",
        description="Total governed actions since startup",
        unit="1",
    )

    # 2. Blocked actions
    _actions_blocked = meter.create_counter(
        name="lancelot.actions.blocked",
        description="Total blocked actions",
        unit="1",
    )

    # 3. Active kill switches (gauge via callback)
    _kill_switches_active = meter.create_up_down_counter(
        name="lancelot.kill_switches.active",
        description="Count of currently active kill switches",
        unit="1",
    )

    # 4. Pending T3 approvals (gauge via callback)
    _t3_approvals_pending = meter.create_up_down_counter(
        name="lancelot.t3_approvals.pending",
        description="Count of T3 actions awaiting operator approval",
        unit="1",
    )

    # 5. T3 approval response time
    _t3_approvals_response_time = meter.create_histogram(
        name="lancelot.t3_approvals.response_time_ms",
        description="Time from T3 approval request to decision",
        unit="ms",
    )

    # 6. Soul version changes
    _soul_version_changes = meter.create_counter(
        name="lancelot.soul.version_changes",
        description="Count of Soul version changes since startup",
        unit="1",
    )

    # 7. Trust tier distribution (gauge via callback)
    _trust_tier_distribution = meter.create_up_down_counter(
        name="lancelot.trust_ledger.tier_distribution",
        description="Count of capabilities at each trust tier",
        unit="1",
    )

    # 8. Total cost USD
    _cost_usd_total = meter.create_counter(
        name="lancelot.cost.usd_total",
        description="Total AI model cost in USD since startup",
        unit="USD",
    )

    # 9. Cost rate USD/hr
    _cost_usd_rate = meter.create_gauge(
        name="lancelot.cost.usd_rate",
        description="Current spend rate in USD per hour (15-min rolling window)",
        unit="USD/hr",
    )

    # 10. MCP tool calls
    _mcp_tool_calls = meter.create_counter(
        name="lancelot.mcp.tool_calls",
        description="Total MCP tool invocations",
        unit="1",
    )

    # 11. Active HIVE agents
    _hive_active_agents = meter.create_up_down_counter(
        name="lancelot.hive.active_agents",
        description="Count of active HIVE sub-agents",
        unit="1",
    )

    # 12. Receipt chain lag
    _receipts_chain_lag = meter.create_gauge(
        name="lancelot.receipts.chain_lag_ms",
        description="Latency between action execution and receipt write completion",
        unit="ms",
    )


# ── Recording Functions ──────────────────────────────────────────

def record_action(action_type: str, tier: int) -> None:
    """Record a governed action (any receipt write)."""
    if _actions_total is None:
        return
    _actions_total.add(1, {"risk_tier": f"T{tier}", "receipt_type": action_type})


def record_blocked_action(block_reason: str) -> None:
    """Record a blocked action with reason."""
    if _actions_blocked is None:
        return
    _actions_blocked.add(1, {"block_reason": block_reason})


def record_kill_switch_change(delta: int) -> None:
    """Record kill switch state change. delta=+1 for activated, -1 for lifted."""
    if _kill_switches_active is None:
        return
    _kill_switches_active.add(delta)


def record_t3_pending_change(delta: int) -> None:
    """Record T3 approval queue change. delta=+1 for new request, -1 for resolved."""
    if _t3_approvals_pending is None:
        return
    _t3_approvals_pending.add(delta)


def record_t3_response_time(duration_ms: float) -> None:
    """Record T3 approval response time."""
    if _t3_approvals_response_time is None:
        return
    _t3_approvals_response_time.record(duration_ms)


def record_soul_version_change() -> None:
    """Record a Soul version change."""
    if _soul_version_changes is None:
        return
    _soul_version_changes.add(1)


def record_trust_tier_change(tier: int, delta: int) -> None:
    """Record trust tier distribution change."""
    if _trust_tier_distribution is None:
        return
    _trust_tier_distribution.add(delta, {"tier": f"T{tier}"})


def record_cost(amount_usd: float, provider: str = "", model: str = "") -> None:
    """Record AI model cost."""
    if _cost_usd_total is None:
        return
    attrs = {}
    if provider:
        attrs["provider"] = provider
    if model:
        attrs["model"] = model
    _cost_usd_total.add(amount_usd, attrs)


def set_cost_rate(usd_per_hour: float) -> None:
    """Set current cost rate gauge."""
    if _cost_usd_rate is None:
        return
    _cost_usd_rate.set(usd_per_hour)


def record_mcp_call(server_id: str, tool_name: str, status: str) -> None:
    """Record an MCP tool invocation."""
    if _mcp_tool_calls is None:
        return
    _mcp_tool_calls.add(1, {
        "server_id": server_id,
        "tool_name": tool_name,
        "status": status,
    })


def record_hive_agent_change(delta: int) -> None:
    """Record HIVE agent count change. delta=+1 for deploy, -1 for stop."""
    if _hive_active_agents is None:
        return
    _hive_active_agents.add(delta)


def set_chain_lag(lag_ms: float) -> None:
    """Set current receipt chain lag gauge."""
    if _receipts_chain_lag is None:
        return
    _receipts_chain_lag.set(lag_ms)


# ── Receipt-Driven Metric Updates ────────────────────────────────

# Map receipt action_types to metric recording functions
_BLOCK_REASONS = {
    "soul_denied": "SOUL_DENIED",
    "kill_switch_off": "KILL_SWITCH_OFF",
    "allowlist_blocked": "ALLOWLIST_BLOCKED",
    "injection_detected": "INJECTION_DETECTED",
    "t3_rejected": "T3_REJECTED",
    "mcp_tool_blocked": "MCP_TOOL_BLOCKED",
}


def update_metrics_from_receipt(receipt_dict: Dict[str, Any]) -> None:
    """Update all relevant OTel metrics based on a receipt write.

    Called from the receipt write path after successful persistence.
    This is the single integration point between the receipt system
    and the OTel metrics layer.
    """
    if _meter is None:
        return

    action_type = receipt_dict.get("action_type", "")
    tier = receipt_dict.get("tier", 0)
    status = receipt_dict.get("status", "")
    metadata = receipt_dict.get("metadata") or {}

    # Always record the action
    record_action(action_type, tier)

    # Check for blocked actions
    at_lower = action_type.lower()
    if at_lower in _BLOCK_REASONS:
        record_blocked_action(_BLOCK_REASONS[at_lower])
    elif status == "failure" and at_lower.startswith("blocked_"):
        record_blocked_action(at_lower.replace("blocked_", "").upper())

    # Kill switch events
    if at_lower == "kill_switch_issued":
        record_kill_switch_change(+1)
    elif at_lower == "kill_switch_lifted":
        record_kill_switch_change(-1)

    # T3 approval events
    if at_lower == "t3_approval_request":
        record_t3_pending_change(+1)
    elif at_lower in ("t3_approved", "t3_rejected"):
        record_t3_pending_change(-1)
        duration = receipt_dict.get("duration_ms")
        if duration is not None:
            record_t3_response_time(float(duration))

    # Soul changes
    if at_lower in ("soul_updated", "soul_version_pinned"):
        record_soul_version_change()

    # MCP tool calls
    if at_lower in ("mcp_tool_call", "mcp_tool_blocked"):
        inputs = receipt_dict.get("inputs") or {}
        record_mcp_call(
            server_id=str(inputs.get("server_id", "")),
            tool_name=str(inputs.get("tool_name", "")),
            status="blocked" if at_lower == "mcp_tool_blocked" else "success",
        )

    # HIVE agent events
    if at_lower == "agent_deployed":
        record_hive_agent_change(+1)
    elif at_lower == "agent_stopped":
        record_hive_agent_change(-1)

    # Cost tracking from task execution receipts
    outputs = receipt_dict.get("outputs") or {}
    if isinstance(outputs, dict):
        cost = outputs.get("cost_usd")
        if cost is not None:
            try:
                record_cost(
                    float(cost),
                    provider=str(outputs.get("provider", "")),
                    model=str(outputs.get("model", "")),
                )
            except (ValueError, TypeError) as exc:
                logger.debug("Ignoring malformed receipt cost_usd value %r: %s", cost, exc)
