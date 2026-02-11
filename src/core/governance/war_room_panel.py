"""
Lancelot vNext4: War Room Governance Panel

Streamlit panel showing governance pipeline metrics:
policy cache stats, batch receipt counts, async queue depth,
and trust ledger graduation status.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def render_governance_panel(streamlit_module=None, gateway_url: str = "http://localhost:8000"):
    """Render the governance metrics panel in the War Room.

    Args:
        streamlit_module: The streamlit module (passed to avoid import issues)
        gateway_url: URL for the gateway API
    """
    st = streamlit_module
    if st is None:
        return

    st.header("Governance Performance")

    try:
        from feature_flags import FEATURE_RISK_TIERED_GOVERNANCE
    except ImportError:
        FEATURE_RISK_TIERED_GOVERNANCE = False

    if not FEATURE_RISK_TIERED_GOVERNANCE:
        st.info(
            "Risk-tiered governance is disabled. "
            "Enable FEATURE_RISK_TIERED_GOVERNANCE to activate."
        )
        return

    # Policy Cache Metrics
    st.subheader("Policy Cache")
    st.caption("Precomputed decisions for T0/T1 actions")

    try:
        import requests
        resp = requests.get(f"{gateway_url}/governance/stats", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            cache_stats = data.get("policy_cache", {})
            col1, col2, col3 = st.columns(3)
            col1.metric("Cache Entries", cache_stats.get("total_entries", 0))
            col2.metric("Hit Rate", f"{cache_stats.get('hit_rate', 0):.1%}")
            col3.metric("Soul Version", cache_stats.get("soul_version", "n/a"))
        else:
            st.warning("Could not fetch governance stats from gateway.")
    except Exception:
        st.warning("Gateway not reachable for governance stats.")

    st.divider()

    # Async Verification Queue
    st.subheader("Async Verification Queue")
    try:
        import requests as _req
        resp = _req.get(f"{gateway_url}/governance/stats", timeout=5)
        if resp.status_code == 200:
            aq = resp.json().get("async_queue", {})
            c1, c2 = st.columns(2)
            c1.metric("Queue Depth", aq.get("depth", 0))
            c2.metric("Pending Jobs", aq.get("pending", 0))
        else:
            st.caption("Stats unavailable")
    except Exception:
        st.caption("Gateway not reachable for queue stats.")

    st.divider()

    # Intent Templates
    st.subheader("Intent Templates")
    try:
        import requests as _req2
        resp = _req2.get(f"{gateway_url}/governance/stats", timeout=5)
        if resp.status_code == 200:
            tpl_data = resp.json().get("intent_templates", {})
            total = tpl_data.get("total", 0)
            active = tpl_data.get("active", 0)
            templates_list = tpl_data.get("templates", [])

            c1, c2 = st.columns(2)
            c1.metric("Total Templates", total)
            c2.metric("Active Templates", active)

            if templates_list:
                for t in templates_list:
                    status = "Active" if t.get("active") else "Candidate"
                    st.text(
                        f"{t.get('intent_pattern', '?')} | {status} | "
                        f"S:{t.get('success_count', 0)} F:{t.get('failure_count', 0)}"
                    )
        else:
            st.caption("Stats unavailable")
    except Exception:
        st.caption("Templates disabled or not initialized")

    st.divider()

    # Batch Receipts
    st.subheader("Batch Receipts")
    st.caption("Metrics available after first task execution")


# ── Trust Ledger Panel ───────────────────────────────────────────

def render_trust_panel(trust_ledger: Any) -> Dict[str, Any]:
    """Render trust ledger data for War Room display.

    Returns a structured dict with summary, per-connector breakdown,
    proposals, and recent events.
    """
    if trust_ledger is None:
        return {
            "summary": {
                "total_records": 0,
                "graduated_records": 0,
                "pending_proposals": 0,
                "avg_success_rate": 0.0,
            },
            "per_connector": [],
            "proposals": [],
            "recent_events": [],
        }

    records = trust_ledger.list_records()
    proposals = trust_ledger.pending_proposals()

    # Summary
    graduated = sum(1 for r in records if r.is_graduated)
    rates = [r.success_rate for r in records if (r.total_successes + r.total_failures) > 0]
    avg_rate = sum(rates) / len(rates) if rates else 0.0

    summary = {
        "total_records": len(records),
        "graduated_records": graduated,
        "pending_proposals": len(proposals),
        "avg_success_rate": round(avg_rate, 4),
    }

    # Per-connector breakdown
    connector_map: Dict[str, List[Dict]] = {}
    for rec in records:
        # Extract connector_id from capability: "connector.{id}.{op}"
        parts = rec.capability.split(".")
        connector_id = parts[1] if len(parts) >= 3 else rec.capability
        if connector_id not in connector_map:
            connector_map[connector_id] = []
        connector_map[connector_id].append({
            "operation": rec.capability,
            "scope": rec.scope,
            "current_tier": rec.current_tier.name,
            "default_tier": rec.default_tier.name,
            "is_graduated": rec.is_graduated,
            "consecutive_successes": rec.consecutive_successes,
            "success_rate": round(rec.success_rate, 4),
        })

    per_connector = [
        {"connector_id": cid, "operations": ops}
        for cid, ops in connector_map.items()
    ]

    # Proposals
    proposal_list = [
        {
            "id": p.id,
            "capability": p.capability,
            "current_tier": p.current_tier.name,
            "proposed_tier": p.proposed_tier.name,
            "consecutive_successes": p.consecutive_successes,
            "status": p.status,
        }
        for p in proposals
    ]

    # Recent events (last 20 across all records)
    all_events = []
    for rec in records:
        for evt in rec.graduation_history:
            all_events.append({
                "timestamp": evt.timestamp,
                "capability": rec.capability,
                "event_type": evt.trigger,
                "from_tier": evt.from_tier.name,
                "to_tier": evt.to_tier.name,
            })
    all_events.sort(key=lambda e: e["timestamp"], reverse=True)
    recent_events = all_events[:20]

    return {
        "summary": summary,
        "per_connector": per_connector,
        "proposals": proposal_list,
        "recent_events": recent_events,
    }


def format_graduation_proposal(proposal: Any) -> str:
    """Format a graduation proposal as a human-readable string."""
    return (
        f"{proposal.capability} has succeeded {proposal.consecutive_successes} "
        f"consecutive times. Propose graduating "
        f"{proposal.current_tier.name} → {proposal.proposed_tier.name}. Approve?"
    )
