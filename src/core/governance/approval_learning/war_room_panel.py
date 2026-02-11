"""
APL War Room Panel — dashboard data for the War Room UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import ApprovalPattern, AutomationRule
from src.core.governance.approval_learning.rule_engine import RuleEngine


def render_apl_panel(
    rule_engine: RuleEngine,
    decision_log: DecisionLog,
) -> dict:
    """Render APL dashboard data for the War Room.

    Returns structured dict:
    - summary: counts and rates
    - rules: active rule details
    - proposals: pending proposals
    - circuit_breakers: rules that hit daily limit
    - reconfirmation_needed: rules that hit total limit
    - recent_decisions: last 20 decisions
    """
    all_rules = rule_engine.list_rules()
    active_rules = [r for r in all_rules if r.status == "active"]
    proposed_rules = [r for r in all_rules if r.status == "proposed"]
    paused_rules = [r for r in all_rules if r.status == "paused"]
    revoked_rules = [r for r in all_rules if r.status == "revoked"]

    # Today's auto vs manual counts
    recent = decision_log.get_recent(200)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_decisions = [d for d in recent if d.recorded_at[:10] == today]
    auto_approved_today = sum(
        1 for d in today_decisions if d.rule_id and d.decision == "approved"
    )
    auto_denied_today = sum(
        1 for d in today_decisions if d.rule_id and d.decision == "denied"
    )
    manual_today = sum(1 for d in today_decisions if not d.rule_id)

    total = decision_log.total_decisions
    auto_total = decision_log.auto_approved_count
    automation_rate = (auto_total / total) if total > 0 else 0.0

    return {
        "summary": {
            "active_rules": len(active_rules),
            "proposed_rules": len(proposed_rules),
            "paused_rules": len(paused_rules),
            "revoked_rules": len(revoked_rules),
            "auto_approved_today": auto_approved_today,
            "manual_today": manual_today,
            "auto_denied_today": auto_denied_today,
            "total_decisions": total,
            "automation_rate": round(automation_rate, 4),
        },
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "usage_today": r.auto_decisions_today,
                "usage_total": r.auto_decisions_total,
                "conditions_summary": _summarize_conditions(r.conditions),
                "activated_at": r.activated_at,
            }
            for r in active_rules
        ],
        "proposals": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "pattern_type": r.pattern_type,
                "conditions": r.conditions,
            }
            for r in proposed_rules
        ],
        "circuit_breakers": [
            {"id": r.id, "name": r.name, "daily_usage": r.auto_decisions_today}
            for r in rule_engine.check_circuit_breakers()
        ],
        "reconfirmation_needed": [
            {"id": r.id, "name": r.name, "total_usage": r.auto_decisions_total}
            for r in rule_engine.check_reconfirmation()
        ],
        "recent_decisions": [
            {
                "id": d.id,
                "capability": d.context.capability,
                "target": d.context.target,
                "decision": d.decision,
                "is_auto": d.is_auto,
                "rule_id": d.rule_id,
                "recorded_at": d.recorded_at,
            }
            for d in decision_log.get_recent(20)
        ],
    }


def format_proposal_for_owner(
    rule: AutomationRule,
    pattern: Optional[ApprovalPattern] = None,
) -> str:
    """Human-readable proposal text for War Room display."""
    lines = [
        "APPROVAL AUTOMATION PROPOSAL",
        "=" * 40,
        "",
        f"Proposed rule: {rule.name}",
        "",
        rule.description,
        "",
        "Safety guardrails:",
        f"  - Maximum {rule.max_auto_decisions_per_day} auto-approvals per day",
        f"  - Rule expires after {rule.max_auto_decisions_total} total auto-approvals",
        "  - Every auto-approved action still emits a full receipt",
        "  - You can revoke this rule instantly from the War Room",
        "",
        "Options: [Activate Rule]  [Decline]  [Decline Permanently]",
    ]
    return "\n".join(lines)


def _summarize_conditions(conditions: dict) -> str:
    """Short summary of rule conditions."""
    parts = []
    if "capability" in conditions:
        cap = conditions["capability"]
        short = cap.split(".")[-1] if "." in cap else cap
        parts.append(short)
    if "target_domain" in conditions:
        parts.append(f"@{conditions['target_domain']}")
    if "scope" in conditions:
        parts.append(conditions["scope"])
    if "time_range" in conditions:
        s, e = conditions["time_range"]
        parts.append(f"{s:02d}-{e:02d}h")
    if "day_range" in conditions:
        s, e = conditions["day_range"]
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        parts.append(f"{days[s]}-{days[e]}")
    return " | ".join(parts) if parts else "all"
