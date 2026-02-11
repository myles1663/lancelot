"""
Lancelot vNext4: War Room Governance Panel

Streamlit panel showing governance pipeline metrics:
policy cache stats, batch receipt counts, async queue depth.
"""

from __future__ import annotations


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
