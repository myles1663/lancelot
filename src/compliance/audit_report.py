# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Shared helpers for auditor-grade compliance exports.

The JSON exports are not just raw receipt dumps. They need a stable summary
contract auditors can scan quickly before dropping into receipt-level detail.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, Iterable, List

from src.compliance.chain_integrity import ChainIntegrityResult


def resolve_operator_display_name(receipt_dict: Dict[str, Any]) -> str:
    metadata = receipt_dict.get("metadata", {})
    display_name = metadata.get("operator_display_name")
    if display_name:
        return str(display_name)

    operator_id = receipt_dict.get("operator_id")
    if operator_id == "SYSTEM":
        return "Lancelot Automation"
    if operator_id == "federation-peer":
        return "Federation Peer"
    if receipt_dict.get("pre_identity_migration"):
        return "Unattributed (pre-identity migration)"
    return str(operator_id or "")


def classify_operator_attribution(receipt_dict: Dict[str, Any]) -> str:
    operator_id = receipt_dict.get("operator_id")
    if operator_id == "SYSTEM":
        return "automation"
    if operator_id == "federation-peer":
        return "federated_peer"
    if receipt_dict.get("pre_identity_migration"):
        return "legacy_unattributed"
    if operator_id:
        return "human"
    return "unattributed"


def build_exception_flags(receipt_dict: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    status = str(receipt_dict.get("status", "")).lower()
    attribution = classify_operator_attribution(receipt_dict)

    if status and status != "success":
        flags.append(f"status_{status}")
    if receipt_dict.get("pre_identity_migration"):
        flags.append("pre_identity_migration")
    if attribution == "unattributed":
        flags.append("missing_operator_attribution")
    if attribution == "legacy_unattributed":
        flags.append("legacy_operator_attribution_gap")
    if attribution == "federated_peer":
        flags.append("federated_actor")

    return flags


def build_evidence_entry(receipt_dict: Dict[str, Any]) -> Dict[str, Any]:
    exception_flags = build_exception_flags(receipt_dict)
    return {
        "receipt_id": receipt_dict.get("id", ""),
        "receipt_type": receipt_dict.get("action_type", ""),
        "timestamp": receipt_dict.get("timestamp", ""),
        "action_name": receipt_dict.get("action_name", ""),
        "status": receipt_dict.get("status", ""),
        "operator_id": receipt_dict.get("operator_id"),
        "display_name": resolve_operator_display_name(receipt_dict),
        "operator_attribution": classify_operator_attribution(receipt_dict),
        "session_id": receipt_dict.get("session_id") or "",
        "quest_id": receipt_dict.get("quest_id") or "",
        "parent_id": receipt_dict.get("parent_id") or "",
        "risk_tier": receipt_dict.get("tier"),
        "pre_identity_migration": receipt_dict.get(
            "pre_identity_migration", False
        ),
        "exception_flags": exception_flags,
    }


def summarize_evidence_entries(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    entries_list = list(entries)
    status_counts = Counter(str(e.get("status", "") or "unknown") for e in entries_list)
    attribution_counts = Counter(
        str(e.get("operator_attribution", "") or "unknown") for e in entries_list
    )
    type_counts = Counter(str(e.get("receipt_type", "") or "unknown") for e in entries_list)
    exception_flags = Counter()
    for entry in entries_list:
        exception_flags.update(entry.get("exception_flags", []))

    return {
        "total_evidence": len(entries_list),
        "status_counts": dict(status_counts),
        "operator_attribution_counts": dict(attribution_counts),
        "unique_operator_ids": sorted(
            {str(e["operator_id"]) for e in entries_list if e.get("operator_id")}
        ),
        "unique_quest_ids": sorted(
            {str(e["quest_id"]) for e in entries_list if e.get("quest_id")}
        ),
        "receipt_type_counts": dict(type_counts),
        "exception_flag_counts": dict(exception_flags),
    }


def extract_exception_entries(
    entries: Iterable[Dict[str, Any]],
    *,
    include_federated_actor: bool = True,
    include_legacy_attribution: bool = True,
) -> List[Dict[str, Any]]:
    exceptions: List[Dict[str, Any]] = []
    for entry in entries:
        flags = list(entry.get("exception_flags", []))
        if not include_federated_actor:
            flags = [flag for flag in flags if flag != "federated_actor"]
        if not include_legacy_attribution:
            flags = [
                flag
                for flag in flags
                if flag not in {
                    "pre_identity_migration",
                    "legacy_operator_attribution_gap",
                }
            ]
        if not flags:
            continue
        exception_entry = dict(entry)
        exception_entry["exception_flags"] = flags
        exceptions.append(exception_entry)
    return exceptions


def build_attribution_summary(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    entries_list = list(entries)
    attribution_counts = Counter(
        classify_operator_attribution(entry) for entry in entries_list
    )
    display_names = sorted(
        {
            resolve_operator_display_name(entry)
            for entry in entries_list
            if resolve_operator_display_name(entry)
        }
    )
    return {
        "total_receipts": len(entries_list),
        "attribution_counts": dict(attribution_counts),
        "known_display_names": display_names,
    }


def build_integrity_block(
    chain_result: ChainIntegrityResult,
    export_id: str,
) -> Dict[str, Any]:
    return {
        "chain_status": chain_result.status,
        "chain_intact": chain_result.is_intact,
        "orphaned_receipt_count": chain_result.orphaned_count,
        "receipts_with_parents": chain_result.receipts_with_parents,
        "total_receipts_evaluated": chain_result.total_receipts,
        "chain_anomaly_detail": (
            chain_result.to_dict() if not chain_result.is_intact else None
        ),
        "export_receipt_verification": {
            "export_id": export_id,
            "receipt_action_type": "compliance_export_generated",
            "artifact_sha256_location": (
                "Verify the export artifact hash against the "
                "COMPLIANCE_EXPORT_GENERATED receipt for this export_id."
            ),
        },
    }


def build_export_scope(
    period_start: str,
    period_end: str,
    *,
    receipt_count: int,
    quest_count: int,
    scoped_quest_id: str = "",
) -> Dict[str, Any]:
    return {
        "period_start": period_start,
        "period_end": period_end,
        "receipt_count": receipt_count,
        "quest_count": quest_count,
        "scoped_quest_id": scoped_quest_id or None,
    }


def build_system_context(
    *,
    generated_at: str,
    operator_id: str,
    operator_display_name: str,
    format_name: str,
    format_version: str,
    active_soul_versions: List[str],
) -> Dict[str, Any]:
    deployment_id = os.getenv("LANCELOT_DEPLOYMENT_ID", "")
    environment_name = (
        os.getenv("LANCELOT_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or "unspecified"
    )
    instance_name = os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME") or ""

    return {
        "format": format_name,
        "format_version": format_version,
        "generated_at": generated_at,
        "generated_by": {
            "operator_id": operator_id,
            "display_name": operator_display_name or operator_id,
        },
        "deployment_id": deployment_id,
        "environment": environment_name,
        "instance_name": instance_name,
        "active_soul_versions": active_soul_versions,
    }


def collect_soul_versions(redacted_receipts: Iterable[Dict[str, Any]]) -> List[str]:
    versions = set()
    for receipt in redacted_receipts:
        for container_name in ("inputs", "outputs", "metadata"):
            container = receipt.get(container_name, {})
            if not isinstance(container, dict):
                continue
            for key in (
                "soul_version_hash",
                "soul_hash",
                "active_soul_hash",
                "target_soul_hash",
            ):
                value = container.get(key, "")
                if value:
                    versions.add(str(value))
    return sorted(versions)


def build_exception_summary(
    entries: Iterable[Dict[str, Any]],
    *,
    sample_limit: int = 25,
    include_federated_actor: bool = True,
    include_legacy_attribution: bool = True,
) -> Dict[str, Any]:
    exceptions = extract_exception_entries(
        entries,
        include_federated_actor=include_federated_actor,
        include_legacy_attribution=include_legacy_attribution,
    )
    summary = summarize_evidence_entries(exceptions)
    return {
        "total_exception_receipts": len(exceptions),
        "by_flag": summary["exception_flag_counts"],
        "sample_receipts": exceptions[:sample_limit],
    }


def build_legacy_attribution_summary(
    entries: Iterable[Dict[str, Any]],
    *,
    sample_limit: int = 25,
) -> Dict[str, Any]:
    entries_list = list(entries)
    legacy_entries = [
        entry
        for entry in entries_list
        if "legacy_operator_attribution_gap" in entry.get("exception_flags", [])
    ]
    return {
        "total_legacy_receipts": len(legacy_entries),
        "sample_receipts": legacy_entries[:sample_limit],
    }
