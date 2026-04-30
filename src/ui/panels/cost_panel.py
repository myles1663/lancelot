"""
War Room — Cost Tracker Panel
==============================

Displays real-time token and cost tracking in the War Room:
- Monthly cost KPIs (total cost, tokens, requests, savings)
- Per-model breakdown table
- Daily cost trend (bar chart, last 14 days)
- Month selector and reset controls

Data is fetched from the control-plane ``/usage/*`` endpoints.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests as http_requests

logger = logging.getLogger(__name__)

_GATEWAY_URL = os.getenv("LANCELOT_GATEWAY_URL", "http://localhost:8000")


# =============================================================================
# API helpers
# =============================================================================

def _get(path: str, params: dict | None = None, timeout: int = 10) -> dict:
    """GET from the gateway control-plane."""
    try:
        resp = http_requests.get(f"{_GATEWAY_URL}{path}", params=params or {}, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("Cost panel: GET %s failed: %s", path, exc)
    return {}


def _post(path: str, timeout: int = 10) -> dict:
    """POST to the gateway control-plane."""
    try:
        resp = http_requests.post(f"{_GATEWAY_URL}{path}", timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("Cost panel: POST %s failed: %s", path, exc)
    return {}


# =============================================================================
# Format helpers
# =============================================================================

def _fmt_tokens(n: int) -> str:
    """Format token count for display (e.g. 75000 -> '75K')."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(c: float) -> str:
    """Format cost for display."""
    if c >= 1.0:
        return f"${c:.2f}"
    if c >= 0.01:
        return f"${c:.3f}"
    return f"${c:.4f}"


# =============================================================================
# Streamlit Render Function
# =============================================================================

def render_cost_panel(streamlit_module: Any = None) -> None:
    """Render the Cost Tracker panel in Streamlit."""
    if streamlit_module is None:
        import streamlit as streamlit_module
    st = streamlit_module

    st.header("Cost Tracker")

    # ---- Fetch data from API ----
    summary_data = _get("/usage/summary")
    monthly_data = _get("/usage/monthly")
    savings_data = _get("/usage/savings")

    usage = summary_data.get("usage", {})
    monthly = monthly_data.get("monthly", {})
    savings = savings_data.get("savings", {})

    # ---- KPI Row ----
    total_cost = monthly.get("total_cost", usage.get("total_cost_est", 0))
    total_tokens = monthly.get("total_tokens", usage.get("total_tokens_est", 0))
    total_requests = monthly.get("total_requests", usage.get("total_requests", 0))
    est_saved = savings.get("estimated_savings", 0)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly Cost", _fmt_cost(total_cost))
    k2.metric("Tokens Used", _fmt_tokens(total_tokens))
    k3.metric("Requests", str(total_requests))
    k4.metric("Saved (Local)", _fmt_cost(est_saved))

    st.divider()

    # ---- Per-Model Breakdown Table ----
    st.subheader("Per-Model Breakdown")

    by_model = monthly.get("by_model", usage.get("by_model", {}))

    if by_model:
        # Build table rows
        table_rows = []
        for model_name, info in sorted(by_model.items(), key=lambda x: x[1].get("cost", 0), reverse=True):
            table_rows.append({
                "Model": model_name,
                "Requests": info.get("requests", 0),
                "Tokens": _fmt_tokens(info.get("tokens", 0)),
                "Cost": _fmt_cost(info.get("cost", 0)),
            })
        st.table(table_rows)
    else:
        st.info("No model usage recorded yet. Send a message to Lancelot to start tracking.")

    st.divider()

    # ---- Daily Cost Trend (last 14 days) ----
    st.subheader("Daily Cost Trend")

    by_day = monthly.get("by_day", {})

    if by_day:
        # Sort days and take last 14
        sorted_days = sorted(by_day.items())[-14:]
        chart_data = {
            "Day": [d[0][-5:] for d in sorted_days],  # MM-DD format
            "Cost ($)": [d[1].get("cost", 0) for d in sorted_days],
            "Tokens": [d[1].get("tokens", 0) for d in sorted_days],
        }
        st.bar_chart(
            data={d: c for d, c in zip(chart_data["Day"], chart_data["Cost ($)"])},
        )

        # Also show daily detail as expandable
        with st.expander("Daily Details"):
            detail_rows = []
            for day_key, day_info in sorted(by_day.items(), reverse=True):
                detail_rows.append({
                    "Date": day_key,
                    "Requests": day_info.get("requests", 0),
                    "Tokens": _fmt_tokens(day_info.get("tokens", 0)),
                    "Cost": _fmt_cost(day_info.get("cost", 0)),
                })
            st.table(detail_rows[:14])
    else:
        st.info("No daily data available yet.")

    st.divider()

    # ---- Month selector + controls ----
    ctrl1, ctrl2 = st.columns([3, 1])

    with ctrl1:
        available_months = monthly_data.get("available_months", [])
        if available_months:
            selected_month = st.selectbox(
                "View Month",
                available_months,
                index=0,
                key="cost_month_selector",
            )
            if selected_month and selected_month != monthly.get("month", ""):
                # Fetch the selected month's data
                other = _get("/usage/monthly", params={"month": selected_month})
                other_m = other.get("monthly", {})
                if other_m and other_m.get("total_requests", 0) > 0:
                    st.write(f"**{selected_month}**: "
                             f"{other_m.get('total_requests', 0)} requests, "
                             f"{_fmt_tokens(other_m.get('total_tokens', 0))} tokens, "
                             f"{_fmt_cost(other_m.get('total_cost', 0))}")
        else:
            st.caption("Only current month available.")

    with ctrl2:
        if st.button("Reset Counters", key="cost_reset_btn", type="secondary"):
            result = _post("/usage/reset")
            if result.get("message"):
                st.success(result["message"])
            else:
                st.warning("Reset may have failed — check gateway logs.")
