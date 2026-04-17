# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
SOC 2 Type II Control Mapper — Maps receipt types to Trust Services Criteria.

The mapping is maintained as a dict so it can be updated as SOC 2
requirements evolve without changing the core export engine.
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


# ── SOC 2 Control Mapping ────────────────────────────────────────────
# Maps SOC 2 Trust Services Criteria control IDs to:
#   - description: human-readable control description
#   - receipt_types: list of ActionType values that provide evidence

SOC2_CONTROL_MAP: Dict[str, Dict[str, Any]] = {
    "CC1_1": {
        "description": "COSO Principles — Governance posture and constitutional controls",
        "receipt_types": [
            ActionType.SOUL_UPDATED.value,
            ActionType.SOUL_VERSION_PINNED.value,
            # CRUSADER_MODE_ACTIVATED — maps to closest existing type
            # TODO: Add when CRUSADER_MODE_ACTIVATED ActionType is created
        ],
    },
    "CC2_2": {
        "description": "Internal Communication — Kill switch events with operator identity",
        "receipt_types": [
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
        ],
    },
    "CC6_1": {
        "description": "Logical and Physical Access Controls — Operator action log with named identity",
        "receipt_types": [
            # All IDENTITY_REQUIRED receipt types
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
            ActionType.T3_APPROVED.value,
            ActionType.T3_REJECTED.value,
            ActionType.SOUL_UPDATED.value,
            ActionType.SOUL_VERSION_PINNED.value,
            ActionType.AGENT_DEPLOYED.value,
            ActionType.AGENT_STOPPED.value,
            ActionType.CREDENTIAL_REGISTERED.value,
            ActionType.CREDENTIAL_REVOKED.value,
            ActionType.MCP_SERVER_REGISTERED.value,
            ActionType.MCP_SERVER_REVOKED.value,
            ActionType.MCP_T3_APPROVED.value,
            ActionType.MCP_T3_REJECTED.value,
            ActionType.CONNECTOR_ENABLED.value,
            ActionType.CONNECTOR_DISABLED.value,
            ActionType.ALLOWLIST_MODIFIED.value,
            ActionType.SCHEDULER_TASK_CREATED.value,
            ActionType.SCHEDULER_TASK_DELETED.value,
            ActionType.TOOL_ENABLED.value,
            ActionType.TOOL_DISABLED.value,
            ActionType.APL_RULE_APPROVED.value,
            ActionType.APL_RULE_REJECTED.value,
            ActionType.HIVE_INTERVENTION_EVENT.value,
            ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        ],
    },
    "CC6_2": {
        "description": "New Access Provisioned — Credential registration and connector enablement",
        "receipt_types": [
            ActionType.CREDENTIAL_REGISTERED.value,
            ActionType.MCP_SERVER_REGISTERED.value,
            ActionType.CONNECTOR_ENABLED.value,
        ],
    },
    "CC6_3": {
        "description": "Access Removed — Credential revocation and connector disablement",
        "receipt_types": [
            ActionType.CREDENTIAL_REVOKED.value,
            ActionType.MCP_SERVER_REVOKED.value,
            ActionType.CONNECTOR_DISABLED.value,
        ],
    },
    "CC6_6": {
        "description": "External Threats — Security pipeline blocks and allowlist enforcement",
        "receipt_types": [
            ActionType.MCP_TOOL_BLOCKED.value,
        ],
    },
    "CC7_1": {
        "description": "System Monitoring — Receipt DAG completeness and governance gaps",
        "receipt_types": [
            ActionType.GOVERNANCE_WRITE_ERROR.value,
        ],
    },
    "CC7_2": {
        "description": "Anomalies Evaluated — T3 approval and rejection with operator attribution",
        "receipt_types": [
            ActionType.T3_APPROVED.value,
            ActionType.T3_REJECTED.value,
            ActionType.MCP_T3_APPROVED.value,
            ActionType.MCP_T3_REJECTED.value,
        ],
    },
    "CC7_3": {
        "description": "Incident Identification — Kill switch activation with response time",
        "receipt_types": [
            ActionType.KILL_SWITCH_ISSUED.value,
            ActionType.KILL_SWITCH_LIFTED.value,
            ActionType.AGENT_STOPPED.value,
        ],
    },
    "CC8_1": {
        "description": "Change Management — Soul updates, deployments, configuration changes",
        "receipt_types": [
            ActionType.SOUL_UPDATED.value,
            ActionType.AGENT_DEPLOYED.value,
            ActionType.ALLOWLIST_MODIFIED.value,
            ActionType.TOOL_ENABLED.value,
            ActionType.TOOL_DISABLED.value,
        ],
    },
    "CC9_2": {
        "description": "Risk Mitigation — Risk tier distribution and Trust Ledger changes",
        "receipt_types": [
            # TRUST_TIER_UPDATED — maps to closest existing types
            # TODO: Add when TRUST_TIER_UPDATED ActionType is created
            ActionType.APL_RULE_APPROVED.value,
            ActionType.APL_RULE_REJECTED.value,
        ],
    },
}


def _receipt_to_evidence(receipt_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a redacted receipt dict to an auditor-facing evidence entry."""
    return build_evidence_entry(receipt_dict)


def transform_soc2(
    receipts: List[Receipt],
    chain_result: ChainIntegrityResult,
    period_start: str,
    period_end: str,
    operator_id: str,
    generated_at: str,
    export_id: str,
    operator_display_name: str = "",
) -> Dict[str, Any]:
    """Transform receipts into SOC 2 Type II JSON export format."""

    # Redact all receipts (ip_address removal + pre-migration flagging)
    redacted = redact_receipts(receipts)

    # Build receipt type index for efficient mapping
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rd in redacted:
        action_type = rd.get("action_type", "")
        by_type.setdefault(action_type, []).append(rd)

    # Map to controls
    controls: Dict[str, Dict[str, Any]] = {}
    for control_id, control_def in SOC2_CONTROL_MAP.items():
        evidence = []
        for rtype in control_def["receipt_types"]:
            for rd in by_type.get(rtype, []):
                evidence.append(_receipt_to_evidence(rd))

        evidence_summary = summarize_evidence_entries(evidence)
        exceptions = build_exception_summary(evidence)
        if not evidence:
            control_status = "no_evidence_observed"
        elif exceptions["total_exception_receipts"] > 0:
            control_status = "evidence_with_exceptions"
        else:
            control_status = "evidence_observed"

        controls[control_id] = {
            "description": control_def["description"],
            "control_status": control_status,
            "evidence_count": len(evidence),
            "evidence_summary": evidence_summary,
            "exception_count": exceptions["total_exception_receipts"],
            "exceptions": exceptions["sample_receipts"],
            "evidence": evidence,
        }

    soul_versions = collect_soul_versions(redacted)
    evidence_entries = [build_evidence_entry(rd) for rd in redacted]
    population_summary = summarize_evidence_entries(evidence_entries)
    attribution_summary = build_attribution_summary(redacted)
    exception_summary = build_exception_summary(
        evidence_entries, include_legacy_attribution=False
    )
    quest_ids = {
        rd.get("quest_id")
        for rd in redacted
        if rd.get("quest_id")
    }
    controls_with_evidence = sum(
        1 for control in controls.values() if control["evidence_count"] > 0
    )
    controls_with_exceptions = sum(
        1 for control in controls.values() if control["exception_count"] > 0
    )

    return {
        "export_metadata": {
            "format": "SOC2_TYPE_II",
            "export_id": export_id,
            "format_version": "2.0",
            "generated_at": generated_at,
            "generated_by": {
                "operator_id": operator_id,
                "display_name": operator_display_name or operator_id,
            },
            "period_start": period_start,
            "period_end": period_end,
            "soul_versions_active": soul_versions,
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
            format_name="SOC2_TYPE_II",
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
        "evidence_population_summary": population_summary,
        "operator_attribution_summary": attribution_summary,
        "exception_summary": exception_summary,
        "legacy_attribution_summary": build_legacy_attribution_summary(
            evidence_entries
        ),
        "control_summary": {
            "total_controls": len(controls),
            "controls_with_evidence": controls_with_evidence,
            "controls_without_evidence": len(controls) - controls_with_evidence,
            "controls_with_exceptions": controls_with_exceptions,
        },
        "controls": controls,
    }
