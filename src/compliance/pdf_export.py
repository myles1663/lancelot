# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Forensic Timeline PDF — Human-Readable Compliance Artifact.

Generates a branded PDF with seven sections:
  1. Cover Page
  2. Executive Summary
  3. Governance Controls Active
  4. Human Authorization Log
  5. Full Event Log
  6. Anomaly Report
  7. Appendix: Receipt Schema

Uses ReportLab (already in requirements.txt).
"""

from __future__ import annotations

import io
import os
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    HRFlowable,
)

from src.shared.receipts import ActionType, Receipt
from src.compliance.chain_integrity import ChainIntegrityResult
from src.compliance.redaction import redact_receipts, is_pre_identity_migration
from src.core.operator_identity import IDENTITY_REQUIRED_TYPES

logger = logging.getLogger("lancelot.compliance.pdf_export")

# ── Color Palette ─────────────────────────────────────────────────────

_BRAND_DARK = colors.HexColor("#1a1a2e")
_BRAND_ACCENT = colors.HexColor("#0f3460")
_BRAND_LIGHT = colors.HexColor("#e0e0e0")
_GREEN = colors.HexColor("#2ecc71")
_RED = colors.HexColor("#e74c3c")
_AMBER = colors.HexColor("#f39c12")
_T0_COLOR = colors.HexColor("#ecf0f1")   # inert — light grey
_T1_COLOR = colors.HexColor("#d5f5e3")   # reversible — light green
_T2_COLOR = colors.HexColor("#fdebd0")   # controlled — light amber
_T3_COLOR = colors.HexColor("#fadbd8")   # irreversible — light red

_TIER_COLORS = {
    "T0": _T0_COLOR, "T1": _T1_COLOR, "T2": _T2_COLOR, "T3": _T3_COLOR,
}


# ── Styles ────────────────────────────────────────────────────────────

def _build_styles():
    """Build PDF paragraph styles."""
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "Title", parent=base["Title"],
            fontSize=28, leading=34, textColor=_BRAND_DARK,
            alignment=TA_CENTER, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"],
            fontSize=12, leading=16, textColor=_BRAND_ACCENT,
            alignment=TA_CENTER, spaceAfter=20,
        ),
        "heading1": ParagraphStyle(
            "H1", parent=base["Heading1"],
            fontSize=18, leading=22, textColor=_BRAND_DARK,
            spaceBefore=16, spaceAfter=8,
        ),
        "heading2": ParagraphStyle(
            "H2", parent=base["Heading2"],
            fontSize=14, leading=18, textColor=_BRAND_ACCENT,
            spaceBefore=12, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontSize=10, leading=14, spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"],
            fontSize=8, leading=10, textColor=colors.grey,
        ),
        "chain_intact": ParagraphStyle(
            "ChainIntact", parent=base["Normal"],
            fontSize=14, leading=18, textColor=_GREEN,
            alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
        ),
        "chain_anomaly": ParagraphStyle(
            "ChainAnomaly", parent=base["Normal"],
            fontSize=14, leading=18, textColor=_RED,
            alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
        ),
    }
    return styles


# ── Table Helpers ─────────────────────────────────────────────────────

def _make_table(headers: List[str], rows: List[List[str]], col_widths=None):
    """Build a styled table."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, _BRAND_LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate long strings for table display."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ── Risk Tier Helpers ─────────────────────────────────────────────────

_TIER_MAP = {
    0: "T0", 1: "T1", 2: "T2", 3: "T3",
}

# Receipt types that represent each governance category
_KILL_SWITCH_TYPES = {
    ActionType.KILL_SWITCH_ISSUED.value,
    ActionType.KILL_SWITCH_LIFTED.value,
}
_T3_TYPES = {
    ActionType.T3_APPROVED.value, ActionType.T3_REJECTED.value,
    ActionType.MCP_T3_APPROVED.value, ActionType.MCP_T3_REJECTED.value,
}
_SOUL_TYPES = {
    ActionType.SOUL_UPDATED.value, ActionType.SOUL_VERSION_PINNED.value,
}


# ── Section Builders ──────────────────────────────────────────────────

def _build_cover_page(
    elements: list, styles: dict,
    period_start: str, period_end: str,
    chain_result: ChainIntegrityResult,
    receipt_count: int, operator_id: str,
    generated_at: str,
):
    """Section 1: Cover Page."""
    elements.append(Spacer(1, 100))
    elements.append(Paragraph("LANCELOT", styles["title"]))
    elements.append(Paragraph("Forensic Timeline — Compliance Export", styles["subtitle"]))
    elements.append(Spacer(1, 30))

    # Metadata table
    version = "v0.3.x"
    try:
        from update_checker import read_current_version
        version = read_current_version()
    except Exception:
        pass

    meta_rows = [
        ["Export Period", f"{period_start[:10]}  to  {period_end[:10]}"],
        ["Generated At", generated_at[:19].replace("T", " ") + " UTC"],
        ["Exporting Operator", operator_id[:36]],
        ["Lancelot Version", version],
        ["Total Receipts", str(receipt_count)],
    ]
    meta_table = _make_table(["Field", "Value"], meta_rows, col_widths=[150, 300])
    elements.append(meta_table)

    elements.append(Spacer(1, 20))

    # Chain integrity result — prominent
    if chain_result.is_intact:
        elements.append(Paragraph(
            "CHAIN INTEGRITY: INTACT", styles["chain_intact"]
        ))
        elements.append(Paragraph(
            "The receipt chain is unbroken for the export period. "
            "This governance record is tamper-evident.",
            styles["body"],
        ))
    else:
        elements.append(Paragraph(
            f"CHAIN INTEGRITY: ANOMALY ({chain_result.orphaned_count} gaps)",
            styles["chain_anomaly"],
        ))
        elements.append(Paragraph(
            "The receipt chain contains gaps. See the Anomaly Report "
            "for details. This does not invalidate the export but "
            "should be investigated.",
            styles["body"],
        ))

    elements.append(Spacer(1, 40))
    elements.append(Paragraph(
        "CONFIDENTIAL — Governance Audit Artifact",
        styles["small"],
    ))
    elements.append(PageBreak())


def _build_executive_summary(
    elements: list, styles: dict,
    receipts: List[Dict[str, Any]],
    chain_result: ChainIntegrityResult,
    period_start: str, period_end: str,
    anomaly_threshold: int,
):
    """Section 2: Executive Summary."""
    elements.append(Paragraph("Executive Summary", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))

    # Action counts by risk tier
    tier_counts = Counter()
    for r in receipts:
        tier = _TIER_MAP.get(r.get("tier", 0), "T0")
        tier_counts[tier] += 1

    # Kill switch events
    ks_events = [r for r in receipts if r.get("action_type") in _KILL_SWITCH_TYPES]

    # T3 approvals
    t3_events = [r for r in receipts if r.get("action_type") in _T3_TYPES]
    t3_approved = [r for r in t3_events if "approved" in r.get("action_type", "")]
    t3_rejected = [r for r in t3_events if "rejected" in r.get("action_type", "")]

    # Soul versions
    soul_events = [r for r in receipts if r.get("action_type") in _SOUL_TYPES]

    # Operator attribution
    attributed = sum(1 for r in receipts if r.get("operator_id"))
    pre_migration = sum(1 for r in receipts if r.get("pre_identity_migration"))

    # Blocked / failed actions
    blocked = sum(1 for r in receipts if r.get("status") == "failure")

    elements.append(Paragraph(
        f"<b>Period:</b> {period_start[:10]} to {period_end[:10]}<br/>"
        f"<b>Total governed actions:</b> {len(receipts)}<br/>"
        f"<b>Chain integrity:</b> {chain_result.status}",
        styles["body"],
    ))

    # Risk tier breakdown table
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Action Breakdown by Risk Tier", styles["heading2"]))
    tier_rows = [
        ["T0 — Inert (read-only)", str(tier_counts.get("T0", 0))],
        ["T1 — Reversible (writes)", str(tier_counts.get("T1", 0))],
        ["T2 — Controlled (shell, network)", str(tier_counts.get("T2", 0))],
        ["T3 — Irreversible (approvals required)", str(tier_counts.get("T3", 0))],
    ]
    elements.append(_make_table(["Risk Tier", "Count"], tier_rows, col_widths=[300, 150]))

    # Key governance metrics
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Governance Metrics", styles["heading2"]))
    metrics = [
        ["Kill switch events", str(len(ks_events))],
        ["T3 human approvals", str(len(t3_approved))],
        ["T3 human rejections", str(len(t3_rejected))],
        ["Soul version changes", str(len(soul_events))],
        ["Actions with operator attribution", str(attributed)],
        ["Pre-identity-migration receipts", str(pre_migration)],
        ["Blocked / failed actions", str(blocked)],
    ]
    elements.append(_make_table(["Metric", "Value"], metrics, col_widths=[300, 150]))

    elements.append(PageBreak())


def _build_governance_controls(
    elements: list, styles: dict,
    receipts: List[Dict[str, Any]],
):
    """Section 3: Governance Controls Active."""
    elements.append(Paragraph("Governance Controls Active", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "This section lists every governance subsystem that was active "
        "during the export period. It answers the auditor question: "
        "'What controls were in place?'",
        styles["body"],
    ))

    # Detect active subsystems from receipt types
    action_types = {r.get("action_type", "") for r in receipts}

    subsystems = [
        ("Soul Governance", bool(action_types & _SOUL_TYPES)),
        ("Kill Switches", bool(action_types & _KILL_SWITCH_TYPES)),
        ("T3 Approval Gate", bool(action_types & _T3_TYPES)),
        ("Operator Identity", any(r.get("operator_id") for r in receipts)),
        ("APL (Approval Pattern Learning)", bool(action_types & {
            ActionType.APL_RULE_APPROVED.value,
            ActionType.APL_RULE_REJECTED.value,
        })),
        ("MCP Governance", bool(action_types & {
            ActionType.MCP_TOOL_CALL.value,
            ActionType.MCP_TOOL_BLOCKED.value,
            ActionType.MCP_T3_APPROVED.value,
        })),
        ("Connector Governance", bool(action_types & {
            ActionType.CONNECTOR_ENABLED.value,
            ActionType.CONNECTOR_DISABLED.value,
        })),
        ("Credential Vault", bool(action_types & {
            ActionType.CREDENTIAL_REGISTERED.value,
            ActionType.CREDENTIAL_REVOKED.value,
        })),
        ("HIVE Agent Mesh", bool(action_types & {
            ActionType.HIVE_AGENT_EVENT.value,
            ActionType.HIVE_INTERVENTION_EVENT.value,
        })),
        ("Scheduler", bool(action_types & {
            ActionType.SCHEDULER_TASK_CREATED.value,
            ActionType.SCHEDULER_TASK_DELETED.value,
        })),
    ]

    rows = [[name, "Active" if active else "No activity"] for name, active in subsystems]
    elements.append(_make_table(
        ["Subsystem", "Status"], rows, col_widths=[300, 150]
    ))
    elements.append(PageBreak())


def _build_human_auth_log(
    elements: list, styles: dict,
    receipts: List[Dict[str, Any]],
):
    """Section 4: Human Authorization Log — all identity-required receipts."""
    elements.append(Paragraph("Human Authorization Log", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "All governance actions that required human identity. "
        "This is the section auditors read first.",
        styles["body"],
    ))

    # Filter to identity-required receipt types
    identity_receipts = [
        r for r in receipts
        if r.get("action_type") in IDENTITY_REQUIRED_TYPES
    ]

    if not identity_receipts:
        elements.append(Paragraph(
            "No identity-required governance actions in this period.",
            styles["body"],
        ))
        elements.append(PageBreak())
        return

    rows = []
    for r in identity_receipts:
        rows.append([
            r.get("timestamp", "")[:19].replace("T", " "),
            r.get("action_type", ""),
            r.get("action_name", ""),
            r.get("operator_id", "—")[:12] + "..." if r.get("operator_id") else "—",
            r.get("status", ""),
        ])

    elements.append(_make_table(
        ["Timestamp", "Type", "Action", "Operator", "Status"],
        rows,
        col_widths=[100, 120, 120, 80, 60],
    ))
    elements.append(PageBreak())


def _build_full_event_log(
    elements: list, styles: dict,
    receipts: List[Dict[str, Any]],
):
    """Section 5: Full Event Log — all receipts chronologically."""
    elements.append(Paragraph("Full Event Log", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f"Complete receipt chain ({len(receipts)} receipts, chronological).",
        styles["body"],
    ))

    # Paginate: max 100 rows per table to avoid huge pages
    PAGE_SIZE = 100
    for page_start in range(0, len(receipts), PAGE_SIZE):
        page_receipts = receipts[page_start:page_start + PAGE_SIZE]
        rows = []
        for r in page_receipts:
            tier = _TIER_MAP.get(r.get("tier", 0), "T0")
            rows.append([
                r.get("timestamp", "")[:19].replace("T", " "),
                tier,
                _truncate(r.get("action_type", ""), 25),
                _truncate(r.get("action_name", ""), 30),
                r.get("status", ""),
                r.get("id", "")[:8],
            ])

        t = _make_table(
            ["Timestamp", "Tier", "Type", "Action", "Status", "ID"],
            rows,
            col_widths=[95, 30, 100, 120, 55, 55],
        )

        # Apply tier-based row coloring
        style_cmds = []
        for i, r in enumerate(page_receipts):
            tier = _TIER_MAP.get(r.get("tier", 0), "T0")
            bg = _TIER_COLORS.get(tier, colors.white)
            row_idx = i + 1  # +1 for header
            style_cmds.append(("BACKGROUND", (1, row_idx), (1, row_idx), bg))
        if style_cmds:
            t.setStyle(TableStyle(style_cmds))

        elements.append(t)

        if page_start + PAGE_SIZE < len(receipts):
            elements.append(Spacer(1, 8))

    elements.append(PageBreak())


def _build_anomaly_report(
    elements: list, styles: dict,
    receipts: List[Dict[str, Any]],
    chain_result: ChainIntegrityResult,
    anomaly_threshold: int,
):
    """Section 6: Anomaly Report."""
    elements.append(Paragraph("Anomaly Report", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))

    anomalies = []

    # Chain integrity gaps
    if not chain_result.is_intact:
        anomalies.append({
            "category": "Chain Integrity",
            "detail": f"{chain_result.orphaned_count} orphaned parent references",
            "severity": "HIGH",
        })
        for gap in chain_result.gaps[:10]:  # limit to first 10
            anomalies.append({
                "category": "Chain Gap",
                "detail": f"Receipt {gap.receipt_id[:12]}... → missing parent {gap.orphaned_parent_id[:12]}...",
                "severity": "HIGH",
            })

    # Kill switch activations
    ks_issued = [r for r in receipts if r.get("action_type") == ActionType.KILL_SWITCH_ISSUED.value]
    for ks in ks_issued:
        flag = ks.get("inputs", {}).get("flag", "unknown")
        anomalies.append({
            "category": "Kill Switch",
            "detail": f"Kill switch issued: {flag} at {ks.get('timestamp', '')[:19]}",
            "severity": "MEDIUM",
        })

    # T3 rejections
    t3_rejections = [r for r in receipts if r.get("action_type") in {
        ActionType.T3_REJECTED.value, ActionType.MCP_T3_REJECTED.value,
    }]
    for rej in t3_rejections:
        anomalies.append({
            "category": "T3 Rejection",
            "detail": f"T3 action rejected: {rej.get('action_name', '')} at {rej.get('timestamp', '')[:19]}",
            "severity": "MEDIUM",
        })

    # Blocked actions above threshold
    blocked = [r for r in receipts if r.get("status") == "failure"]
    if len(blocked) > anomaly_threshold:
        anomalies.append({
            "category": "Blocked Actions",
            "detail": f"{len(blocked)} blocked/failed actions (threshold: {anomaly_threshold})",
            "severity": "LOW",
        })

    # Governance write errors
    gov_errors = [r for r in receipts if r.get("action_type") == ActionType.GOVERNANCE_WRITE_ERROR.value]
    for ge in gov_errors:
        anomalies.append({
            "category": "Governance Write Error",
            "detail": f"Identity enforcement failed at {ge.get('timestamp', '')[:19]}",
            "severity": "HIGH",
        })

    if not anomalies:
        elements.append(Paragraph(
            "No anomalies detected in this period.",
            styles["body"],
        ))
    else:
        rows = [[a["severity"], a["category"], a["detail"]] for a in anomalies]
        elements.append(_make_table(
            ["Severity", "Category", "Detail"],
            rows,
            col_widths=[60, 100, 300],
        ))

    elements.append(PageBreak())


def _build_appendix(elements: list, styles: dict):
    """Section 7: Appendix — Receipt Schema Reference."""
    elements.append(Paragraph("Appendix: Receipt Schema", styles["heading1"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_ACCENT))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(
        "This appendix allows an auditor unfamiliar with Lancelot to "
        "interpret the evidence in this document.",
        styles["body"],
    ))

    # Receipt fields
    elements.append(Paragraph("Receipt Fields", styles["heading2"]))
    field_rows = [
        ["id", "Unique identifier (UUID)"],
        ["timestamp", "When the action occurred (ISO 8601 UTC)"],
        ["action_type", "Category of action (e.g., tool_call, kill_switch_issued)"],
        ["action_name", "Specific action performed"],
        ["status", "success or failure"],
        ["operator_id", "Stable UUID of the human operator (null = automated)"],
        ["session_id", "Ephemeral War Room session UUID"],
        ["tier", "Risk tier: T0 (inert), T1 (reversible), T2 (controlled), T3 (irreversible)"],
        ["parent_id", "Link to parent receipt (chain linking)"],
        ["quest_id", "Link to originating goal/workflow"],
    ]
    elements.append(_make_table(
        ["Field", "Description"], field_rows, col_widths=[80, 380]
    ))

    # Risk tiers
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Risk Tier Definitions", styles["heading2"]))
    tier_rows = [
        ["T0 — Inert", "Read-only operations. No side effects. Near-zero governance overhead."],
        ["T1 — Reversible", "Write operations with rollback capability. Async verification."],
        ["T2 — Controlled", "Shell execution, network fetches. Sync verification required."],
        ["T3 — Irreversible", "Outbound writes, deployments, deletions. Requires human approval."],
    ]
    elements.append(_make_table(
        ["Tier", "Description"], tier_rows, col_widths=[100, 360]
    ))

    # Operator Identity
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Operator Identity", styles["heading2"]))
    elements.append(Paragraph(
        "Every human-initiated governance action carries an OperatorIdentity "
        "with a stable operator_id (deterministic UUID derived from username). "
        "Automated actions carry operator_id=null. The SYSTEM identity "
        "(operator_id='SYSTEM') is reserved for automated governance actions "
        "and is never valid on human-required receipt types.",
        styles["body"],
    ))
    elements.append(Paragraph(
        "Receipts generated before Operator Identity tracking was implemented "
        "are flagged as PRE_IDENTITY_MIGRATION. They are authentic but cannot "
        "be attributed to a named operator.",
        styles["body"],
    ))


# ── Main PDF Generator ────────────────────────────────────────────────

def generate_forensic_timeline_pdf(
    receipts: List[Receipt],
    chain_result: ChainIntegrityResult,
    period_start: str,
    period_end: str,
    operator_id: str,
    generated_at: str,
    export_id: str,
    anomaly_threshold: int = 5,
) -> bytes:
    """Generate a Forensic Timeline PDF from receipt data.

    Returns the PDF as bytes.
    """
    # Redact all receipts
    redacted = redact_receipts(receipts)

    styles = _build_styles()
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title="Lancelot Forensic Timeline",
        author="Lancelot Governance Systems",
    )

    elements = []

    # Section 1: Cover Page
    _build_cover_page(
        elements, styles,
        period_start, period_end,
        chain_result, len(receipts), operator_id, generated_at,
    )

    # Section 2: Executive Summary
    _build_executive_summary(
        elements, styles, redacted,
        chain_result, period_start, period_end, anomaly_threshold,
    )

    # Section 3: Governance Controls Active
    _build_governance_controls(elements, styles, redacted)

    # Section 4: Human Authorization Log
    _build_human_auth_log(elements, styles, redacted)

    # Section 5: Full Event Log
    _build_full_event_log(elements, styles, redacted)

    # Section 6: Anomaly Report
    _build_anomaly_report(
        elements, styles, redacted, chain_result, anomaly_threshold,
    )

    # Section 7: Appendix
    _build_appendix(elements, styles)

    doc.build(elements)
    return buf.getvalue()
