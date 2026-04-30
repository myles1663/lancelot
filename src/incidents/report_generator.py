# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Incident Report Generator — PDF generation for closed incidents.

Generates a structured incident report suitable for board presentation,
regulatory submission, or post-incident review. Uses the same shared
PDF helpers as the compliance export engine.

Report sections:
  1. Cover Page — incident ID, category, severity, playbook, status
  2. Executive Summary — plain English, one paragraph
  3. Trigger Event — receipt that triggered the playbook
  4. Timeline — chronological response actions
  5. Containment Actions — kill switches, connector disables, agent stops
  6. Remediation Actions — linked receipts
  7. Root Cause Analysis — required for HIGH/CRITICAL
  8. Governance Controls Active — Soul version, kill switch state, APL rules
  9. Post-Incident Actions — review items from playbook
"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.platypus import Paragraph, Spacer, PageBreak

from src.shared.pdf_helpers import (
    build_styles,
    make_table,
    truncate,
    create_pdf_document,
    build_cover_page,
    severity_color,
    BRAND_DARK,
    BRAND_ACCENT,
    RED,
    GREEN,
)
from src.incidents.models import IncidentRecord, IncidentStatus

logger = logging.getLogger("lancelot.incidents.report_generator")


def generate_incident_report(
    incident: IncidentRecord,
    receipts: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[str] = None,
) -> bytes:
    """Generate a PDF incident report.

    Args:
        incident: The closed incident record.
        receipts: Optional list of linked remediation receipts (as dicts).
        output_dir: If provided, also save PDF to this directory.

    Returns:
        PDF content as bytes.
    """
    receipts = receipts or []
    styles = build_styles()
    buffer = io.BytesIO()
    doc = create_pdf_document(
        buffer,
        title=f"Incident Report — {incident.incident_id[:8]}",
    )

    elements = []

    # 1. Cover Page
    severity_str = incident.severity
    status_str = incident.status
    elements.extend(build_cover_page(
        styles,
        title="Incident Report",
        subtitle=f"{incident.category} — {incident.playbook_name}",
        metadata_lines=[
            f"<b>Incident ID:</b> {incident.incident_id}",
            f"<b>Severity:</b> {severity_str}",
            f"<b>Status:</b> {status_str}",
            f"<b>Opened:</b> {_format_ts(incident.opened_at)}",
            f"<b>Closed:</b> {_format_ts(incident.closed_at) if incident.closed_at else 'N/A'}",
            f"<b>Closed By:</b> {incident.closed_by or 'N/A'}",
            "",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "Lancelot Governance Systems LLC — CONFIDENTIAL",
        ],
    ))

    # 2. Executive Summary
    elements.append(Paragraph("1. Executive Summary", styles["heading1"]))
    duration = _compute_duration(incident.opened_at, incident.closed_at)
    summary = (
        f"Incident {incident.incident_id[:8]} was a {severity_str} "
        f"{incident.category.replace('_', ' ').title()} event. "
        f"The incident was opened at {_format_ts(incident.opened_at)} "
        f"and {'resolved after ' + duration if incident.closed_at else 'remains open'}. "
        f"The playbook executed was <b>{incident.playbook_name}</b>. "
        f"{'Root cause: ' + (incident.root_cause or 'Not documented.') if incident.closed_at else ''}"
    )
    elements.append(Paragraph(summary, styles["body"]))
    elements.append(Spacer(1, 12))

    # 3. Trigger Event
    elements.append(Paragraph("2. Trigger Event", styles["heading1"]))
    elements.append(Paragraph(
        f"<b>Trigger Receipt ID:</b> {incident.trigger_receipt_id}",
        styles["body"],
    ))
    elements.append(Paragraph(
        f"<b>Category:</b> {incident.category}",
        styles["body"],
    ))
    elements.append(Paragraph(
        f"<b>Severity at Open:</b> {incident.severity}",
        styles["body"],
    ))
    elements.append(Spacer(1, 12))

    # 4. Timeline
    elements.append(Paragraph("3. Response Timeline", styles["heading1"]))
    if incident.timeline:
        timeline_rows = []
        for entry in incident.timeline:
            timeline_rows.append([
                _format_ts(entry.get("timestamp", "")),
                entry.get("entry_type", ""),
                truncate(entry.get("actor", ""), 20),
                truncate(entry.get("detail", ""), 80),
            ])
        elements.append(make_table(
            ["Time", "Type", "Actor", "Detail"],
            timeline_rows,
            col_widths=[100, 80, 80, 250],
        ))
    else:
        elements.append(Paragraph("No timeline entries recorded.", styles["body"]))
    elements.append(Spacer(1, 12))

    # 5. Containment Actions
    elements.append(Paragraph("4. Containment Actions", styles["heading1"]))
    containment_receipts = [
        r for r in receipts
        if r.get("action_type", "") in (
            "kill_switch_issued", "connector_disabled", "agent_stopped",
            "mcp_server_revoked",
        )
    ]
    if containment_receipts:
        rows = []
        for r in containment_receipts:
            rows.append([
                _format_ts(r.get("timestamp", "")),
                r.get("action_type", ""),
                truncate(str(r.get("description", "")), 60),
                r.get("id", "")[:8],
            ])
        elements.append(make_table(
            ["Time", "Action", "Description", "Receipt"],
            rows,
            col_widths=[100, 100, 200, 60],
        ))
    else:
        elements.append(Paragraph(
            "No containment actions linked to this incident.",
            styles["body"],
        ))
    elements.append(Spacer(1, 12))

    # 6. Remediation Actions
    elements.append(Paragraph("5. Remediation Actions", styles["heading1"]))
    if incident.remediation_receipts:
        remediation_rows = []
        for rid in incident.remediation_receipts:
            # Find the matching receipt
            matching = [r for r in receipts if r.get("id") == rid]
            if matching:
                r = matching[0]
                remediation_rows.append([
                    _format_ts(r.get("timestamp", "")),
                    r.get("action_type", ""),
                    truncate(str(r.get("description", "")), 60),
                    rid[:8],
                ])
            else:
                remediation_rows.append(["", "", "", rid[:8]])
        elements.append(make_table(
            ["Time", "Action", "Description", "Receipt"],
            remediation_rows,
            col_widths=[100, 100, 200, 60],
        ))
    else:
        elements.append(Paragraph(
            "No remediation actions linked.",
            styles["body"],
        ))
    elements.append(Spacer(1, 12))

    # 7. Root Cause Analysis
    elements.append(Paragraph("6. Root Cause Analysis", styles["heading1"]))
    if incident.root_cause:
        elements.append(Paragraph(incident.root_cause, styles["body"]))
    else:
        required = incident.severity in ("CRITICAL", "HIGH")
        msg = ("Root cause analysis is <b>required</b> for "
               f"{incident.severity} incidents but has not been documented."
               if required else "No root cause documented.")
        elements.append(Paragraph(msg, styles["body"]))
    elements.append(Spacer(1, 12))

    # 8. Governance Controls Active
    elements.append(Paragraph("7. Governance Controls Active", styles["heading1"]))
    elements.append(Paragraph(
        "The following governance controls were active at the time of the incident. "
        "This section reflects the state at incident open, not the current state.",
        styles["body"],
    ))
    elements.append(Paragraph(
        f"<b>Playbook:</b> {incident.playbook_name} v1.0",
        styles["body"],
    ))
    elements.append(Spacer(1, 12))

    # 9. Post-Incident Actions
    elements.append(Paragraph("8. Post-Incident Actions", styles["heading1"]))
    elements.append(Paragraph(
        "Review the playbook response for adequacy. If the response was slow "
        "or the playbook was insufficient, file a playbook amendment. If the "
        "incident category repeats, perform pattern analysis.",
        styles["body"],
    ))
    if incident.board_report_generated:
        elements.append(Paragraph(
            "Board report has been generated for this incident.",
            styles["body"],
        ))
    elements.append(Spacer(1, 12))

    # Footer
    elements.append(Paragraph(
        "Lancelot Governance Systems LLC — BSL 1.0 — CONFIDENTIAL",
        styles["small"],
    ))

    # Build PDF
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    # Optionally save to disk
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{incident.incident_id}.pdf")
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        logger.info("Incident report saved: %s", path)

    return pdf_bytes


def _format_ts(ts: Optional[str]) -> str:
    """Format an ISO timestamp for display."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return str(ts)


def _compute_duration(opened: Optional[str], closed: Optional[str]) -> str:
    """Compute human-readable duration between two ISO timestamps."""
    if not opened or not closed:
        return "unknown duration"
    try:
        dt_open = datetime.fromisoformat(opened)
        dt_close = datetime.fromisoformat(closed)
        delta = dt_close - dt_open
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds} seconds"
        elif total_seconds < 3600:
            return f"{total_seconds // 60} minutes"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        else:
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            return f"{days}d {hours}h"
    except (ValueError, TypeError):
        return "unknown duration"
