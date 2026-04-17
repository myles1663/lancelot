# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
GDPR Article 30 Processing Record Generator.

Generates structured processing activity records per quest_id where
PII scrubbing was triggered.  For quests with no PII events, a brief
record is generated noting the absence of personal data processing.

If the PII scrubbing pipeline only records occurrence (not category),
the export notes this limitation honestly rather than omitting the record.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from src.shared.receipts import ActionType, Receipt
from src.compliance.chain_integrity import ChainIntegrityResult
from src.compliance.redaction import redact_receipts
from src.compliance.audit_report import (
    build_attribution_summary,
    build_evidence_entry,
    build_exception_summary,
    build_export_scope,
    build_integrity_block,
    build_legacy_attribution_summary,
    build_system_context,
    collect_soul_versions,
    summarize_evidence_entries,
)


# PII-related receipt types and metadata keys
_PII_INDICATOR_TYPES = {"verification", "system"}
_PII_METADATA_KEYS = {"pii_detected", "pii_scrubbed", "pii_categories", "redacted"}

# Receipt types that indicate data transmission to external services
_DATA_TRANSMISSION_TYPES = {
    ActionType.MCP_TOOL_CALL.value,
    ActionType.CONNECTOR_ENABLED.value,
}


def _detect_pii_events(receipts: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group PII-related receipts by quest_id.

    Detects PII events by checking receipt metadata for PII scrubbing
    indicators.  Returns a dict mapping quest_id → list of PII receipts.
    """
    pii_by_quest: Dict[str, List[Dict[str, Any]]] = {}

    for rd in receipts:
        metadata = rd.get("metadata", {})
        has_pii = False

        # Check for explicit PII metadata
        for key in _PII_METADATA_KEYS:
            if key in metadata:
                has_pii = True
                break

        # Check action_name for scrubbing indicators
        action_name = rd.get("action_name", "")
        if "pii" in action_name.lower() or "scrub" in action_name.lower():
            has_pii = True

        if has_pii:
            quest = rd.get("quest_id") or "_no_quest"
            pii_by_quest.setdefault(quest, []).append(rd)

    return pii_by_quest


def _extract_pii_categories(receipts: List[Dict[str, Any]]) -> List[str]:
    """Extract PII categories from receipt metadata.

    If the scrubbing pipeline records categories, returns them.
    If it only records occurrence, returns a note about the limitation.
    """
    categories: Set[str] = set()
    has_category_detail = False

    for rd in receipts:
        metadata = rd.get("metadata", {})
        cats = metadata.get("pii_categories", [])
        if isinstance(cats, list) and cats:
            categories.update(cats)
            has_category_detail = True

    if has_category_detail:
        return sorted(categories)

    # Limitation: categories not recorded by scrubbing pipeline
    return ["detected, category not recorded"]


def _extract_recipients(receipts: List[Dict[str, Any]]) -> List[str]:
    """Extract external data recipients from transmission receipts."""
    recipients: Set[str] = set()
    for rd in receipts:
        if rd.get("action_type") in _DATA_TRANSMISSION_TYPES:
            # Extract target from inputs
            inputs = rd.get("inputs", {})
            target = (
                inputs.get("server_id")
                or inputs.get("connector_id")
                or inputs.get("target")
                or inputs.get("url", "")
            )
            if target:
                recipients.add(str(target))
    return sorted(recipients)


def _build_processing_record(
    quest_id: str,
    quest_receipts: List[Dict[str, Any]],
    pii_receipts: List[Dict[str, Any]],
    all_receipts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a GDPR Article 30 processing activity record for a quest."""

    has_pii = len(pii_receipts) > 0
    categories = _extract_pii_categories(pii_receipts) if has_pii else []
    recipients = _extract_recipients(quest_receipts)

    # Derive time range from quest receipts
    timestamps = [r.get("timestamp", "") for r in quest_receipts if r.get("timestamp")]
    start_time = min(timestamps) if timestamps else ""
    end_time = max(timestamps) if timestamps else ""

    operator_name = os.getenv("LANCELOT_OPERATOR_NAME", "")

    return {
        "quest_id": quest_id,
        "processing_activity": True,
        "personal_data_processed": has_pii,
        "purpose": "AI agent autonomous task execution under Soul governance constraints",
        "controller": {
            "name": operator_name or "See deployment configuration",
            "deployment_id": os.getenv("LANCELOT_DEPLOYMENT_ID", ""),
        },
        "categories_of_data_subjects": (
            "Derived from PII scrubbing events" if has_pii
            else "No personal data subjects identified"
        ),
        "categories_of_personal_data": categories,
        "recipients": recipients,
        "retention_period": "Per receipt TTL configuration",
        "security_measures": (
            "Soul governance constraints, risk tier controls (T0-T3), "
            "input sanitization pipeline, credential vault isolation, "
            "PII scrubbing before external transmission"
        ),
        "pii_event_count": len(pii_receipts),
        "total_actions": len(quest_receipts),
        "period_start": start_time,
        "period_end": end_time,
        "evidence_summary": summarize_evidence_entries(
            build_evidence_entry(receipt) for receipt in quest_receipts
        ),
    }


def transform_gdpr(
    receipts: List[Receipt],
    chain_result: ChainIntegrityResult,
    period_start: str,
    period_end: str,
    operator_id: str,
    generated_at: str,
    export_id: str,
    operator_display_name: str = "",
) -> Dict[str, Any]:
    """Transform receipts into GDPR Article 30 Processing Record format."""

    redacted = redact_receipts(receipts)

    # Group all receipts by quest_id
    by_quest: Dict[str, List[Dict[str, Any]]] = {}
    for rd in redacted:
        quest = rd.get("quest_id") or "_no_quest"
        by_quest.setdefault(quest, []).append(rd)

    # Detect PII events
    pii_by_quest = _detect_pii_events(redacted)

    # Build processing records
    processing_records = []
    for quest_id, quest_receipts in by_quest.items():
        pii_receipts = pii_by_quest.get(quest_id, [])
        record = _build_processing_record(
            quest_id, quest_receipts, pii_receipts, redacted
        )
        processing_records.append(record)

    # Separate PII and non-PII quests
    pii_quests = [r for r in processing_records if r["personal_data_processed"]]
    non_pii_quests = [r for r in processing_records if not r["personal_data_processed"]]
    evidence_entries = [build_evidence_entry(rd) for rd in redacted]
    soul_versions = collect_soul_versions(redacted)
    recipients = sorted(
        {
            recipient
            for record in processing_records
            for recipient in record.get("recipients", [])
        }
    )
    pii_categories = sorted(
        {
            category
            for record in pii_quests
            for category in record.get("categories_of_personal_data", [])
        }
    )

    return {
        "export_metadata": {
            "format": "GDPR_ARTICLE_30",
            "format_version": "2.0",
            "generated_at": generated_at,
            "generated_by": {
                "operator_id": operator_id,
                "display_name": operator_display_name or operator_id,
            },
            "period_start": period_start,
            "period_end": period_end,
            "export_id": export_id,
            "receipt_count": len(receipts),
            "chain_integrity": chain_result.status,
            "chain_anomaly_detail": build_integrity_block(
                chain_result, export_id
            )["chain_anomaly_detail"],
        },
        "system_context": build_system_context(
            generated_at=generated_at,
            operator_id=operator_id,
            operator_display_name=operator_display_name,
            format_name="GDPR_ARTICLE_30",
            format_version="2.0",
            active_soul_versions=soul_versions,
        ),
        "export_scope": build_export_scope(
            period_start,
            period_end,
            receipt_count=len(receipts),
            quest_count=len(processing_records),
        ),
        "integrity": build_integrity_block(chain_result, export_id),
        "evidence_population_summary": summarize_evidence_entries(
            evidence_entries
        ),
        "operator_attribution_summary": build_attribution_summary(redacted),
        "exception_summary": build_exception_summary(
            evidence_entries, include_legacy_attribution=False
        ),
        "legacy_attribution_summary": build_legacy_attribution_summary(
            evidence_entries
        ),
        "processing_activities": {
            "total_quests": len(processing_records),
            "quests_with_personal_data": len(pii_quests),
            "quests_without_personal_data": len(non_pii_quests),
            "records": processing_records,
        },
        "processing_summary": {
            "unique_recipients": recipients,
            "pii_categories_observed": pii_categories,
            "quests_requiring_article_30_attention": len(pii_quests),
        },
        "pii_category_note": (
            "PII categories are derived from the PII scrubbing pipeline. "
            "If the pipeline records only occurrence (not category), records "
            "note 'detected, category not recorded' — this is an honest "
            "limitation, not an omission."
        ),
    }
