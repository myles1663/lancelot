# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation API — Governance API endpoints for inter-instance communication.

Five endpoint categories:
1. State Stream — SSE heartbeat stream + latest snapshot
2. Command API — kill switch, pause (via CommandRelay)
3. Handoff API — task handoff initiate/accept/complete (via HandoffProtocol)
4. Soul API — soul push/pull/handshake (via SoulTransport)
5. Discovery API — identity, status, topology, peers, health
6. Peer API — peer registration (via PeerRegistrationProtocol)
7. Budget API — cost reporting (via CostReporter)

All endpoints are gated by FEATURE_FEDERATION. In standalone mode,
endpoints are present but return mode-appropriate responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability
from src.federation.config import FederationConfig, save_federation_config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/federation",
    tags=["federation"],
)

_COST_STALE_REASON_PREFIX = "Federation cost data stale for peer(s): "
_HEARTBEAT_STREAM_FAILED_PREFIX = "Federation heartbeat stream failed for peer(s): "

# Module-level state — set by init_federation_api()
_identity = None
_heartbeat_emitter = None
_config = None
_topology_registry = None
_initialized = False

# Transport-layer handlers (set by init_federation_transport())
_peer_protocol = None
_command_relay = None
_soul_transport = None
_handoff_protocol = None
_cost_reporter = None
_auth = None
_audit_engine = None
_divergence_detector = None
_transport = None
_heartbeat_mesh = None


class KillCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = ""
    command_type: str = ""
    authority: str = ""
    reason: str = ""
    target_instance_id: Optional[str] = None
    target_agent_id: Optional[str] = None
    target_feature: Optional[str] = None
    timeout_seconds: Optional[float] = None


class FederationCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: KillCommandBody = Field(default_factory=KillCommandBody)
    issuer_instance_id: str = ""


class PauseSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer_instance_id: str = ""
    reason: str = ""
    full_stop: bool = False


class ResumeSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer_instance_id: str = ""
    reason: str = ""


class HandoffInitiationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: str = ""
    federation_quest_id: str = ""
    source_instance_id: str = ""
    task_context: Dict[str, Any] = Field(default_factory=dict)
    soul_context: Dict[str, Any] = Field(default_factory=dict)
    contract: Dict[str, Any] = Field(default_factory=dict)
    receipt_chain: list[Dict[str, Any]] = Field(default_factory=list)


class SoulConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = ""
    instance_id: str = ""


class SoulHandshakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_instance_id: str = ""
    remote_soul_hash: str = ""
    remote_soul_document: Dict[str, Any] = Field(default_factory=dict)


class SoulUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_instance_id: str = ""
    event_id: str = ""
    soul_document: Dict[str, Any] = Field(default_factory=dict)
    soul_hash: str = ""
    tier: str = "T1"


class PeerRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_id: str = ""
    instance_id: str = ""
    public_key_hex: str = ""
    fingerprint: str = ""
    address: str = ""
    role: str = "peer"
    soul_version_hash: str = ""
    challenge: str = ""
    challenge_signature: str = ""


class PeerConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_id: str = ""
    instance_id: str = ""
    public_key_hex: str = ""
    fingerprint: str = ""
    challenge_response: str = ""
    counter_challenge: str = ""
    soul_version_hash: str = ""


class BudgetReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = ""
    actual_today_usd: float = 0.0
    projected_today_usd: float = 0.0
    daily_ceiling_usd: float = 10.0
    active_spawns: int = 0
    spawn_cost_rate_usd_hr: float = 0.0
    total_tokens_today: int = 0


class ManageRegisterPeerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_address: str = ""
    role: str = "peer"


class ManageHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_instance_id: str = ""
    task_context: Dict[str, Any] = Field(default_factory=dict)
    soul_context: Dict[str, Any] = Field(default_factory=dict)
    contract: Dict[str, Any] = Field(default_factory=dict)
    federation_quest_id: str = ""


class ManageCompleteHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: str = ""
    target_instance_id: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)
    receipts: list[Dict[str, Any]] = Field(default_factory=list)
    federation_quest_id: str = ""


class CompletionReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: str = ""
    federation_quest_id: str = ""
    reporting_instance_id: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)
    receipts: list[Dict[str, Any]] = Field(default_factory=list)
    completed_at: str = ""


class ManageKillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: KillCommandBody = Field(default_factory=KillCommandBody)
    target_ids: Optional[list[str]] = None


class DashboardDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class FederatedDashboardDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)
    operator_identity: Dict[str, Any] = Field(default_factory=dict)
    source_instance_id: str = ""


def init_federation_api(
    identity,
    heartbeat_emitter,
    config,
    topology_registry=None,
    divergence_detector=None,
) -> None:
    """Wire federation API with runtime objects. Called from gateway."""
    global _identity, _heartbeat_emitter, _config, _topology_registry, _initialized, _divergence_detector
    _identity = identity
    _heartbeat_emitter = heartbeat_emitter
    _config = config
    _topology_registry = topology_registry
    _divergence_detector = divergence_detector
    _initialized = True
    logger.info("Federation API initialized: instance=%s", identity.instance_id)


def init_federation_transport(
    peer_protocol=None,
    command_relay=None,
    soul_transport=None,
    handoff_protocol=None,
    cost_reporter=None,
    auth=None,
    audit_engine=None,
    transport=None,
    heartbeat_mesh=None,
) -> None:
    """Wire transport-layer handlers into the API. Called after transport init."""
    global _peer_protocol, _command_relay, _soul_transport
    global _handoff_protocol, _cost_reporter, _auth, _audit_engine
    global _transport, _heartbeat_mesh
    _peer_protocol = peer_protocol
    _command_relay = command_relay
    _soul_transport = soul_transport
    _handoff_protocol = handoff_protocol
    _cost_reporter = cost_reporter
    _auth = auth
    _audit_engine = audit_engine
    _transport = transport
    _heartbeat_mesh = heartbeat_mesh
    logger.info("Federation transport handlers wired into API")


def shutdown_federation_api() -> None:
    """Clean up federation API state."""
    global _identity, _heartbeat_emitter, _config, _topology_registry, _initialized, _divergence_detector
    global _peer_protocol, _command_relay, _soul_transport
    global _handoff_protocol, _cost_reporter, _auth
    global _transport, _heartbeat_mesh
    _identity = None
    _heartbeat_emitter = None
    _config = None
    _topology_registry = None
    _initialized = False
    _peer_protocol = None
    _command_relay = None
    _soul_transport = None
    _handoff_protocol = None
    _cost_reporter = None
    _auth = None
    _audit_engine = None
    _divergence_detector = None
    _transport = None
    _heartbeat_mesh = None
    logger.info("Federation API shut down")


def _summarize_circuit_breakers(states: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    summary = {"closed": 0, "open": 0, "half_open": 0}
    for state in states.values():
        state_name = str(state.get("state", "")).lower()
        if state_name in summary:
            summary[state_name] += 1
    return summary


def _current_federation_instance_ids() -> set[str]:
    ids: set[str] = set()
    if _identity and getattr(_identity, "instance_id", ""):
        ids.add(str(_identity.instance_id))
    if _topology_registry is not None:
        try:
            for peer in _topology_registry.list_peers():
                peer_id = str(getattr(peer, "instance_id", "") or "")
                if peer_id:
                    ids.add(peer_id)
        except Exception as exc:
            logger.debug("Failed to collect current federation instance IDs: %s", exc)
    return ids


async def _parse_request_model(
    request: Request,
    model_cls: type[BaseModel],
    *,
    allow_empty: bool = False,
) -> BaseModel:
    if allow_empty and request.headers.get("content-length") in (None, "", "0"):
        payload = {}
    else:
        try:
            payload = await request.json()
        except Exception as exc:
            if allow_empty:
                payload = {}
            else:
                raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _build_runtime_status() -> Dict[str, Any]:
    degraded_reasons = []
    runtime_errors = []

    transport_started = bool(getattr(_transport, "started", False))
    heartbeat_mesh_running = bool(getattr(_heartbeat_mesh, "running", False))
    cost_reporter_running = bool(getattr(_cost_reporter, "running", False))
    subscription_status = {}
    subscription_stream_outcome = {}
    subscription_stream_errors = {}
    circuit_breakers = {}
    circuit_breaker_summary = {"closed": 0, "open": 0, "half_open": 0}
    stale_instance_ids = []
    divergence_evaluation_failed = False
    divergence_status_error = None

    if _transport is None:
        degraded_reasons.append("Federation transport not initialized")
    elif not transport_started:
        degraded_reasons.append("Federation transport not started")

    if _heartbeat_mesh is None:
        degraded_reasons.append("Federation heartbeat mesh not initialized")
    else:
        if not heartbeat_mesh_running:
            degraded_reasons.append("Federation heartbeat mesh not running")
        try:
            subscription_status = _heartbeat_mesh.get_subscription_status()
            subscription_stream_outcome = getattr(
                _heartbeat_mesh,
                "get_stream_outcome_status",
                lambda: {},
            )()
            subscription_stream_errors = getattr(
                _heartbeat_mesh,
                "get_stream_errors",
                lambda: {},
            )()
            divergence_evaluation_failed = bool(
                getattr(_heartbeat_mesh, "divergence_evaluation_failed", False)
            )
            divergence_status_error = getattr(
                _heartbeat_mesh, "divergence_status_error", None
            )
            if divergence_evaluation_failed:
                degraded_reasons.append("Federation divergence evaluation failed")
                if divergence_status_error:
                    runtime_errors.append(
                        f"heartbeat_mesh_divergence_error: {divergence_status_error}"
                    )
            failed_stream_peers = sorted(
                peer_id
                for peer_id, outcome in subscription_stream_outcome.items()
                if str(outcome).lower() == "failed"
            )
            if failed_stream_peers:
                degraded_reasons.append(
                    "Federation heartbeat stream failed for peer(s): "
                    + ", ".join(failed_stream_peers)
                )
        except Exception as exc:
            runtime_errors.append(f"heartbeat_mesh_status_error: {exc}")
            degraded_reasons.append("Federation heartbeat mesh status unavailable")

    if _transport is not None:
        try:
            circuit_breakers = _transport.get_circuit_breaker_states()
            circuit_breaker_summary = _summarize_circuit_breakers(circuit_breakers)
            if circuit_breaker_summary["open"] > 0:
                degraded_reasons.append(
                    f"{circuit_breaker_summary['open']} federation circuit breaker(s) open"
                )
        except Exception as exc:
            runtime_errors.append(f"transport_status_error: {exc}")
            degraded_reasons.append("Federation transport circuit breaker status unavailable")

    if _cost_reporter is None:
        degraded_reasons.append("Federation cost reporter not initialized")
    elif not cost_reporter_running:
        degraded_reasons.append("Federation cost reporter not running")

    soul_consistency = "degraded"
    active_propagations = []
    local_soul_hash = ""
    if _soul_transport:
        try:
            soul_consistency = _soul_transport.get_consistency_state()
            active_propagations = _soul_transport.get_active_propagations()
            local_soul_hash = getattr(_soul_transport, "get_local_soul_hash", lambda: "")() or ""
            if not local_soul_hash:
                degraded_reasons.append("Federation runtime Soul hash unavailable")
        except Exception as exc:
            runtime_errors.append(f"soul_transport_error: {exc}")
            degraded_reasons.append("Federation Soul transport status unavailable")
    else:
        degraded_reasons.append("Federation Soul transport not initialized")

    cost_threshold = "unknown"
    if _cost_reporter:
        try:
            budget_status = _cost_reporter.get_aggregate_status()
            if isinstance(budget_status, dict):
                if budget_status.get("error"):
                    runtime_errors.append(
                        f"cost_reporter_status_error: {budget_status['error']}"
                    )
                    degraded_reasons.append("Federation cost status unavailable")
                cost_threshold = budget_status.get("threshold", cost_threshold)
                raw_stale_ids = [
                    str(instance_id)
                    for instance_id in list(budget_status.get("stale_instance_ids", []) or [])
                    if str(instance_id)
                ]
                current_ids = _current_federation_instance_ids()
                stale_instance_ids = [
                    instance_id
                    for instance_id in raw_stale_ids
                    if not current_ids or instance_id in current_ids
                ]
                if stale_instance_ids:
                    degraded_reasons.append(
                        "Federation cost data stale for peer(s): "
                        + ", ".join(sorted(stale_instance_ids))
                    )
        except Exception as exc:
            runtime_errors.append(f"cost_reporter_error: {exc}")
            degraded_reasons.append("Federation cost status unavailable")

    divergence_state = "unknown"
    divergence_duration_s = 0.0
    reconciliation = None
    if _divergence_detector:
        try:
            divergence_state = _divergence_detector.state.value
            divergence_duration_s = _divergence_detector.get_divergence_duration_s()
            last_reconciliation = _divergence_detector.last_reconciliation
            reconciliation = (
                last_reconciliation.to_dict()
                if last_reconciliation and hasattr(last_reconciliation, "to_dict")
                else None
            )
            if divergence_state != "connected":
                degraded_reasons.append(f"Federation divergence state: {divergence_state}")
        except Exception as exc:
            runtime_errors.append(f"divergence_error: {exc}")
            degraded_reasons.append("Federation divergence status unavailable")
    else:
        degraded_reasons.append("Federation divergence detector not initialized")

    return {
        "runtime_degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "runtime_errors": runtime_errors,
        "transport_started": transport_started,
        "heartbeat_mesh_running": heartbeat_mesh_running,
        "cost_reporter_running": cost_reporter_running,
        "subscription_status": subscription_status,
        "subscription_stream_outcome": subscription_stream_outcome,
        "subscription_stream_errors": subscription_stream_errors,
        "circuit_breakers": circuit_breakers,
        "circuit_breaker_summary": circuit_breaker_summary,
        "stale_instance_ids": stale_instance_ids,
        "divergence_evaluation_failed": divergence_evaluation_failed,
        "divergence_status_error": divergence_status_error,
        "soul_consistency": soul_consistency,
        "local_soul_hash": local_soul_hash,
        "active_propagations": active_propagations,
        "cost_threshold": cost_threshold,
        "divergence_state": divergence_state,
        "divergence_duration_s": divergence_duration_s,
        "reconciliation": reconciliation,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _short_id(value: str) -> str:
    if not value:
        return ""
    return value[:12]


def _elapsed_seconds(iso_timestamp: Optional[str]) -> Optional[float]:
    if not iso_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (_utc_now() - parsed).total_seconds())
    except Exception:
        return None


def _display_name_from_address(address: str) -> str:
    if not address:
        return ""
    try:
        parsed = urlparse(address)
        return parsed.hostname or address.replace("https://", "").replace("http://", "")
    except Exception:
        return address.replace("https://", "").replace("http://", "")


def _command_center_url(address: str) -> str:
    if not address:
        return ""
    base = str(address).rstrip("/")
    if base.endswith("/war-room/command"):
        return base
    if base.endswith("/war-room"):
        return f"{base}/command"
    return f"{base}/war-room/command"


def _dashboard_instance_label_map() -> Dict[str, str]:
    labels: Dict[str, str] = {}
    if _identity and getattr(_identity, "instance_id", ""):
        labels[str(_identity.instance_id)] = "Local Lancelot"
    if _topology_registry is not None:
        try:
            for peer in _topology_registry.list_peers():
                peer_id = str(getattr(peer, "instance_id", "") or "")
                if not peer_id:
                    continue
                metadata = getattr(peer, "metadata", {}) or {}
                address = str(getattr(peer, "address", "") or "")
                labels[peer_id] = (
                    str(metadata.get("instance_name", "")).strip()
                    or _display_name_from_address(address)
                    or f"Instance {_short_id(peer_id)}"
                )
        except Exception as exc:
            logger.debug("Failed to collect dashboard instance labels: %s", exc)
    return labels


def _format_dashboard_instance_list(instance_ids: list[str]) -> str:
    labels = _dashboard_instance_label_map()
    return ", ".join(
        labels.get(instance_id) or f"Instance {_short_id(instance_id)}"
        for instance_id in instance_ids
    )


def _parse_reason_instance_ids(reason: str, prefix: str) -> list[str]:
    if not reason.startswith(prefix):
        return []
    return [
        item.strip()
        for item in reason[len(prefix):].split(",")
        if item.strip()
    ]


def _dashboard_runtime_attention_reasons(runtime_status: Dict[str, Any]) -> list[str]:
    current_ids = _current_federation_instance_ids()
    formatted: list[str] = []
    for raw_reason in runtime_status.get("degraded_reasons", []) or []:
        reason = str(raw_reason or "").strip()
        if not reason:
            continue

        stale_cost_ids = _parse_reason_instance_ids(reason, _COST_STALE_REASON_PREFIX)
        if stale_cost_ids:
            relevant_ids = [
                instance_id for instance_id in stale_cost_ids
                if not current_ids or instance_id in current_ids
            ]
            if relevant_ids:
                formatted.append(
                    "Cost telemetry stale for "
                    + _format_dashboard_instance_list(relevant_ids)
                )
            continue

        failed_stream_ids = _parse_reason_instance_ids(
            reason,
            _HEARTBEAT_STREAM_FAILED_PREFIX,
        )
        if failed_stream_ids:
            relevant_ids = [
                instance_id for instance_id in failed_stream_ids
                if not current_ids or instance_id in current_ids
            ]
            if relevant_ids:
                formatted.append(
                    "Heartbeat stream failed for "
                    + _format_dashboard_instance_list(relevant_ids)
                )
            continue

        formatted.append(reason)
    return list(dict.fromkeys(formatted))


def _dashboard_config_payload() -> Dict[str, Any]:
    dashboard = getattr(_config, "dashboard", None)
    return {
        "enabled": bool(getattr(dashboard, "enabled", True)),
        "poll_interval_s": _safe_float(getattr(dashboard, "poll_interval_s", 10.0), 10.0),
        "stream_interval_s": _safe_float(
            getattr(dashboard, "stream_interval_s", 3.0),
            3.0,
        ),
        "max_recent_activity_items": _safe_int(
            getattr(dashboard, "max_recent_activity_items", 50),
            50,
        ),
        "card_sort_order": str(getattr(dashboard, "card_sort_order", "urgency") or "urgency"),
        "show_fleet_activity_feed": bool(
            getattr(dashboard, "show_fleet_activity_feed", True)
        ),
        "activity_feed_max_events": _safe_int(
            getattr(dashboard, "activity_feed_max_events", 200),
            200,
        ),
    }


def _dashboard_disabled_reason() -> str:
    try:
        import feature_flags as ff
    except Exception:
        return "Feature flags unavailable"

    if not getattr(ff, "FEATURE_FEDERATION", False):
        return "FEATURE_FEDERATION is disabled"
    if not getattr(ff, "FEATURE_FEDERATION_DASHBOARD", False):
        return "FEATURE_FEDERATION_DASHBOARD is disabled"
    if not _dashboard_config_payload()["enabled"]:
        return "Federation dashboard is disabled in config/federation.yaml"
    return ""


def _empty_dashboard_snapshot(*, enabled: bool, disabled_reason: str = "") -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "disabled_reason": disabled_reason,
        "generated_at": _utc_now_iso(),
        "command_center_path": "/war-room/command",
        "dashboard": _dashboard_config_payload(),
        "fleet": {
            "total_instances": 0,
            "instances_needing_attention": 0,
            "critical_instances": 0,
            "lost_instances": 0,
            "paused_instances": 0,
            "pending_approvals": 0,
            "trust_proposals": 0,
            "active_agents": 0,
            "fleet_cost_utilization_pct": 0.0,
            "budget_threshold": "unknown",
            "soul_consistency": "unknown",
        },
        "instances": [],
        "approvals": [],
        "trust_proposals": [],
        "activity": [],
        "errors": [],
    }


def _budget_threshold_for_pct(pct: float) -> str:
    if pct >= 100.0:
        return "hard_stop"
    if pct >= 95.0:
        return "spawn_gated"
    if pct >= 85.0:
        return "spawn_restricted"
    if pct >= 75.0:
        return "warning"
    return "normal"


def _collect_cost_data() -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    by_instance: Dict[str, Dict[str, Any]] = {}
    aggregate: Dict[str, Any] = {
        "utilization_pct": 0.0,
        "threshold": "unknown",
        "stale_instance_ids": [],
    }
    if not _cost_reporter:
        return by_instance, aggregate

    try:
        status = _cost_reporter.get_aggregate_status()
        if isinstance(status, dict):
            aggregate.update(status)
            if status.get("instance_id"):
                by_instance[str(status["instance_id"])] = status
    except Exception as exc:
        aggregate["error"] = str(exc)
        return by_instance, aggregate

    aggregator = getattr(_cost_reporter, "_cost_aggregator", None)
    if aggregator is not None:
        try:
            for item in aggregator.get_all_instances():
                payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
                instance_id = str(payload.get("instance_id", ""))
                if instance_id:
                    by_instance[instance_id] = payload
        except Exception as exc:
            aggregate["instance_error"] = str(exc)

    return by_instance, aggregate


def _collect_local_hive_summary() -> Dict[str, int]:
    summary = {"active_agents": 0, "paused_agents": 0}
    try:
        from src.hive import api as hive_api

        registry = getattr(hive_api, "_registry", None)
        if registry is None:
            return summary
        agents = registry.list_active()
        for agent in agents:
            state = getattr(getattr(agent, "state", ""), "value", getattr(agent, "state", ""))
            state = str(state).lower()
            if state == "paused":
                summary["paused_agents"] += 1
            elif state in {"spawning", "ready", "executing", "waiting", "completing"}:
                summary["active_agents"] += 1
        if not agents and hasattr(registry, "active_count"):
            summary["active_agents"] = _safe_int(registry.active_count())
    except Exception as exc:
        logger.debug("Failed to collect local HIVE dashboard summary: %s", exc)
    return summary


def _approval_context_from_params(params: Any) -> str:
    if not params:
        return ""
    try:
        return json.dumps(params, sort_keys=True)[:240]
    except Exception:
        return str(params)[:240]


def _collect_local_approvals(instance_id: str, instance_name: str) -> list[Dict[str, Any]]:
    approvals: list[Dict[str, Any]] = []
    try:
        from src.core import governance_api

        sentry = getattr(governance_api, "_mcp_sentry", None)
        if sentry is not None:
            cleanup = getattr(sentry, "cleanup_expired", None)
            if callable(cleanup):
                cleanup()
            for approval_id, req in getattr(sentry, "pending_requests", {}).items():
                if str(req.get("status", "")).upper() != "PENDING":
                    continue
                capability = str(req.get("tool", "unknown"))
                approvals.append({
                    "id": approval_id,
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "type": "sentry",
                    "action_name": capability,
                    "risk_tier": str(req.get("risk_tier") or "T3"),
                    "capability": capability,
                    "context": _approval_context_from_params(req.get("params", {})),
                    "created_at": req.get("timestamp", ""),
                    "waiting_since": req.get("timestamp", ""),
                })

        rule_engine = getattr(governance_api, "_rule_engine", None)
        if rule_engine is not None:
            for rule in rule_engine.list_rules(status="proposed"):
                approvals.append({
                    "id": rule.id,
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "type": "apl_rule",
                    "action_name": getattr(rule, "name", "Approval learning rule"),
                    "risk_tier": "T2",
                    "capability": "approval_learning.rule",
                    "context": getattr(rule, "description", ""),
                    "created_at": getattr(rule, "created_at", ""),
                    "waiting_since": getattr(rule, "created_at", ""),
                })
    except Exception as exc:
        logger.debug("Failed to collect local approval dashboard data: %s", exc)
    return approvals


def _collect_local_trust_proposals(instance_id: str, instance_name: str) -> list[Dict[str, Any]]:
    proposals: list[Dict[str, Any]] = []
    try:
        from src.core import trust_api, governance_api

        ledger = getattr(trust_api, "_trust_ledger", None) or getattr(
            governance_api,
            "_trust_ledger",
            None,
        )
        if ledger is None:
            return proposals
        for proposal in ledger.pending_proposals():
            proposals.append({
                "id": proposal.id,
                "instance_id": instance_id,
                "instance_name": instance_name,
                "capability": proposal.capability,
                "scope": proposal.scope,
                "current_tier": int(proposal.current_tier),
                "proposed_tier": int(proposal.proposed_tier),
                "consecutive_successes": proposal.consecutive_successes,
                "status": proposal.status,
                "created_at": proposal.created_at,
            })
    except Exception as exc:
        logger.debug("Failed to collect local trust dashboard data: %s", exc)
    return proposals


def _receipt_payload_value(receipt: Any, key: str) -> str:
    for attr in ("metadata", "outputs", "inputs"):
        payload = getattr(receipt, attr, {}) or {}
        if isinstance(payload, dict) and payload.get(key):
            return str(payload[key])
    return ""


def _receipt_activity_description(receipt: Any) -> str:
    action_name = str(getattr(receipt, "action_name", "") or "").strip()
    if action_name:
        return action_name
    for key in ("description", "message", "event", "phase", "capability"):
        value = _receipt_payload_value(receipt, key)
        if value:
            return value
    return str(getattr(receipt, "action_type", "") or "receipt")


def _collect_local_activity(instance_id: str, instance_name: str, limit: int) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    try:
        from src.core.receipts_api import get_receipt_service_instance

        service = get_receipt_service_instance()
        if service is None:
            return events
        receipts = service.list(limit=limit)
        for receipt in receipts:
            metadata = getattr(receipt, "metadata", {}) or {}
            operator = (
                metadata.get("operator_id")
                or metadata.get("operator")
                or metadata.get("actor")
                or ""
            )
            events.append({
                "id": getattr(receipt, "id", ""),
                "timestamp": getattr(receipt, "timestamp", ""),
                "instance_id": instance_id,
                "instance_name": instance_name,
                "event_type": getattr(receipt, "action_type", ""),
                "description": _receipt_activity_description(receipt),
                "operator": operator,
                "status": getattr(receipt, "status", ""),
            })
    except Exception as exc:
        logger.debug("Failed to collect local dashboard activity: %s", exc)
    return events


def _collect_runtime_pause() -> Dict[str, Any]:
    try:
        from src.core.runtime_pause import get_runtime_pause_status

        status = get_runtime_pause_status()
        return status if isinstance(status, dict) else {}
    except Exception as exc:
        logger.debug("Failed to collect runtime pause status: %s", exc)
        return {}


def _collect_local_health_state() -> tuple[str, list[str]]:
    try:
        from health import api as health_api

        if getattr(health_api, "_snapshot_provider", None) is None:
            return "healthy", []
        snapshot = health_api._get_snapshot()
        reasons = [
            str(reason)
            for reason in getattr(snapshot, "degraded_reasons", []) or []
            if str(reason)
        ]
        if getattr(snapshot, "ready", False):
            return "healthy", []
        if not getattr(snapshot, "last_health_tick_at", None) and not reasons:
            return "healthy", []
        return "degraded", reasons or ["System health degraded"]
    except Exception as exc:
        logger.debug("Failed to collect local health dashboard state: %s", exc)
        return "healthy", []


def _derive_attention_state(instance: Dict[str, Any]) -> tuple[str, list[str]]:
    reasons = list(instance.get("attention_reasons", []) or [])
    heartbeat_state = str(instance.get("heartbeat_state", "")).lower()
    health = str(instance.get("health", "")).lower()
    budget_threshold = str(instance.get("budget_threshold", "")).lower()
    soul_matches_root = instance.get("soul_matches_root")
    pending_approvals = _safe_int(instance.get("pending_approvals"))
    trust_proposals = _safe_int(instance.get("trust_proposals"))
    paused_agents = _safe_int(instance.get("paused_agents"))
    runtime_errors = instance.get("runtime_errors", []) or []
    detail_status = str(instance.get("detail_status", "available")).lower()
    paused = bool(instance.get("paused")) or paused_agents > 0

    if heartbeat_state in {"critical", "lost"}:
        reasons.append(f"Heartbeat {heartbeat_state}")
    elif heartbeat_state == "warning":
        reasons.append("Heartbeat warning")

    if health in {"degraded", "error"}:
        reasons.append(f"Health {health}")

    if pending_approvals > 0:
        reasons.append(f"{pending_approvals} pending approval(s)")
    if trust_proposals > 0:
        reasons.append(f"{trust_proposals} trust proposal(s)")
    if budget_threshold in {"spawn_gated", "hard_stop", "blocked", "exceeded", "critical"}:
        reasons.append(f"Budget {budget_threshold}")
    elif budget_threshold in {"warning", "spawn_restricted", "restricted"}:
        reasons.append(f"Budget {budget_threshold}")
    if soul_matches_root is False:
        reasons.append("Soul hash differs from root")
    if detail_status != "available":
        reasons.append("Remote detail unavailable")
    if runtime_errors:
        reasons.append("Runtime errors present")
    if paused:
        reasons.append("Runtime paused")

    deduped = list(dict.fromkeys(reason for reason in reasons if reason))

    if paused:
        state = "paused"
    elif (
        heartbeat_state in {"critical", "lost"}
        or health == "error"
        or budget_threshold in {"spawn_gated", "hard_stop", "blocked", "exceeded", "critical"}
        or runtime_errors
    ):
        state = "critical"
    elif deduped:
        state = "attention"
    else:
        state = "healthy"
    return state, deduped


def _build_local_instance_snapshot(
    runtime_status: Dict[str, Any],
    cost_by_instance: Dict[str, Dict[str, Any]],
    aggregate_cost: Dict[str, Any],
) -> tuple[Dict[str, Any], list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    instance_id = _identity.instance_id if _identity else ""
    instance_name = "Local Lancelot"
    if _config and getattr(_config, "self_address", ""):
        instance_name = _display_name_from_address(_config.self_address) or instance_name

    latest_hb = _heartbeat_emitter.get_latest() if _heartbeat_emitter else None
    heartbeat_age = _elapsed_seconds(latest_hb.timestamp if latest_hb else None)
    heartbeat_state = "fresh" if latest_hb else "lost"
    soul_hash = (
        runtime_status.get("local_soul_hash")
        or (latest_hb.soul_version_hash if latest_hb else "")
        or ""
    )

    hive = _collect_local_hive_summary()
    approvals = _collect_local_approvals(instance_id, instance_name)
    trust_proposals = _collect_local_trust_proposals(instance_id, instance_name)
    activity_limit = _dashboard_config_payload()["max_recent_activity_items"]
    activity = _collect_local_activity(instance_id, instance_name, activity_limit)
    recent = activity[0] if activity else None
    pause_status = _collect_runtime_pause()
    paused = bool(pause_status.get("paused", False))
    health, health_reasons = _collect_local_health_state()
    attention_reasons = list(health_reasons)
    attention_reasons.extend(_dashboard_runtime_attention_reasons(runtime_status))

    cost = cost_by_instance.get(instance_id, {})
    budget_pct = _safe_float(
        cost.get("utilization_pct")
        or (latest_hb.budget_utilization_pct if latest_hb else 0.0)
    )
    budget_threshold = (
        str(cost.get("threshold_level") or cost.get("threshold") or "")
        or (
            str(aggregate_cost.get("threshold"))
            if aggregate_cost.get("instance_count", 1) <= 1
            else ""
        )
        or _budget_threshold_for_pct(budget_pct)
    )

    if paused:
        health = "paused"

    instance = {
        "instance_id": instance_id,
        "instance_short_id": _short_id(instance_id),
        "name": instance_name,
        "role": "self",
        "address": getattr(_config, "self_address", "") if _config else "",
        "command_center_url": "/war-room/command",
        "is_self": True,
        "state": "healthy",
        "health": health,
        "heartbeat_state": heartbeat_state,
        "heartbeat_age_s": heartbeat_age,
        "last_heartbeat_at": latest_hb.timestamp if latest_hb else None,
        "soul_version_hash": soul_hash,
        "soul_matches_root": True if soul_hash else None,
        "budget_utilization_pct": round(budget_pct, 1),
        "budget_threshold": budget_threshold,
        "active_agents": hive["active_agents"],
        "paused_agents": hive["paused_agents"],
        "pending_approvals": len(approvals),
        "trust_proposals": len(trust_proposals),
        "recent_activity": recent.get("description") if recent else "",
        "recent_activity_at": recent.get("timestamp") if recent else None,
        "attention_reasons": attention_reasons,
        "runtime_errors": list(runtime_status.get("runtime_errors", []) or []),
        "detail_status": "available",
        "paused": paused,
        "pause_reason": pause_status.get("reason"),
    }
    instance["state"], instance["attention_reasons"] = _derive_attention_state(instance)
    return instance, approvals, trust_proposals, activity


def _peer_instance_base(
    peer: Any,
    *,
    root_soul_hash: str,
    cost_by_instance: Dict[str, Dict[str, Any]],
    detail_error: str = "",
) -> Dict[str, Any]:
    from src.federation.heartbeat import compute_staleness

    peer_id = str(getattr(peer, "instance_id", ""))
    address = str(getattr(peer, "address", ""))
    metadata = getattr(peer, "metadata", {}) or {}
    instance_name = (
        str(metadata.get("instance_name", "")).strip()
        or _display_name_from_address(address)
        or f"Instance {_short_id(peer_id)}"
    )
    last_heartbeat = getattr(peer, "last_heartbeat_at", None)
    heartbeat_state, heartbeat_age = compute_staleness(
        last_heartbeat,
        warning_s=getattr(_config, "staleness_warning_s", 10.0),
        critical_s=getattr(_config, "staleness_critical_s", 20.0),
        lost_s=getattr(_config, "staleness_lost_s", 30.0),
    )
    cost = cost_by_instance.get(peer_id, {})
    budget_pct = _safe_float(cost.get("utilization_pct"))
    budget_threshold = str(
        cost.get("threshold_level")
        or cost.get("threshold")
        or _budget_threshold_for_pct(budget_pct)
    )
    soul_hash = str(getattr(peer, "soul_version_hash", "") or "")
    soul_matches_root = None
    if root_soul_hash and soul_hash:
        soul_matches_root = soul_hash == root_soul_hash

    instance = {
        "instance_id": peer_id,
        "instance_short_id": _short_id(peer_id),
        "name": instance_name,
        "role": str(getattr(peer, "role", "peer") or "peer"),
        "address": address,
        "command_center_url": _command_center_url(address),
        "is_self": False,
        "state": "healthy",
        "health": "unknown" if detail_error else "healthy",
        "heartbeat_state": heartbeat_state,
        "heartbeat_age_s": heartbeat_age if heartbeat_age >= 0 else None,
        "last_heartbeat_at": last_heartbeat,
        "soul_version_hash": soul_hash,
        "soul_matches_root": soul_matches_root,
        "budget_utilization_pct": round(budget_pct, 1),
        "budget_threshold": budget_threshold,
        "active_agents": 0,
        "paused_agents": 0,
        "pending_approvals": 0,
        "trust_proposals": 0,
        "recent_activity": "",
        "recent_activity_at": None,
        "attention_reasons": [detail_error] if detail_error else [],
        "runtime_errors": [],
        "detail_status": "unavailable" if detail_error else "topology_only",
        "paused": False,
        "pause_reason": "",
    }
    instance["state"], instance["attention_reasons"] = _derive_attention_state(instance)
    return instance


def _merge_peer_detail(base: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    passthrough_fields = {
        "health",
        "active_agents",
        "paused_agents",
        "pending_approvals",
        "trust_proposals",
        "recent_activity",
        "recent_activity_at",
        "runtime_errors",
        "paused",
        "pause_reason",
    }
    for field in passthrough_fields:
        if field in detail:
            merged[field] = detail[field]

    if detail.get("name"):
        merged["name"] = detail["name"]
    if detail.get("soul_version_hash") and not merged.get("soul_version_hash"):
        merged["soul_version_hash"] = detail["soul_version_hash"]
    if detail.get("budget_utilization_pct"):
        merged["budget_utilization_pct"] = detail["budget_utilization_pct"]
    if detail.get("budget_threshold"):
        merged["budget_threshold"] = detail["budget_threshold"]

    detail_reasons = list(detail.get("attention_reasons", []) or [])
    merged["attention_reasons"] = list(
        dict.fromkeys(list(merged.get("attention_reasons", []) or []) + detail_reasons)
    )
    merged["detail_status"] = "available"
    merged["state"], merged["attention_reasons"] = _derive_attention_state(merged)
    return merged


def _normalize_remote_rows(
    rows: Any,
    *,
    instance_id: str,
    instance_name: str,
) -> list[Dict[str, Any]]:
    normalized: list[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return normalized
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["instance_id"] = item.get("instance_id") or instance_id
        item["instance_name"] = item.get("instance_name") or instance_name
        normalized.append(item)
    return normalized


def _sort_dashboard_instances(instances: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    sort_order = _dashboard_config_payload()["card_sort_order"]
    if sort_order == "alphabetical":
        return sorted(instances, key=lambda item: str(item.get("name", "")).lower())
    if sort_order == "role":
        return sorted(instances, key=lambda item: (str(item.get("role", "")), str(item.get("name", ""))))

    severity = {"critical": 0, "attention": 1, "paused": 2, "healthy": 3}
    return sorted(
        instances,
        key=lambda item: (
            severity.get(str(item.get("state", "healthy")), 4),
            -_safe_int(item.get("pending_approvals")),
            -_safe_int(item.get("trust_proposals")),
            str(item.get("name", "")).lower(),
        ),
    )


def _build_fleet_summary(
    instances: list[Dict[str, Any]],
    *,
    aggregate_cost: Dict[str, Any],
    runtime_status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "total_instances": len(instances),
        "instances_needing_attention": sum(
            1 for item in instances if item.get("state") in {"attention", "critical", "paused"}
        ),
        "critical_instances": sum(1 for item in instances if item.get("state") == "critical"),
        "lost_instances": sum(1 for item in instances if item.get("heartbeat_state") == "lost"),
        "paused_instances": sum(1 for item in instances if item.get("state") == "paused"),
        "pending_approvals": sum(_safe_int(item.get("pending_approvals")) for item in instances),
        "trust_proposals": sum(_safe_int(item.get("trust_proposals")) for item in instances),
        "active_agents": sum(_safe_int(item.get("active_agents")) for item in instances),
        "fleet_cost_utilization_pct": round(_safe_float(aggregate_cost.get("utilization_pct")), 1),
        "budget_threshold": str(aggregate_cost.get("threshold", "unknown") or "unknown"),
        "soul_consistency": str(runtime_status.get("soul_consistency", "unknown") or "unknown"),
    }


async def _fetch_peer_dashboard_local(peer: Any) -> tuple[Optional[Dict[str, Any]], str]:
    if _transport is None or not callable(getattr(_transport, "send", None)):
        return None, "Federation transport not available for remote dashboard detail"
    address = str(getattr(peer, "address", "") or "")
    if not address:
        return None, "Peer address unavailable"
    timeout = min(_safe_float(getattr(_config, "command_timeout_s", 5.0), 5.0), 5.0)
    result = await _transport.send(
        peer_address=address,
        method="GET",
        path="/api/federation/dashboard/local",
        peer_id=str(getattr(peer, "instance_id", "")),
        timeout_override_s=timeout,
    )
    if not getattr(result, "success", False):
        status_code = getattr(result, "status_code", None)
        error = getattr(result, "error", "") or f"HTTP {status_code}"
        return None, f"Remote dashboard detail unavailable: {error}"
    body = getattr(result, "body", None)
    if not isinstance(body, dict):
        return None, "Remote dashboard detail returned an invalid payload"
    if body.get("enabled") is False:
        return None, body.get("disabled_reason") or "Remote dashboard disabled"
    return body, ""


async def _build_dashboard_snapshot(*, include_remote: bool) -> Dict[str, Any]:
    disabled_reason = _dashboard_disabled_reason()
    if disabled_reason:
        return _empty_dashboard_snapshot(enabled=False, disabled_reason=disabled_reason)

    runtime_status = _build_runtime_status()
    cost_by_instance, aggregate_cost = _collect_cost_data()
    local_instance, approvals, trust_proposals, activity = _build_local_instance_snapshot(
        runtime_status,
        cost_by_instance,
        aggregate_cost,
    )

    instances = [local_instance]
    errors: list[Dict[str, Any]] = []

    peers = _topology_registry.list_peers() if _topology_registry else []
    root_soul_hash = str(runtime_status.get("local_soul_hash", "") or "")

    if include_remote and peers:
        remote_results = await asyncio.gather(
            *[_fetch_peer_dashboard_local(peer) for peer in peers],
            return_exceptions=True,
        )
        for peer, result in zip(peers, remote_results):
            detail: Optional[Dict[str, Any]] = None
            detail_error = ""
            if isinstance(result, Exception):
                detail_error = f"Remote dashboard detail failed: {result}"
            else:
                detail, detail_error = result

            base = _peer_instance_base(
                peer,
                root_soul_hash=root_soul_hash,
                cost_by_instance=cost_by_instance,
                detail_error=detail_error,
            )

            if detail:
                remote_instances = detail.get("instances", [])
                remote_instance = remote_instances[0] if remote_instances else {}
                if isinstance(remote_instance, dict):
                    base = _merge_peer_detail(base, remote_instance)
                instance_id = str(base.get("instance_id", ""))
                instance_name = str(base.get("name", ""))
                approvals.extend(
                    _normalize_remote_rows(
                        detail.get("approvals", []),
                        instance_id=instance_id,
                        instance_name=instance_name,
                    )
                )
                trust_proposals.extend(
                    _normalize_remote_rows(
                        detail.get("trust_proposals", []),
                        instance_id=instance_id,
                        instance_name=instance_name,
                    )
                )
                activity.extend(
                    _normalize_remote_rows(
                        detail.get("activity", []),
                        instance_id=instance_id,
                        instance_name=instance_name,
                    )
                )
            elif detail_error:
                errors.append({
                    "instance_id": getattr(peer, "instance_id", ""),
                    "message": detail_error,
                })
            instances.append(base)
    elif include_remote:
        for peer in peers:
            instances.append(
                _peer_instance_base(
                    peer,
                    root_soul_hash=root_soul_hash,
                    cost_by_instance=cost_by_instance,
                )
            )

    instances = _sort_dashboard_instances(instances)
    approvals.sort(
        key=lambda item: (
            0 if str(item.get("risk_tier", "")).upper() == "T3" else 1,
            str(item.get("created_at") or item.get("waiting_since") or ""),
        )
    )
    trust_proposals.sort(key=lambda item: str(item.get("created_at", "")))
    activity.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    if not _dashboard_config_payload()["show_fleet_activity_feed"]:
        activity = []
    else:
        activity = activity[:_dashboard_config_payload()["activity_feed_max_events"]]

    return {
        "enabled": True,
        "disabled_reason": "",
        "generated_at": _utc_now_iso(),
        "command_center_path": "/war-room/command",
        "dashboard": _dashboard_config_payload(),
        "fleet": _build_fleet_summary(
            instances,
            aggregate_cost=aggregate_cost,
            runtime_status=runtime_status,
        ),
        "instances": instances,
        "approvals": approvals,
        "trust_proposals": trust_proposals,
        "activity": activity,
        "errors": errors,
    }


def _require_dashboard_enabled_for_action() -> None:
    disabled_reason = _dashboard_disabled_reason()
    if disabled_reason:
        raise HTTPException(status_code=403, detail=disabled_reason)


def _clean_decision_reason(reason: str) -> str:
    cleaned = str(reason or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="A decision reason is required")
    return cleaned


def _dashboard_operator_identity_from_request(request: Request):
    from src.core.auth_api import resolve_authenticated_identity

    identity = resolve_authenticated_identity(request)
    if identity is None or not getattr(identity, "is_valid", False):
        raise HTTPException(status_code=401, detail="Operator identity is required")
    return identity


def _operator_identity_from_payload(payload: Dict[str, Any]):
    from src.core.operator_identity import OperatorIdentity

    try:
        identity = OperatorIdentity.from_dict(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid operator_identity payload") from exc
    if not identity.is_valid:
        raise HTTPException(status_code=401, detail="Valid operator_identity is required")
    return identity


def _require_dashboard_operator_decision_capabilities(request: Request) -> None:
    if getattr(request.state, "federation_auth_mode", "") == "root_peer":
        return

    from src.core.auth_api import request_has_capability

    missing = [
        capability
        for capability in ("federation.admin", "governance.admin")
        if not request_has_capability(request, capability)
    ]
    if missing:
        raise HTTPException(status_code=403, detail=f"Missing capability: {missing[0]}")


def _apply_local_dashboard_decision(
    approval_id: str,
    *,
    decision: str,
    reason: str,
    identity: Any,
) -> Dict[str, Any]:
    from src.core import governance_api

    normalized = str(decision or "").lower()
    if normalized == "approve":
        result = governance_api._approve_item_direct(
            approval_id,
            reason=reason,
            identity=identity,
        )
    elif normalized == "deny":
        result = governance_api._deny_item_direct(
            approval_id,
            reason=reason,
            identity=identity,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported decision: {decision}")

    if result is None:
        raise HTTPException(status_code=404, detail=f"Approval item {approval_id} not found")
    if not isinstance(result, dict):
        return {"status": normalized, "id": approval_id, "result": result}
    return result


def _receipt_action_type_for_dashboard_decision(result: Dict[str, Any], decision: str):
    from src.shared.receipts import ActionType

    result_type = str(result.get("type", "")).lower()
    approved = decision == "approve"
    if result_type == "sentry":
        return ActionType.MCP_T3_APPROVED if approved else ActionType.MCP_T3_REJECTED
    if result_type == "apl_rule":
        return ActionType.APL_RULE_APPROVED if approved else ActionType.APL_RULE_REJECTED
    return ActionType.T3_APPROVED if approved else ActionType.T3_REJECTED


def _emit_dashboard_proxy_receipt(
    *,
    identity: Any,
    decision: str,
    target_instance_id: str,
    approval_id: str,
    result: Dict[str, Any],
) -> None:
    from src.core.governance_receipts import emit_governance_receipt_for_identity

    action_type = _receipt_action_type_for_dashboard_decision(result, decision)
    source_instance_id = _identity.instance_id if _identity else ""
    emit_governance_receipt_for_identity(
        identity,
        action_type,
        action_name=f"federation_approval_proxy_{decision}",
        inputs={
            "approval_id": approval_id,
            "target_instance_id": target_instance_id,
            "source_instance_id": source_instance_id,
            "decision": decision,
            "result_type": result.get("type", ""),
        },
        outputs={"remote_result": result},
        metadata={
            "federated_proxy": True,
            "target_instance_id": target_instance_id,
            "source_instance_id": source_instance_id,
            "operator_id": getattr(identity, "operator_id", ""),
        },
    )


def _find_dashboard_peer(instance_id: str):
    if not _topology_registry:
        raise HTTPException(status_code=404, detail="Federation topology is unavailable")
    peer = _topology_registry.get_peer(instance_id)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Federation instance {instance_id} not found")
    return peer


async def _send_dashboard_decision_to_peer(
    peer: Any,
    *,
    approval_id: str,
    decision: str,
    reason: str,
    identity: Any,
) -> Dict[str, Any]:
    if _transport is None or not callable(getattr(_transport, "send", None)):
        raise HTTPException(status_code=503, detail="Federation transport not available")

    address = str(getattr(peer, "address", "") or "")
    if not address:
        raise HTTPException(status_code=503, detail="Federation peer address unavailable")

    peer_id = str(getattr(peer, "instance_id", "") or "")
    timeout = min(_safe_float(getattr(_config, "command_timeout_s", 5.0), 5.0), 10.0)
    result = await _transport.send(
        peer_address=address,
        method="POST",
        path=(
            "/api/federation/dashboard/local/approvals/"
            f"{quote(approval_id, safe='')}/{decision}"
        ),
        body={
            "reason": reason,
            "operator_identity": identity.to_dict(),
            "source_instance_id": _identity.instance_id if _identity else "",
        },
        peer_id=peer_id,
        timeout_override_s=timeout,
    )

    body = getattr(result, "body", None)
    if not getattr(result, "success", False):
        status_code = getattr(result, "status_code", 0) or 502
        if status_code < 400 or status_code > 599:
            status_code = 502
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
        detail = detail or getattr(result, "error", "") or f"HTTP {status_code}"
        raise HTTPException(status_code=status_code, detail=detail)

    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="Remote approval decision returned invalid payload")
    return body


async def _handle_federation_dashboard_decision(
    request: Request,
    *,
    instance_id: str,
    approval_id: str,
    decision: str,
    reason: str,
) -> JSONResponse:
    if not _initialized:
        return _not_initialized()
    _require_dashboard_enabled_for_action()

    normalized_decision = str(decision or "").lower()
    clean_reason = _clean_decision_reason(reason)
    identity = _dashboard_operator_identity_from_request(request)
    local_instance_id = _identity.instance_id if _identity else ""

    if instance_id == local_instance_id:
        result = _apply_local_dashboard_decision(
            approval_id,
            decision=normalized_decision,
            reason=clean_reason,
            identity=identity,
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "decision": normalized_decision,
                "instance_id": instance_id,
                "approval_id": approval_id,
                "result": result,
            },
        )

    peer = _find_dashboard_peer(instance_id)
    remote_body = await _send_dashboard_decision_to_peer(
        peer,
        approval_id=approval_id,
        decision=normalized_decision,
        reason=clean_reason,
        identity=identity,
    )
    if remote_body.get("success") is False:
        raise HTTPException(
            status_code=502,
            detail=remote_body.get("error") or remote_body.get("detail") or "Remote decision failed",
        )

    remote_result = remote_body.get("result")
    if not isinstance(remote_result, dict):
        remote_result = {"result": remote_result}
    _emit_dashboard_proxy_receipt(
        identity=identity,
        decision=normalized_decision,
        target_instance_id=instance_id,
        approval_id=approval_id,
        result=remote_result,
    )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "decision": normalized_decision,
            "instance_id": instance_id,
            "approval_id": approval_id,
            "result": remote_result,
            "remote": remote_body,
        },
    )


def _handle_local_dashboard_decision(
    request: Request,
    *,
    approval_id: str,
    decision: str,
    body: FederatedDashboardDecisionRequest,
) -> JSONResponse:
    if not _initialized:
        return _not_initialized()
    _require_dashboard_enabled_for_action()
    _require_dashboard_operator_decision_capabilities(request)

    identity = (
        _operator_identity_from_payload(body.operator_identity)
        if body.operator_identity
        else _dashboard_operator_identity_from_request(request)
    )
    clean_reason = _clean_decision_reason(body.reason)
    normalized_decision = str(decision or "").lower()
    result = _apply_local_dashboard_decision(
        approval_id,
        decision=normalized_decision,
        reason=clean_reason,
        identity=identity,
    )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "decision": normalized_decision,
            "instance_id": _identity.instance_id if _identity else "",
            "approval_id": approval_id,
            "source_instance_id": body.source_instance_id,
            "result": result,
        },
    )


def _not_initialized() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "Federation subsystem not initialized"},
    )


def _no_transport(endpoint: str) -> JSONResponse:
    """Transport layer not yet initialized."""
    return JSONResponse(
        status_code=503,
        content={
            "error": f"{endpoint}: transport layer not initialized",
            "hint": "Federation transport starts after peer registration",
        },
    )


# ═══════════════════════════════════════════════════════════════
# Auth Verification Helper
# ═══════════════════════════════════════════════════════════════

async def _require_valid_peer_request(request: Request) -> None:
    """Require a valid Ed25519-signed federation peer request."""
    if not _auth:
        return

    body = await request.body()
    headers = dict(request.headers)

    result = _auth.verify_request(
        method=request.method,
        path=request.url.path,
        body=body,
        headers=headers,
    )

    if not result.valid:
        logger.warning(
            "Federation auth failed: %s (from %s)",
            result.reason, result.instance_id or "unknown",
        )
        raise HTTPException(
            status_code=401,
            detail=f"Federation authentication failed: {result.reason}",
        )
    request.state.federation_peer_instance_id = result.instance_id


class UpdateFederationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    self_address: str = ""


async def _require_operator_or_valid_peer_request(request: Request) -> None:
    """Allow either a signed peer request or a normal authenticated operator request."""
    try:
        require_authenticated_request(request)
        return
    except HTTPException as exc:
        if exc.status_code not in {401, 503}:
            raise
    await _require_valid_peer_request(request)


async def _require_operator_or_root_peer_request(request: Request) -> None:
    """Allow either a local operator or a signed ROOT federation peer request."""
    try:
        require_authenticated_request(request)
        request.state.federation_auth_mode = "operator"
        return
    except HTTPException as exc:
        if exc.status_code not in {401, 503}:
            raise

    if not _auth:
        raise HTTPException(status_code=401, detail="Federation authentication is required")

    await _require_valid_peer_request(request)
    peer_id = str(getattr(request.state, "federation_peer_instance_id", "") or "")
    if not peer_id:
        raise HTTPException(status_code=401, detail="Signed federation peer identity is required")

    peer = _topology_registry.get_peer(peer_id) if _topology_registry else None
    role = str(getattr(peer, "role", "") or "").lower() if peer else ""
    if role != "root":
        raise HTTPException(
            status_code=403,
            detail="Dashboard detail and decisions require ROOT peer authority",
        )
    request.state.federation_auth_mode = "root_peer"


# ═══════════════════════════════════════════════════════════════
# Category 1: State Stream
# ═══════════════════════════════════════════════════════════════

@router.get("/stream")
async def heartbeat_stream(
    _peer: None = Depends(_require_valid_peer_request),
):
    """SSE endpoint — continuous heartbeat stream.

    Clients connect and receive heartbeats as server-sent events.
    """
    if not _initialized or not _heartbeat_emitter:
        return _not_initialized()

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def on_heartbeat(hb):
            try:
                queue.put_nowait(hb)
            except asyncio.QueueFull:
                logger.warning("Federation heartbeat stream queue full; dropping heartbeat event")

        _heartbeat_emitter.subscribe(on_heartbeat)
        try:
            while True:
                hb = await queue.get()
                data = json.dumps(hb.to_dict())
                yield f"event: heartbeat\ndata: {data}\n\n"
        except asyncio.CancelledError:
            logger.debug("Federation heartbeat stream cancelled")
        finally:
            _heartbeat_emitter.unsubscribe(on_heartbeat)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/heartbeat")
async def latest_heartbeat(
    _authn: None = Depends(require_authenticated_request),
):
    """Return the most recent heartbeat snapshot."""
    if not _initialized or not _heartbeat_emitter:
        return _not_initialized()

    latest = _heartbeat_emitter.get_latest()
    if latest is None:
        return JSONResponse(
            status_code=200,
            content={"heartbeat": None, "message": "No heartbeats emitted yet"},
        )

    return JSONResponse(status_code=200, content={"heartbeat": latest.to_dict()})


# ═══════════════════════════════════════════════════════════════
# Category 2: Command API
# ═══════════════════════════════════════════════════════════════

@router.post("/command")
async def receive_command(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a governance command from an authorized peer."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("command")

    body = (await _parse_request_model(request, FederationCommandRequest)).model_dump(exclude_none=True)
    result = _command_relay.handle_kill_command(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


@router.post("/killswitch")
async def receive_killswitch(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a federation kill switch command."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("killswitch")

    body = (await _parse_request_model(request, FederationCommandRequest)).model_dump(exclude_none=True)
    result = _command_relay.handle_kill_command(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


@router.post("/pause")
async def receive_pause(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a federation pause signal."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("pause")

    body = (await _parse_request_model(request, PauseSignalRequest)).model_dump(exclude_none=True)
    result = _command_relay.handle_pause(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


@router.post("/resume")
async def receive_resume(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a federation resume signal."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("resume")

    body = (await _parse_request_model(request, ResumeSignalRequest)).model_dump(exclude_none=True)
    result = _command_relay.handle_resume(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


# ═══════════════════════════════════════════════════════════════
# Category 3: Handoff API
# ═══════════════════════════════════════════════════════════════

@router.post("/handoff/initiate")
async def initiate_handoff(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a workflow handoff from a source peer."""
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("handoff/initiate")

    body = (await _parse_request_model(request, HandoffInitiationRequest)).model_dump(exclude_none=True)
    result = _handoff_protocol.handle_handoff_initiation(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.post("/soul/confirm")
async def confirm_soul_update(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a peer confirmation for a T3 Soul propagation event."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul/confirm")

    body = (await _parse_request_model(request, SoulConfirmationRequest)).model_dump(exclude_none=True)
    result = await _soul_transport.handle_soul_confirmation(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.post("/handoff/complete")
async def complete_handoff(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a handoff completion report from a target peer."""
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("handoff/complete")

    body = (await _parse_request_model(request, CompletionReportRequest)).model_dump(exclude_none=True)
    result = _handoff_protocol.handle_completion_report(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    return JSONResponse(status_code=200, content=result)


@router.get("/handoff/{handoff_id}")
async def get_handoff(
    handoff_id: str,
    _authn: None = Depends(require_authenticated_request),
):
    """Get status of a specific handoff."""
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("handoff")

    status = _handoff_protocol.get_handoff_status(handoff_id)
    if status is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Handoff {handoff_id} not found"},
        )
    return JSONResponse(status_code=200, content=status)


# ═══════════════════════════════════════════════════════════════
# Category 4: Discovery API
# ═══════════════════════════════════════════════════════════════

@router.get("/identity")
async def get_identity(
    _authn: None = Depends(require_authenticated_request),
):
    """Return this instance's public federation identity."""
    if not _initialized or not _identity:
        return _not_initialized()

    return JSONResponse(status_code=200, content=_identity.to_public_dict())


@router.get("/status")
async def get_status(
    _authn: None = Depends(require_authenticated_request),
):
    """Return federation status summary."""
    if not _initialized:
        return _not_initialized()

    latest_hb = _heartbeat_emitter.get_latest() if _heartbeat_emitter else None
    peer_count = 0
    if _topology_registry:
        peer_count = len(_topology_registry.list_peers())
    runtime_status = _build_runtime_status()

    return JSONResponse(status_code=200, content={
        "enabled": True,
        "instance_id": _identity.instance_id if _identity else "",
        "fingerprint": _identity.fingerprint if _identity else "",
        "public_key": _identity.public_key_hex() if _identity else "",
        "deployment_mode": latest_hb.deployment_mode if latest_hb else "standalone",
        "peer_count": peer_count,
        "soul_consistency": runtime_status["soul_consistency"],
        "local_soul_hash": runtime_status["local_soul_hash"],
        "active_propagations": runtime_status["active_propagations"],
        "cost_threshold": runtime_status["cost_threshold"],
        "divergence_state": runtime_status["divergence_state"],
        "divergence_duration_s": runtime_status["divergence_duration_s"],
        "reconciliation": runtime_status["reconciliation"],
        "heartbeat_interval_s": _config.heartbeat_interval_s if _config else 2.0,
        "tls_required": _config.tls_required if _config else False,
        "self_address": _config.self_address if _config else "",
        "last_heartbeat": latest_hb.timestamp if latest_hb else None,
        "transport_ready": (
            runtime_status["transport_started"]
            and runtime_status["heartbeat_mesh_running"]
            and runtime_status["cost_reporter_running"]
        ),
        "transport_started": runtime_status["transport_started"],
        "heartbeat_mesh_running": runtime_status["heartbeat_mesh_running"],
        "cost_reporter_running": runtime_status["cost_reporter_running"],
        "runtime_degraded": runtime_status["runtime_degraded"],
        "degraded_reasons": runtime_status["degraded_reasons"],
        "runtime_errors": runtime_status["runtime_errors"],
        "subscription_status": runtime_status["subscription_status"],
        "subscription_stream_outcome": runtime_status["subscription_stream_outcome"],
        "subscription_stream_errors": runtime_status["subscription_stream_errors"],
        "circuit_breaker_summary": runtime_status["circuit_breaker_summary"],
        "stale_instance_ids": runtime_status["stale_instance_ids"],
        "divergence_evaluation_failed": runtime_status["divergence_evaluation_failed"],
        "divergence_status_error": runtime_status["divergence_status_error"],
    })


@router.get("/settings")
async def get_federation_settings(
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Return editable local federation settings."""
    if not _initialized:
        return _not_initialized()

    return JSONResponse(status_code=200, content={
        "instance_id": _identity.instance_id if _identity else "",
        "fingerprint": _identity.fingerprint if _identity else "",
        "public_key": _identity.public_key_hex() if _identity else "",
        "self_address": _config.self_address if _config else "",
        "deployment_mode": _topology_registry.deployment_mode.value if _topology_registry else "standalone",
        "restart_required": True,
    })


@router.put("/settings")
async def update_federation_settings(
    req: UpdateFederationSettingsRequest,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Persist editable local federation settings."""
    global _config
    if not _initialized:
        return _not_initialized()

    normalized = req.self_address.strip().rstrip("/")
    if normalized:
        from urllib.parse import urlparse
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="self_address must be a valid http(s) URL")

    current = _config or FederationConfig()
    updated = current.model_copy(update={"self_address": normalized})
    save_federation_config(updated)

    _config = updated

    return JSONResponse(status_code=200, content={
        "saved": True,
        "self_address": updated.self_address,
        "restart_required": True,
        "message": "Federation settings saved. Restart the federation subsystem or lancelot-core to apply runtime transport changes.",
    })


@router.get("/health")
async def get_federation_health(
    _authn: None = Depends(require_authenticated_request),
):
    """Return federation health summary for War Room."""
    if not _initialized:
        return JSONResponse(status_code=200, content={
            "total_peers": 0, "healthy": 0, "warning": 0,
            "critical": 0, "lost": 0, "deployment_mode": "standalone",
        })

    runtime_status = _build_runtime_status()
    if _topology_registry:
        summary = _topology_registry.get_health_summary()
        summary.update({
            "runtime_degraded": runtime_status["runtime_degraded"],
            "degraded_reasons": runtime_status["degraded_reasons"],
            "transport_started": runtime_status["transport_started"],
            "heartbeat_mesh_running": runtime_status["heartbeat_mesh_running"],
            "cost_reporter_running": runtime_status["cost_reporter_running"],
            "subscription_status": runtime_status["subscription_status"],
            "subscription_stream_outcome": runtime_status["subscription_stream_outcome"],
            "subscription_stream_errors": runtime_status["subscription_stream_errors"],
            "circuit_breaker_summary": runtime_status["circuit_breaker_summary"],
            "stale_instance_ids": runtime_status["stale_instance_ids"],
            "divergence_state": runtime_status["divergence_state"],
            "divergence_evaluation_failed": runtime_status["divergence_evaluation_failed"],
            "divergence_status_error": runtime_status["divergence_status_error"],
            "active_propagation_count": len(runtime_status["active_propagations"]),
        })
        return JSONResponse(status_code=200, content=summary)

    return JSONResponse(status_code=200, content={
        "total_peers": 0, "healthy": 0, "warning": 0,
        "critical": 0, "lost": 0, "deployment_mode": "standalone",
        "runtime_degraded": runtime_status["runtime_degraded"],
        "degraded_reasons": runtime_status["degraded_reasons"],
        "transport_started": runtime_status["transport_started"],
        "heartbeat_mesh_running": runtime_status["heartbeat_mesh_running"],
        "cost_reporter_running": runtime_status["cost_reporter_running"],
        "subscription_status": runtime_status["subscription_status"],
        "subscription_stream_outcome": runtime_status["subscription_stream_outcome"],
        "subscription_stream_errors": runtime_status["subscription_stream_errors"],
        "circuit_breaker_summary": runtime_status["circuit_breaker_summary"],
        "stale_instance_ids": runtime_status["stale_instance_ids"],
        "divergence_state": runtime_status["divergence_state"],
        "divergence_evaluation_failed": runtime_status["divergence_evaluation_failed"],
        "divergence_status_error": runtime_status["divergence_status_error"],
        "active_propagation_count": len(runtime_status["active_propagations"]),
    })


@router.get("/dashboard")
async def get_federation_dashboard(
    _authn: None = Depends(require_authenticated_request),
):
    """Return the operator fleet dashboard snapshot for this control plane."""
    if not _initialized:
        return _not_initialized()

    snapshot = await _build_dashboard_snapshot(include_remote=True)
    return JSONResponse(status_code=200, content=snapshot)


@router.get("/dashboard/stream")
async def stream_federation_dashboard(
    _authn: None = Depends(require_authenticated_request),
):
    """Stream live fleet dashboard snapshots for the operator control plane."""
    if not _initialized:
        return _not_initialized()

    async def event_generator():
        while True:
            try:
                snapshot = await _build_dashboard_snapshot(include_remote=True)
                yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
                interval = _safe_float(
                    snapshot.get("dashboard", {}).get("stream_interval_s"),
                    3.0,
                )
                await asyncio.sleep(max(1.0, min(interval, 30.0)))
            except asyncio.CancelledError:
                logger.debug("Federation dashboard stream cancelled")
                raise
            except Exception as exc:
                logger.warning("Federation dashboard stream snapshot failed: %s", exc)
                payload = {"error": str(exc), "generated_at": _utc_now_iso()}
                yield f"event: dashboard_error\ndata: {json.dumps(payload)}\n\n"
                await asyncio.sleep(3.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/dashboard/local")
async def get_local_federation_dashboard(
    request: Request,
    _authn_or_peer: None = Depends(_require_operator_or_root_peer_request),
):
    """Return this instance's local dashboard detail for an operator or ROOT peer."""
    if not _initialized:
        return _not_initialized()

    snapshot = await _build_dashboard_snapshot(include_remote=False)
    return JSONResponse(status_code=200, content=snapshot)


@router.post("/dashboard/instances/{instance_id}/approvals/{approval_id}/approve")
async def approve_federation_dashboard_approval(
    request: Request,
    instance_id: str,
    approval_id: str,
    body: DashboardDecisionRequest,
    _authn: None = Depends(require_authenticated_request),
    _federation_capability: None = Depends(require_operator_capability("federation.admin")),
    _governance_capability: None = Depends(require_operator_capability("governance.admin")),
):
    """Approve a pending governance item on any federated dashboard instance."""
    return await _handle_federation_dashboard_decision(
        request,
        instance_id=instance_id,
        approval_id=approval_id,
        decision="approve",
        reason=body.reason,
    )


@router.post("/dashboard/instances/{instance_id}/approvals/{approval_id}/deny")
async def deny_federation_dashboard_approval(
    request: Request,
    instance_id: str,
    approval_id: str,
    body: DashboardDecisionRequest,
    _authn: None = Depends(require_authenticated_request),
    _federation_capability: None = Depends(require_operator_capability("federation.admin")),
    _governance_capability: None = Depends(require_operator_capability("governance.admin")),
):
    """Deny a pending governance item on any federated dashboard instance."""
    return await _handle_federation_dashboard_decision(
        request,
        instance_id=instance_id,
        approval_id=approval_id,
        decision="deny",
        reason=body.reason,
    )


@router.post("/dashboard/local/approvals/{approval_id}/approve")
async def approve_local_dashboard_approval(
    request: Request,
    approval_id: str,
    body: FederatedDashboardDecisionRequest,
    _authn_or_peer: None = Depends(_require_operator_or_root_peer_request),
):
    """Approve a local pending governance item for a federated ROOT dashboard."""
    return _handle_local_dashboard_decision(
        request,
        approval_id=approval_id,
        decision="approve",
        body=body,
    )


@router.post("/dashboard/local/approvals/{approval_id}/deny")
async def deny_local_dashboard_approval(
    request: Request,
    approval_id: str,
    body: FederatedDashboardDecisionRequest,
    _authn_or_peer: None = Depends(_require_operator_or_root_peer_request),
):
    """Deny a local pending governance item for a federated ROOT dashboard."""
    return _handle_local_dashboard_decision(
        request,
        approval_id=approval_id,
        decision="deny",
        body=body,
    )


@router.get("/peers")
async def get_peers(
    _authn: None = Depends(require_authenticated_request),
):
    """Return list of federation peers."""
    if not _initialized or not _topology_registry:
        return JSONResponse(status_code=200, content=[])

    peers = [p.to_dict() for p in _topology_registry.list_peers()]
    return JSONResponse(status_code=200, content=peers)


@router.get("/topology")
async def get_topology(
    _authn: None = Depends(require_authenticated_request),
):
    """Return current federation topology (peer registry)."""
    if not _initialized:
        return _not_initialized()

    peers = []
    if _topology_registry:
        peers = [p.to_dict() for p in _topology_registry.list_peers()]

    return JSONResponse(status_code=200, content={
        "deployment_mode": "standalone" if not peers else "federated",
        "peer_count": len(peers),
        "peers": peers,
    })


@router.get("/soul/hash")
async def get_soul_hash(
    request: Request,
    _authn_or_peer: None = Depends(_require_operator_or_valid_peer_request),
):
    """Return current Soul version hash for handshake verification."""
    if not _initialized:
        return _not_initialized()

    soul_hash = ""
    if _soul_transport is not None:
        try:
            soul_hash = _soul_transport.get_local_soul_hash() or ""
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "error": f"Failed to resolve live runtime Soul hash: {exc}",
                    "instance_id": _identity.instance_id if _identity else None,
                    "soul_version_hash": "",
                },
            )

    if not soul_hash and _heartbeat_emitter is not None:
        latest_hb = _heartbeat_emitter.get_latest()
        if latest_hb:
            soul_hash = latest_hb.soul_version_hash or ""

    if not soul_hash:
        return JSONResponse(
            status_code=503,
            content={
                "error": "No active runtime Soul hash available",
                "instance_id": _identity.instance_id if _identity else None,
                "soul_version_hash": "",
            },
        )

    return JSONResponse(status_code=200, content={
        "soul_version_hash": soul_hash,
        "instance_id": _identity.instance_id if _identity else None,
    })


# ═══════════════════════════════════════════════════════════════
# Category 5: Soul API
# ═══════════════════════════════════════════════════════════════

@router.post("/soul/handshake")
async def soul_handshake(
    request: Request,
    _authn_or_peer: None = Depends(_require_operator_or_valid_peer_request),
):
    """Perform Soul version handshake with a peer."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul/handshake")

    body = (
        await _parse_request_model(request, SoulHandshakeRequest, allow_empty=True)
    ).model_dump(exclude_none=True)

    result = _soul_transport.handle_handshake(body)
    return JSONResponse(status_code=200, content=result)


@router.get("/soul")
async def get_soul(
    request: Request,
    _authn_or_peer: None = Depends(_require_operator_or_valid_peer_request),
):
    """Return this instance's full Soul document for peer fetch."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul")

    result = _soul_transport.handle_soul_fetch()
    return JSONResponse(status_code=200, content=result)


@router.post("/soul/update")
async def receive_soul_update(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a Soul version push from a peer."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul/update")

    body = (await _parse_request_model(request, SoulUpdateRequest)).model_dump(exclude_none=True)
    result = _soul_transport.handle_soul_push(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


# ═══════════════════════════════════════════════════════════════
# Category 6: Peer Registration
# ═══════════════════════════════════════════════════════════════

@router.post("/peer/register")
async def register_peer(request: Request):
    """Register a peer in this instance's topology."""
    if not _initialized:
        return _not_initialized()
    if not _peer_protocol:
        return _no_transport("peer/register")

    body = (await _parse_request_model(request, PeerRegistrationRequest)).model_dump(exclude_none=True)
    result = await _peer_protocol.handle_registration_request(body)
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.post("/peer/confirm")
async def confirm_peer_registration(request: Request):
    """Complete the mutual confirm leg for a pending peer registration."""
    if not _initialized:
        return _not_initialized()
    if not _peer_protocol:
        return _no_transport("peer/confirm")

    body = (await _parse_request_model(request, PeerConfirmationRequest)).model_dump(exclude_none=True)
    result = _peer_protocol.handle_registration_confirm(body)
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.delete("/peer/{instance_id}")
async def remove_peer(
    instance_id: str,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Remove a registered peer."""
    if not _initialized:
        return _not_initialized()
    if not _peer_protocol:
        return _no_transport("peer/remove")

    result = _peer_protocol.handle_peer_removal(instance_id)
    return JSONResponse(status_code=200, content=result)


# ═══════════════════════════════════════════════════════════════
# Category 7: Budget API
# ═══════════════════════════════════════════════════════════════

@router.get("/budget")
async def get_budget(
    _authn: None = Depends(require_authenticated_request),
):
    """Return current budget snapshot."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget")

    result = _cost_reporter.get_aggregate_status()
    if result.get("error"):
        return JSONResponse(status_code=503, content=result)
    return JSONResponse(status_code=200, content=result)


@router.post("/budget/report")
async def receive_budget_report(
    request: Request,
    _peer: None = Depends(_require_valid_peer_request),
):
    """Receive a cost report from a peer."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget/report")

    body = (await _parse_request_model(request, BudgetReportRequest)).model_dump(exclude_none=True)
    result = _cost_reporter.handle_cost_report(
        body,
        authenticated_instance_id=getattr(
            request.state,
            "federation_peer_instance_id",
            "",
        ),
    )
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.get("/budget/threshold")
async def get_budget_threshold(
    _authn: None = Depends(require_authenticated_request),
):
    """Return current budget threshold level."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget/threshold")

    result = _cost_reporter.get_aggregate_status()
    if result.get("error"):
        return JSONResponse(
            status_code=503,
            content={
                "threshold": "unknown",
                "error": result.get("error"),
            },
        )

    threshold = result.get("threshold", "unknown")
    return JSONResponse(status_code=200, content={"threshold": threshold})


# ═══════════════════════════════════════════════════════════════
# Category 8: Operator Management API (initiator-side actions)
# ═══════════════════════════════════════════════════════════════

@router.post("/manage/register-peer")
async def manage_register_peer(
    request: Request,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Operator action: Initiate peer registration handshake.

    POST body: {"target_address": "http://peer:8000", "role": "child"}
    """
    if not _initialized:
        return _not_initialized()
    if not _peer_protocol:
        return _no_transport("manage/register-peer")

    body = await _parse_request_model(request, ManageRegisterPeerRequest)
    target_address = body.target_address
    target_role = body.role

    if not target_address:
        return JSONResponse(status_code=400, content={
            "error": "target_address is required",
        })

    result = await _peer_protocol.initiate_registration(
        target_address=target_address,
        target_role=target_role,
    )

    return JSONResponse(
        status_code=200 if result.success else 400,
        content={
            "success": result.success,
            "peer_instance_id": result.peer_instance_id,
            "peer_fingerprint": result.peer_fingerprint,
            "mutual": result.mutual,
            "error": result.error,
        },
    )


@router.post("/manage/handoff")
async def manage_initiate_handoff(
    request: Request,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Operator action: Initiate a task handoff to a peer.

    POST body: {
        "target_instance_id": "...",
        "task_context": {...},
        "soul_context": {...},
        "contract": {...},
        "federation_quest_id": "..."
    }
    """
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("manage/handoff")

    body = await _parse_request_model(request, ManageHandoffRequest)
    target_id = body.target_instance_id

    if not target_id:
        return JSONResponse(status_code=400, content={
            "error": "target_instance_id is required",
        })

    handoff_result = await _handoff_protocol.initiate_handoff(
        target_instance_id=target_id,
        task_context=body.task_context,
        soul_context=body.soul_context,
        contract=body.contract,
        federation_quest_id=body.federation_quest_id,
    )

    status = 200 if handoff_result.success else 400
    return JSONResponse(status_code=status, content={
        "success": handoff_result.success,
        "handoff_id": handoff_result.handoff_id,
        "state": handoff_result.state,
        "target_instance_id": handoff_result.target_instance_id,
        "error": handoff_result.error,
    })


@router.post("/manage/complete-handoff")
async def manage_complete_handoff(
    request: Request,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Operator action: Report completion of a handoff to the source peer.

    POST body: {
        "handoff_id": "...",
        "target_instance_id": "...",
        "result": {...},
        "federation_quest_id": "..."
    }
    """
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("manage/complete-handoff")

    body = await _parse_request_model(request, ManageCompleteHandoffRequest)
    handoff_id = body.handoff_id

    if not handoff_id:
        return JSONResponse(status_code=400, content={
            "error": "handoff_id is required",
        })

    success = await _handoff_protocol.report_completion(
        handoff_id=handoff_id,
        result=body.result,
        receipts=body.receipts,
    )

    return JSONResponse(
        status_code=200 if success else 400,
        content={"success": success, "handoff_id": handoff_id},
    )


@router.post("/manage/kill")
async def manage_propagate_kill(
    request: Request,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Operator action: Propagate kill command to peers.

    POST body: {
        "command": {"command_id": "...", "command_type": "emergency_stop", ...},
        "target_ids": ["peer-id-1"] (optional, defaults to all peers)
    }
    """
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("manage/kill")

    body = await _parse_request_model(request, ManageKillRequest)
    command = body.command.model_dump(exclude_none=True)
    target_ids = body.target_ids

    # Build command_data in the format the relay expects
    command_data = {**command}
    if target_ids:
        command_data["target_instance_ids"] = target_ids
    command_data["issuer_instance_id"] = _identity.instance_id

    try:
        outcome = await _command_relay.issue_and_propagate_kill(command_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return JSONResponse(status_code=200, content=outcome)


# ═══════════════════════════════════════════════════════════════
# Category 9: Audit API
# ═══════════════════════════════════════════════════════════════

@router.get("/audit")
async def get_audit_entries(
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
    quest_id: str = "",
    instance_id: str = "",
    event_type: str = "",
    limit: int = 100,
):
    """Query federation audit trail entries."""
    if not _initialized:
        return _not_initialized()
    if not _audit_engine:
        return _no_transport("audit")

    entries = _audit_engine.query(
        federation_quest_id=quest_id or None,
        instance_id=instance_id or None,
        event_type=event_type or None,
        limit=limit,
    )

    return JSONResponse(status_code=200, content={
        "entries": [e.to_dict() for e in entries],
        "total": len(entries),
    })


@router.get("/audit/summary")
async def get_audit_summary(
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Get audit engine summary statistics."""
    if not _initialized:
        return _not_initialized()
    if not _audit_engine:
        return _no_transport("audit/summary")

    return JSONResponse(status_code=200, content=_audit_engine.get_summary())


@router.get("/audit/quest/{quest_id}")
async def get_quest_timeline(
    quest_id: str,
    _authn: None = Depends(require_authenticated_request),
    _capability: None = Depends(require_operator_capability("federation.admin")),
):
    """Reconstruct the forensic timeline for a federation quest."""
    if not _initialized:
        return _not_initialized()
    if not _audit_engine:
        return _no_transport("audit/quest")

    timeline = _audit_engine.reconstruct_quest(quest_id)
    return JSONResponse(status_code=200, content=timeline.to_dict())
