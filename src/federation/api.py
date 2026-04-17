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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse, StreamingResponse
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability
from src.federation.config import FederationConfig, save_federation_config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/federation",
    tags=["federation"],
)

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
                stale_instance_ids = list(budget_status.get("stale_instance_ids", []) or [])
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
                pass

        _heartbeat_emitter.subscribe(on_heartbeat)
        try:
            while True:
                hb = await queue.get()
                data = json.dumps(hb.to_dict())
                yield f"event: heartbeat\ndata: {data}\n\n"
        except asyncio.CancelledError:
            pass
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = {}
    try:
        if request.headers.get("content-length") not in (None, "", "0"):
            body = await request.json()
    except Exception:
        body = {}

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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
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

    body = await request.json()
    target_address = body.get("target_address", "")
    target_role = body.get("role", "peer")

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

    body = await request.json()
    target_id = body.get("target_instance_id", "")

    if not target_id:
        return JSONResponse(status_code=400, content={
            "error": "target_instance_id is required",
        })

    handoff_result = await _handoff_protocol.initiate_handoff(
        target_instance_id=target_id,
        task_context=body.get("task_context", {}),
        soul_context=body.get("soul_context", {}),
        contract=body.get("contract", {}),
        federation_quest_id=body.get("federation_quest_id", ""),
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

    body = await request.json()
    handoff_id = body.get("handoff_id", "")

    if not handoff_id:
        return JSONResponse(status_code=400, content={
            "error": "handoff_id is required",
        })

    success = await _handoff_protocol.report_completion(
        handoff_id=handoff_id,
        result=body.get("result", {}),
        receipts=body.get("receipts", []),
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

    body = await request.json()
    command = body.get("command", {})
    target_ids = body.get("target_ids")

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
