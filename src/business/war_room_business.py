"""
Business War Room Panel — content repurposing dashboard.

Displays pipeline status, trust status, connector health,
and governance efficiency for the business automation system.
"""

from __future__ import annotations

from typing import Any, Dict


def render_business_panel(
    trust_ledger: Any = None,
    connector_registry: Any = None,
) -> dict:
    """Render business-specific War Room data.

    Returns structured dict for display:
    - pipeline_status: content processing counts
    - trust_status: per-connector tier summary
    - connector_health: per-connector status
    - governance_efficiency: tier distribution percentages
    """
    result = {
        "pipeline_status": {
            "intake": 0,
            "processing": 0,
            "verification": 0,
            "delivered": 0,
        },
        "trust_status": {},
        "connector_health": {},
        "governance_efficiency": {
            "pct_at_T0": 0.0,
            "pct_at_T1": 0.0,
            "pct_at_T2": 0.0,
            "pct_at_T3": 0.0,
        },
    }

    # Trust status
    if trust_ledger is not None:
        records = trust_ledger.list_records()
        tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}

        for rec in records:
            tier_val = int(rec.current_tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

            # Group by connector
            parts = rec.capability.split(".")
            connector_id = parts[1] if len(parts) >= 3 else "unknown"
            if connector_id not in result["trust_status"]:
                result["trust_status"][connector_id] = {
                    "operations": 0,
                    "graduated": 0,
                    "current_tiers": [],
                }
            result["trust_status"][connector_id]["operations"] += 1
            result["trust_status"][connector_id]["current_tiers"].append(
                rec.current_tier.name
            )
            if rec.is_graduated:
                result["trust_status"][connector_id]["graduated"] += 1

        # Governance efficiency
        total = sum(tier_counts.values())
        if total > 0:
            result["governance_efficiency"] = {
                "pct_at_T0": round(tier_counts[0] / total, 4),
                "pct_at_T1": round(tier_counts[1] / total, 4),
                "pct_at_T2": round(tier_counts[2] / total, 4),
                "pct_at_T3": round(tier_counts[3] / total, 4),
            }

    # Connector health
    if connector_registry is not None:
        try:
            for entry in connector_registry.list_active():
                result["connector_health"][entry.connector_id] = {
                    "status": entry.status.value if hasattr(entry.status, "value") else str(entry.status),
                    "operations": len(entry.connector.get_operations())
                    if hasattr(entry.connector, "get_operations") else 0,
                }
        except Exception:
            pass

    return result
