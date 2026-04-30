# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Shared PDF Helpers — reusable ReportLab components.

Provides: page setup, header/footer generation, section heading styles,
table formatting, and common color palette. Used by both the compliance
export engine and the incident report generator.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

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


# ── Color Palette ─────────────────────────────────────────────────────

BRAND_DARK = colors.HexColor("#1a1a2e")
BRAND_ACCENT = colors.HexColor("#0f3460")
BRAND_LIGHT = colors.HexColor("#e0e0e0")
GREEN = colors.HexColor("#2ecc71")
RED = colors.HexColor("#e74c3c")
AMBER = colors.HexColor("#f39c12")


# ── Styles ────────────────────────────────────────────────────────────

def build_styles() -> Dict[str, ParagraphStyle]:
    """Build standard PDF paragraph styles."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title", parent=base["Title"],
            fontSize=28, leading=34, textColor=BRAND_DARK,
            alignment=TA_CENTER, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"],
            fontSize=12, leading=16, textColor=BRAND_ACCENT,
            alignment=TA_CENTER, spaceAfter=20,
        ),
        "heading1": ParagraphStyle(
            "H1", parent=base["Heading1"],
            fontSize=18, leading=22, textColor=BRAND_DARK,
            spaceBefore=16, spaceAfter=8,
        ),
        "heading2": ParagraphStyle(
            "H2", parent=base["Heading2"],
            fontSize=14, leading=18, textColor=BRAND_ACCENT,
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
        "status_ok": ParagraphStyle(
            "StatusOK", parent=base["Normal"],
            fontSize=14, leading=18, textColor=GREEN,
            alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
        ),
        "status_error": ParagraphStyle(
            "StatusError", parent=base["Normal"],
            fontSize=14, leading=18, textColor=RED,
            alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
        ),
    }


# ── Table Helpers ─────────────────────────────────────────────────────

def make_table(
    headers: List[str],
    rows: List[List[str]],
    col_widths=None,
) -> Table:
    """Build a styled table with branded header row."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, BRAND_LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def truncate(text: str, max_len: int = 60) -> str:
    """Truncate long strings for table display."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ── Document Builder ──────────────────────────────────────────────────

def create_pdf_document(
    buffer: io.BytesIO,
    title: str = "Lancelot Report",
    author: str = "Lancelot Governance Systems",
) -> SimpleDocTemplate:
    """Create a standard A4 PDF document."""
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=title,
        author=author,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
    )


def build_cover_page(
    styles: Dict[str, ParagraphStyle],
    title: str,
    subtitle: str,
    metadata_lines: List[str],
) -> List[Any]:
    """Build a standard cover page with title, subtitle, and metadata."""
    elements = [
        Spacer(1, 80),
        Paragraph(title, styles["title"]),
        Spacer(1, 10),
        Paragraph(subtitle, styles["subtitle"]),
        Spacer(1, 30),
    ]
    for line in metadata_lines:
        elements.append(Paragraph(line, styles["body"]))
    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(width="80%", color=BRAND_ACCENT))
    elements.append(PageBreak())
    return elements


def severity_color(severity: str) -> colors.Color:
    """Return the display color for a severity level."""
    return {
        "CRITICAL": RED,
        "HIGH": AMBER,
        "MEDIUM": colors.HexColor("#3498db"),
        "LOW": colors.grey,
    }.get(severity, colors.grey)
