# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Management API — Internal governance and registry endpoints.

Mounted at /api/a2a/ (internal management, not protocol-standard).
Protocol-standard endpoints are at /a2a/ and /.well-known/.

Endpoints:
    GET    /api/a2a/status             — Subsystem status
    GET    /api/a2a/agents             — List remote agents
    GET    /api/a2a/agents/{id}        — Get agent detail
    POST   /api/a2a/agents             — Register remote agent
    DELETE /api/a2a/agents/{id}        — Revoke agent
    POST   /api/a2a/agents/{id}/verify — Re-verify Agent Card
    GET    /api/a2a/card               — View Lancelot's Agent Card
    POST   /api/a2a/card/regenerate    — Force Agent Card regeneration
    POST   /api/a2a/delegate           — Outbound delegation
    GET    /api/a2a/receipts           — Recent A2A receipts
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import get_api_key_identity, require_operator_capability, resolve_operator_identity

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/a2a",
    tags=["a2a-management"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("a2a.admin")),
    ],
)

# Module-level dependencies
_registry: Any = None
_receipt_service: Any = None
_soul: Any = None
_outbound_pipeline: Any = None
_a2a_client: Any = None


def _get_soul():
    """Resolve the live A2A Soul."""
    soul = _soul
    if soul is None:
        return None
    if hasattr(soul, "inbound_a2a_permissions") or hasattr(soul, "outbound_a2a_permissions"):
        return soul
    return soul() if callable(soul) else soul


def _resolve_request_identity(request: Request):
    """Resolve operator identity from the authenticated request."""
    identity = resolve_operator_identity(request)
    if identity is None:
        identity = get_api_key_identity(request)
    return identity


def init_a2a_api(
    registry: Any,
    receipt_service: Any,
    soul: Any,
    outbound_pipeline: Any = None,
    a2a_client: Any = None,
) -> None:
    """Initialize A2A management API."""
    global _registry, _receipt_service, _soul, _outbound_pipeline, _a2a_client
    _registry = registry
    _receipt_service = receipt_service
    _soul = soul
    _outbound_pipeline = outbound_pipeline
    _a2a_client = a2a_client
    logger.info("A2A management API initialized")


# ── Request Models ───────────────────────────────────────────

class RegisterAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    display_name: str
    agent_card_url: str = ""
    agent_framework: str = "unknown"
    auth_type: str = "none"
    credentials_ref: str = ""
    direction: str = "outbound"


class DelegateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_agent_id: str
    content: str
    task_type: str = "general"


# ── Endpoints ────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Get A2A subsystem status."""
    degraded_reasons: List[str] = []
    runtime_errors: List[str] = []
    registry_ready = _registry is not None
    outbound_pipeline_ready = _outbound_pipeline is not None
    client_ready = _a2a_client is not None

    soul = None
    try:
        soul = _get_soul()
    except Exception as exc:
        runtime_errors.append(f"soul_error: {exc}")
        degraded_reasons.append("A2A Soul status unavailable")

    inbound_perms = getattr(soul, "inbound_a2a_permissions", None) if soul else None
    outbound_perms = getattr(soul, "outbound_a2a_permissions", None) if soul else None

    agent_count = 0
    if _registry:
        try:
            agent_count = len(_registry.list_agents())
        except Exception as exc:
            runtime_errors.append(f"registry_error: {exc}")
            degraded_reasons.append("A2A registry status unavailable")
    else:
        degraded_reasons.append("A2A registry not initialized")

    if soul is None:
        degraded_reasons.append("A2A Soul not loaded")
    if _outbound_pipeline is None:
        degraded_reasons.append("A2A outbound pipeline not initialized")
    if _a2a_client is None:
        degraded_reasons.append("A2A client not initialized")

    return {
        "enabled": _registry is not None,
        "soul_version": getattr(soul, "version", None) if soul else None,
        "inbound_enabled": inbound_perms is not None and inbound_perms.allow_inbound if inbound_perms else False,
        "outbound_enabled": outbound_perms is not None and outbound_perms.allow_outbound if outbound_perms else False,
        "registered_agents": agent_count,
        "max_delegation_depth": outbound_perms.max_delegation_depth if outbound_perms else 2,
        "registry_ready": registry_ready,
        "outbound_pipeline_ready": outbound_pipeline_ready,
        "client_ready": client_ready,
        "runtime_degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "runtime_errors": runtime_errors,
    }


@router.get("/agents")
async def list_agents(
    direction: Optional[str] = None,
    status: Optional[str] = None,
    framework: Optional[str] = None,
):
    """List all registered remote agents."""
    if not _registry:
        raise HTTPException(status_code=503, detail="A2A registry not initialized")

    agents = _registry.list_agents(direction=direction, status=status, framework=framework)
    return {
        "agents": [
            {**a.to_dict(), "card_status": a.card_status, "credentials_ref": "[REDACTED]" if a.credentials_ref else ""}
            for a in agents
        ],
        "total": len(agents),
    }


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get detailed info for a remote agent."""
    if not _registry:
        raise HTTPException(status_code=503, detail="A2A registry not initialized")

    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    result = {**agent.to_dict(), "card_status": agent.card_status}
    result["credentials_ref"] = "[REDACTED]" if agent.credentials_ref else ""

    # Include recent receipts
    if _receipt_service:
        try:
            receipts = _receipt_service.search(
                query=agent_id,
                limit=10,
                action_types=[
                    "a2a_task_received", "a2a_inbound_blocked",
                    "a2a_task_completed", "a2a_delegation_sent",
                    "a2a_delegation_completed", "a2a_delegation_failed",
                    "a2a_outbound_blocked",
                ],
            )
            result["recent_receipts"] = [r.to_dict() for r in receipts]
        except Exception:
            result["recent_receipts"] = []

    # Include Soul permission view
    soul = _get_soul()
    if soul:
        result["soul_permissions"] = _get_agent_soul_permissions(agent)

    return result


@router.post("/agents")
async def register_agent(body: RegisterAgentRequest, request: Request):
    """Manually register a remote agent."""
    if not _registry:
        raise HTTPException(status_code=503, detail="A2A registry not initialized")

    existing = _registry.get(body.agent_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent already registered: {body.agent_id}")

    from src.a2a.types import RemoteAgent
    agent = RemoteAgent(
        agent_id=body.agent_id,
        display_name=body.display_name,
        agent_card_url=body.agent_card_url,
        agent_framework=body.agent_framework,
        auth_type=body.auth_type,
        credentials_ref=body.credentials_ref,
        direction=body.direction,
        auto_registered=False,
    )

    # Auto-populate network allowlist from card URL
    if body.agent_card_url:
        from urllib.parse import urlparse
        parsed = urlparse(body.agent_card_url)
        if parsed.hostname:
            agent.network_allowlist_entries = [parsed.hostname]

    _registry.register(agent)

    # Emit registration receipt (manual = identity-required handled at API layer)
    identity = _resolve_request_identity(request)
    if _receipt_service:
        from src.shared.receipts import Receipt, ActionType, ReceiptStatus, CognitionTier
        receipt = Receipt(
            action_type=ActionType.A2A_AGENT_REGISTERED.value,
            action_name="a2a_agent_manual_registration",
            inputs={"agent_id": body.agent_id, "direction": body.direction},
            outputs={"auto_registered": False, "kill_switch_id": agent.kill_switch_id},
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.DETERMINISTIC.value,
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            metadata={"subsystem": "a2a"},
        )
        try:
            _receipt_service.create(receipt)
        except Exception as e:
            logger.warning("Registration receipt failed: %s", e)

    return {"agent_id": agent.agent_id, "kill_switch_id": agent.kill_switch_id, "status": "registered"}


@router.delete("/agents/{agent_id}")
async def revoke_agent(agent_id: str):
    """Revoke a remote agent."""
    if not _registry:
        raise HTTPException(status_code=503, detail="A2A registry not initialized")

    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    _registry.revoke(agent_id)
    return {"agent_id": agent_id, "status": "revoked"}


@router.post("/agents/{agent_id}/verify")
async def verify_agent_card(agent_id: str):
    """Re-verify a remote agent's Agent Card."""
    if not _registry:
        raise HTTPException(status_code=503, detail="A2A registry not initialized")

    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    if not _a2a_client:
        raise HTTPException(status_code=503, detail="A2A client not initialized")

    verified = _a2a_client.verify_agent_card(agent, allow_repin=True)
    if verified:
        agent.last_verified = datetime.now(timezone.utc).isoformat()
        _registry.update(agent)

    return {
        "agent_id": agent_id,
        "verified": verified,
        "card_status": agent.card_status,
    }


@router.get("/card")
async def get_own_card(request: Request):
    """View Lancelot's own Agent Card as external callers see it."""
    soul = _get_soul()
    if not soul:
        raise HTTPException(status_code=503, detail="Soul not loaded")

    from src.a2a.agent_card import generate_agent_card
    base_url = str(request.base_url).rstrip("/")
    card = generate_agent_card(soul=soul, base_url=base_url)
    return card.to_dict()


@router.post("/card/regenerate")
async def regenerate_card(request: Request):
    """Force regeneration of Lancelot's Agent Card."""
    soul = _get_soul()
    if not soul:
        raise HTTPException(status_code=503, detail="Soul not loaded")

    from src.a2a.agent_card import invalidate_card, generate_agent_card
    invalidate_card()

    base_url = str(request.base_url).rstrip("/")
    card = generate_agent_card(soul=soul, base_url=base_url)

    # Emit card update receipt
    identity = _resolve_request_identity(request)
    if _receipt_service:
        from src.shared.receipts import Receipt, ActionType, ReceiptStatus, CognitionTier
        receipt = Receipt(
            action_type=ActionType.A2A_AGENT_CARD_UPDATED.value,
            action_name="a2a_agent_card_regenerated",
            inputs={"trigger": "manual"},
            outputs={"skills_count": len(card.skills), "version": card.version},
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.DETERMINISTIC.value,
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            metadata={"subsystem": "a2a"},
        )
        try:
            _receipt_service.create(receipt)
        except Exception as e:
            logger.warning("Card update receipt failed: %s", e)

    return {"status": "regenerated", "skills_count": len(card.skills)}


@router.post("/delegate")
async def delegate_task(body: DelegateRequest, request: Request):
    """Initiate an outbound A2A delegation."""
    if not _outbound_pipeline:
        raise HTTPException(status_code=503, detail="Outbound A2A pipeline not initialized")

    identity = _resolve_request_identity(request)
    result = _outbound_pipeline.delegate(
        target_agent_id=body.target_agent_id,
        task_content=body.content,
        task_type=body.task_type,
        operator_id=identity.operator_id,
        session_id=identity.session_id,
    )

    if not result.success:
        status = 403 if result.block_reason else 400
        raise HTTPException(status_code=status, detail=result.error or "Delegation failed")

    return result.to_dict()


@router.get("/receipts")
async def list_a2a_receipts(limit: int = 20):
    """List recent A2A receipts."""
    if not _receipt_service:
        raise HTTPException(status_code=503, detail="Receipt service not available")

    a2a_types = [
        "a2a_task_received", "a2a_inbound_blocked", "a2a_task_executing",
        "a2a_task_completed", "a2a_delegation_sent", "a2a_outbound_blocked",
        "a2a_delegation_completed", "a2a_delegation_failed",
        "a2a_agent_registered", "a2a_agent_card_updated", "a2a_agent_card_fetched",
    ]

    receipts = _receipt_service.search(
        query="a2a_",
        limit=min(limit, 100),
        action_types=a2a_types,
    )

    return {
        "receipts": [r.to_dict() for r in receipts],
        "total": len(receipts),
    }


# ── Helpers ──────────────────────────────────────────────────

def _get_agent_soul_permissions(agent: Any) -> Dict[str, Any]:
    """Extract Soul permission rules governing a specific agent."""
    result: Dict[str, Any] = {"inbound": None, "outbound": None}

    soul = _get_soul()
    inbound = getattr(soul, "inbound_a2a_permissions", None) if soul else None
    if inbound:
        for rule in inbound.allowed_callers:
            if rule.get("agent_id") == agent.agent_id or \
               rule.get("agent_framework") == agent.agent_framework:
                result["inbound"] = rule
                break

    outbound = getattr(soul, "outbound_a2a_permissions", None) if soul else None
    if outbound:
        for target in outbound.allowed_targets:
            if target.get("agent_id") == agent.agent_id or \
               target.get("agent_framework") == agent.agent_framework:
                result["outbound"] = target
                break

    return result
