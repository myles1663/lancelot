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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/federation", tags=["federation"])

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


def init_federation_api(
    identity,
    heartbeat_emitter,
    config,
    topology_registry=None,
) -> None:
    """Wire federation API with runtime objects. Called from gateway."""
    global _identity, _heartbeat_emitter, _config, _topology_registry, _initialized
    _identity = identity
    _heartbeat_emitter = heartbeat_emitter
    _config = config
    _topology_registry = topology_registry
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
) -> None:
    """Wire transport-layer handlers into the API. Called after transport init."""
    global _peer_protocol, _command_relay, _soul_transport
    global _handoff_protocol, _cost_reporter, _auth, _audit_engine
    _peer_protocol = peer_protocol
    _command_relay = command_relay
    _soul_transport = soul_transport
    _handoff_protocol = handoff_protocol
    _cost_reporter = cost_reporter
    _auth = auth
    _audit_engine = audit_engine
    logger.info("Federation transport handlers wired into API")


def shutdown_federation_api() -> None:
    """Clean up federation API state."""
    global _identity, _heartbeat_emitter, _config, _topology_registry, _initialized
    global _peer_protocol, _command_relay, _soul_transport
    global _handoff_protocol, _cost_reporter, _auth
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
    logger.info("Federation API shut down")


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

async def _verify_peer_request(request: Request) -> Optional[JSONResponse]:
    """Verify an incoming request from a federation peer.

    Returns None if valid, or a JSONResponse with error if invalid.
    Protected endpoints should call this first.
    """
    if not _auth:
        return None  # Auth not configured — skip verification

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
        return JSONResponse(
            status_code=401,
            content={"error": "Authentication failed", "reason": result.reason},
        )
    return None


# ═══════════════════════════════════════════════════════════════
# Category 1: State Stream
# ═══════════════════════════════════════════════════════════════

@router.get("/stream")
async def heartbeat_stream():
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
async def latest_heartbeat():
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
async def receive_command(request: Request):
    """Receive a governance command from an authorized peer."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("command")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _command_relay.handle_kill_command(body)
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


@router.post("/killswitch")
async def receive_killswitch(request: Request):
    """Receive a federation kill switch command."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("killswitch")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _command_relay.handle_kill_command(body)
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


@router.post("/pause")
async def receive_pause(request: Request):
    """Receive a federation pause signal."""
    if not _initialized:
        return _not_initialized()
    if not _command_relay:
        return _no_transport("pause")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _command_relay.handle_pause(body)
    status = 200 if result.get("accepted") else 403
    return JSONResponse(status_code=status, content=result)


# ═══════════════════════════════════════════════════════════════
# Category 3: Handoff API
# ═══════════════════════════════════════════════════════════════

@router.post("/handoff/initiate")
async def initiate_handoff(request: Request):
    """Receive a workflow handoff from a source peer."""
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("handoff/initiate")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _handoff_protocol.handle_handoff_initiation(body)
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.post("/handoff/complete")
async def complete_handoff(request: Request):
    """Receive a handoff completion report from a target peer."""
    if not _initialized:
        return _not_initialized()
    if not _handoff_protocol:
        return _no_transport("handoff/complete")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _handoff_protocol.handle_completion_report(body)
    return JSONResponse(status_code=200, content=result)


@router.get("/handoff/{handoff_id}")
async def get_handoff(handoff_id: str):
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
async def get_identity():
    """Return this instance's public federation identity."""
    if not _initialized or not _identity:
        return _not_initialized()

    return JSONResponse(status_code=200, content=_identity.to_public_dict())


@router.get("/status")
async def get_status():
    """Return federation status summary."""
    if not _initialized:
        return _not_initialized()

    latest_hb = _heartbeat_emitter.get_latest() if _heartbeat_emitter else None
    peer_count = 0
    if _topology_registry:
        peer_count = len(_topology_registry.list_peers())

    return JSONResponse(status_code=200, content={
        "enabled": True,
        "instance_id": _identity.instance_id if _identity else "",
        "fingerprint": _identity.fingerprint if _identity else "",
        "deployment_mode": latest_hb.deployment_mode if latest_hb else "standalone",
        "peer_count": peer_count,
        "soul_consistency": "synchronized",
        "cost_threshold": "normal",
        "heartbeat_interval_s": _config.heartbeat_interval_s if _config else 2.0,
        "tls_required": _config.tls_required if _config else False,
        "last_heartbeat": latest_hb.timestamp if latest_hb else None,
        "transport_ready": _command_relay is not None,
    })


@router.get("/health")
async def get_federation_health():
    """Return federation health summary for War Room."""
    if not _initialized:
        return JSONResponse(status_code=200, content={
            "total_peers": 0, "healthy": 0, "warning": 0,
            "critical": 0, "lost": 0, "deployment_mode": "standalone",
        })

    if _topology_registry:
        summary = _topology_registry.get_health_summary()
        return JSONResponse(status_code=200, content=summary)

    return JSONResponse(status_code=200, content={
        "total_peers": 0, "healthy": 0, "warning": 0,
        "critical": 0, "lost": 0, "deployment_mode": "standalone",
    })


@router.get("/peers")
async def get_peers():
    """Return list of federation peers."""
    if not _initialized or not _topology_registry:
        return JSONResponse(status_code=200, content=[])

    peers = [p.to_dict() for p in _topology_registry.list_peers()]
    return JSONResponse(status_code=200, content=peers)


@router.get("/topology")
async def get_topology():
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
async def get_soul_hash():
    """Return current Soul version hash for handshake verification."""
    if not _initialized:
        return _not_initialized()

    latest_hb = _heartbeat_emitter.get_latest() if _heartbeat_emitter else None
    return JSONResponse(status_code=200, content={
        "soul_version_hash": latest_hb.soul_version_hash if latest_hb else "",
        "instance_id": _identity.instance_id if _identity else None,
    })


# ═══════════════════════════════════════════════════════════════
# Category 5: Soul API
# ═══════════════════════════════════════════════════════════════

@router.post("/soul/handshake")
async def soul_handshake(request: Request):
    """Perform Soul version handshake with a peer."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul/handshake")

    # Handshake is a local comparison — no auth needed
    latest_hb = _heartbeat_emitter.get_latest() if _heartbeat_emitter else None
    return JSONResponse(status_code=200, content={
        "instance_id": _identity.instance_id if _identity else "",
        "soul_version_hash": latest_hb.soul_version_hash if latest_hb else "",
    })


@router.get("/soul")
async def get_soul():
    """Return this instance's full Soul document for peer fetch."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul")

    result = _soul_transport.handle_soul_fetch()
    return JSONResponse(status_code=200, content=result)


@router.post("/soul/update")
async def receive_soul_update(request: Request):
    """Receive a Soul version push from a peer."""
    if not _initialized:
        return _not_initialized()
    if not _soul_transport:
        return _no_transport("soul/update")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _soul_transport.handle_soul_push(body)
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
    result = _peer_protocol.handle_registration_request(body)
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.delete("/peer/{instance_id}")
async def remove_peer(instance_id: str):
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
async def get_budget():
    """Return current budget snapshot."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget")

    result = _cost_reporter.get_aggregate_status()
    return JSONResponse(status_code=200, content=result)


@router.post("/budget/report")
async def receive_budget_report(request: Request):
    """Receive a cost report from a peer."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget/report")

    auth_err = await _verify_peer_request(request)
    if auth_err:
        return auth_err

    body = await request.json()
    result = _cost_reporter.handle_cost_report(body)
    status = 200 if result.get("accepted") else 400
    return JSONResponse(status_code=status, content=result)


@router.get("/budget/threshold")
async def get_budget_threshold():
    """Return current budget threshold level."""
    if not _initialized:
        return _not_initialized()
    if not _cost_reporter:
        return _no_transport("budget/threshold")

    result = _cost_reporter.get_aggregate_status()
    threshold = result.get("threshold", "normal")
    return JSONResponse(status_code=200, content={"threshold": threshold})


# ═══════════════════════════════════════════════════════════════
# Category 8: Operator Management API (initiator-side actions)
# ═══════════════════════════════════════════════════════════════

@router.post("/manage/register-peer")
async def manage_register_peer(request: Request):
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
async def manage_initiate_handoff(request: Request):
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
async def manage_complete_handoff(request: Request):
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
async def manage_propagate_kill(request: Request):
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

    # Build command_data in the format propagate_kill expects
    command_data = {**command}
    if target_ids:
        command_data["target_instance_ids"] = target_ids
    command_data["issuer_instance_id"] = _identity.instance_id

    results = await _command_relay.propagate_kill(command_data)

    return JSONResponse(status_code=200, content={
        "results": results,
        "total": len(results),
    })


# ═══════════════════════════════════════════════════════════════
# Category 9: Audit API
# ═══════════════════════════════════════════════════════════════

@router.get("/audit")
async def get_audit_entries(
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
async def get_audit_summary():
    """Get audit engine summary statistics."""
    if not _initialized:
        return _not_initialized()
    if not _audit_engine:
        return _no_transport("audit/summary")

    return JSONResponse(status_code=200, content=_audit_engine.get_summary())


@router.get("/audit/quest/{quest_id}")
async def get_quest_timeline(quest_id: str):
    """Reconstruct the forensic timeline for a federation quest."""
    if not _initialized:
        return _not_initialized()
    if not _audit_engine:
        return _no_transport("audit/quest")

    timeline = _audit_engine.reconstruct_quest(quest_id)
    return JSONResponse(status_code=200, content=timeline.to_dict())
