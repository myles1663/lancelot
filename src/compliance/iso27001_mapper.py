# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
ISO 27001:2022 Annex A Control Mapper.

Maps receipt types to ISO 27001:2022 Annex A controls.  Controls outside
the scope of an AI agent governance platform (physical security, supplier
relationships, business continuity) are excluded with an explicit note.
"""

from __future__ import annotations

from typing import Any, Dict, List

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


ISO27001_CONTROL_MAP: Dict[str, Dict[str, Any]] = {
    "A.5.1": {
        "description": "Information Security Policies — Soul document as governance policy artifact",
        "receipt_types": [
            ActionType.SOUL_UPDATED.value,
            ActionType.SOUL_VERSION_PINNED.value,
        ],
        "notes": "Soul version history provides policy change audit trail.",
    },
    "A.5.15": {
        "description": "Access Control — Operator identity on all governance actions",
        "receipt_types": [
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
            ActionType.T3_APPROVED.value,
            ActionType.T3_REJECTED.value,
            ActionType.CREDENTIAL_REGISTERED.value,
            ActionType.CREDENTIAL_REVOKED.value,
            ActionType.CONNECTOR_ENABLED.value,
            ActionType.CONNECTOR_DISABLED.value,
        ],
        "notes": "Maps to CC6.x in SOC 2. Same evidence, different framework key.",
    },
    "A.5.16": {
        "description": "Identity Management — Stable operator_id across sessions",
        "receipt_types": [
            ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        ],
        "notes": "operator_id provides persistent identity across all audit periods.",
    },
    "A.5.17": {
        "description": "Authentication — Session creation records with auth_method",
        "receipt_types": [],
        "notes": "Local auth provides baseline evidence; SSO adds richer evidence.",
    },
    "A.5.23": {
        "description": "Cloud Services Security — Governed connector and MCP server receipts",
        "receipt_types": [
            ActionType.CONNECTOR_ENABLED.value,
            ActionType.CONNECTOR_DISABLED.value,
            ActionType.MCP_SERVER_REGISTERED.value,
            ActionType.MCP_SERVER_REVOKED.value,
            ActionType.MCP_TOOL_CALL.value,
            ActionType.MCP_TOOL_BLOCKED.value,
        ],
        "notes": "All cloud service interactions mediated by governed proxy.",
    },
    "A.5.28": {
        "description": "Collection of Evidence — Receipt DAG integrity, tamper-evident chaining",
        "receipt_types": [
            ActionType.GOVERNANCE_WRITE_ERROR.value,
            ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        ],
        "notes": "Chain integrity verification provides evidence admissibility argument.",
    },
    "A.8.2": {
        "description": "Privileged Access Rights — T3 approval records with operator identity",
        "receipt_types": [
            ActionType.T3_APPROVED.value,
            ActionType.T3_REJECTED.value,
            ActionType.MCP_T3_APPROVED.value,
            ActionType.MCP_T3_REJECTED.value,
        ],
        "notes": "T3 is the architectural privileged action gate.",
    },
    "A.8.15": {
        "description": "Logging — Receipt system as governance log",
        "receipt_types": [
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
            ActionType.HIVE_INTERVENTION_EVENT.value,
        ],
        "notes": "More comprehensive than standard logging: every action, every authorization.",
    },
    "A.8.16": {
        "description": "Monitoring Activities — Kill switch events and anomaly detection",
        "receipt_types": [
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
            ActionType.AGENT_STOPPED.value,
        ],
        "notes": "Kill switch activation constitutes documented incident detection.",
    },
    "A.8.17": {
        "description": "Clock Synchronization — Receipt timestamp consistency (ISO 8601)",
        "receipt_types": [],
        "notes": "All receipts share the same time source. Noted in export metadata.",
    },
}

# Controls explicitly excluded from scope
ISO27001_EXCLUDED_CONTROLS = {
    "A.7": "Physical controls — outside scope of AI agent governance platform.",
    "A.5.19": "Supplier relationships — not applicable to agent runtime.",
    "A.5.29": "Business continuity — not applicable at agent governance layer.",
    "A.5.30": "ICT readiness — infrastructure-level, outside agent scope.",
}


def _receipt_to_evidence(receipt_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a redacted receipt dict to an ISO 27001 evidence entry."""
    return build_evidence_entry(receipt_dict)


def transform_iso27001(
    receipts: List[Receipt],
    chain_result: ChainIntegrityResult,
    period_start: str,
    period_end: str,
    operator_id: str,
    generated_at: str,
    export_id: str,
    operator_display_name: str = "",
) -> Dict[str, Any]:
    """Transform receipts into ISO 27001:2022 JSON export format."""

    redacted = redact_receipts(receipts)

    # Index by type
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rd in redacted:
        by_type.setdefault(rd.get("action_type", ""), []).append(rd)

    # Map to controls
    controls: Dict[str, Dict[str, Any]] = {}
    for control_id, control_def in ISO27001_CONTROL_MAP.items():
        evidence = []
        for rtype in control_def["receipt_types"]:
            for rd in by_type.get(rtype, []):
                evidence.append(_receipt_to_evidence(rd))

        evidence_summary = summarize_evidence_entries(evidence)
        exceptions = build_exception_summary(evidence)
        if not evidence:
            control_status = "not_observed_in_period"
        elif exceptions["total_exception_receipts"] > 0:
            control_status = "observed_with_exceptions"
        else:
            control_status = "observed"

        controls[control_id] = {
            "description": control_def["description"],
            "notes": control_def["notes"],
            "control_status": control_status,
            "evidence_count": len(evidence),
            "evidence_summary": evidence_summary,
            "exception_count": exceptions["total_exception_receipts"],
            "exceptions": exceptions["sample_receipts"],
            "evidence": evidence,
        }

    soul_versions = collect_soul_versions(redacted)
    evidence_entries = [build_evidence_entry(rd) for rd in redacted]
    quest_ids = {rd.get("quest_id") for rd in redacted if rd.get("quest_id")}

    return {
        "export_metadata": {
            "format": "ISO27001_2022",
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
            format_name="ISO27001_2022",
            format_version="2.0",
            active_soul_versions=soul_versions,
        ),
        "export_scope": build_export_scope(
            period_start,
            period_end,
            receipt_count=len(receipts),
            quest_count=len(quest_ids),
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
        "statement_of_applicability": {
            "controls_in_scope": len(controls),
            "controls_out_of_scope": len(ISO27001_EXCLUDED_CONTROLS),
        },
        "controls": controls,
        "excluded_controls": ISO27001_EXCLUDED_CONTROLS,
    }
