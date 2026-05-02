# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Management API — War Room endpoints for MCP server management.

Provides CRUD for MCP server registrations, credential management,
connection testing, and governance status. All endpoints require
War Room authentication.

Routes:
    GET    /api/mcp/servers                — list registered servers
    POST   /api/mcp/servers                — register a new server
    GET    /api/mcp/servers/{id}           — server detail + tools + status
    DELETE /api/mcp/servers/{id}           — unregister a server
    POST   /api/mcp/servers/{id}/status    — update server status
    POST   /api/mcp/servers/{id}/test      — test connection + discover tools
    POST   /api/mcp/servers/{id}/credential — store credential in vault
    GET    /api/mcp/receipts/summary       — MCP receipt statistics
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/mcp",
    tags=["mcp"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("mcp.admin")),
    ],
)

# ── Module-level state (wired by gateway) ────────────────────

_registry = None        # MCPServerRegistry
_evaluator = None       # MCPPermissionEvaluator
_proxy = None           # GovernedMCPProxy
_vault = None           # CredentialVault
_network_policy = None  # MCPNetworkPolicy
_receipt_service = None # ReceiptService


def init_mcp_api(
    registry=None,
    evaluator=None,
    proxy=None,
    vault=None,
    network_policy=None,
    receipt_service=None,
):
    """Wire MCP API with runtime objects. Called by gateway."""
    global _registry, _evaluator, _proxy, _vault, _network_policy, _receipt_service
    _registry = registry
    _evaluator = evaluator
    _proxy = proxy
    _vault = vault
    _network_policy = network_policy
    _receipt_service = receipt_service
    logger.info("MCP API initialized")


def shutdown_mcp_api() -> None:
    """Clear MCP API runtime references for a hot-toggle shutdown."""
    global _registry, _evaluator, _proxy, _vault, _network_policy, _receipt_service
    _registry = None
    _evaluator = None
    _proxy = None
    _vault = None
    _network_policy = None
    _receipt_service = None
    logger.info("MCP API shutdown complete")


def update_mcp_soul(soul) -> None:
    """Refresh MCP permissions from the live Soul document."""
    if _evaluator is None:
        return
    if soul is None:
        _evaluator.load_permissions([], soul_version="")
        return
    if hasattr(soul, "model_dump"):
        _evaluator.load_from_soul(soul.model_dump())
    elif hasattr(soul, "dict"):
        _evaluator.load_from_soul(soul.dict())
    else:
        _evaluator.load_from_soul(dict(soul))


def _check_initialized():
    if _registry is None:
        raise HTTPException(
            status_code=503,
            detail="MCP subsystem not initialized. Enable FEATURE_MCP.",
        )


# ── Request Models ───────────────────────────────────────────

class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: str
    name: str
    endpoint: str
    auth_type: str = "none"
    vault_key: str = ""
    auth_header: str = ""
    default_risk_tier: str = "T2"
    network_domains: list = []

class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str

class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vault_key: str
    value: str
    type: str = "api_key"


# ── Endpoints ────────────────────────────────────────────────

@router.get("/servers")
async def list_servers():
    """List all registered MCP servers."""
    try:
        import feature_flags
        feature_enabled = getattr(feature_flags, "FEATURE_MCP", False)
    except ImportError:
        feature_enabled = False

    degraded_reasons = []
    runtime_errors = []

    if _registry is None:
        return {
            "servers": [],
            "total": 0,
            "active_count": 0,
            "feature_enabled": feature_enabled,
            "registry_ready": False,
            "evaluator_ready": _evaluator is not None,
            "proxy_ready": _proxy is not None,
            "vault_ready": _vault is not None,
            "network_policy_ready": _network_policy is not None,
            "runtime_degraded": bool(feature_enabled),
            "degraded_reasons": (
                ["MCP registry not initialized"] if feature_enabled else []
            ),
            "runtime_errors": [],
        }

    if feature_enabled and _evaluator is None:
        degraded_reasons.append("MCP permission evaluator not initialized")
    if feature_enabled and _proxy is None:
        degraded_reasons.append("MCP proxy not initialized")
    if feature_enabled and _vault is None:
        degraded_reasons.append("MCP vault not initialized")
    if feature_enabled and _network_policy is None:
        degraded_reasons.append("MCP network policy not initialized")

    try:
        servers = _registry.list_servers()
    except Exception as exc:
        logger.error("Failed to list MCP servers: %s", exc)
        degraded_reasons.append("MCP registry status unavailable")
        runtime_errors.append(f"registry_error: {exc}")
        return {
            "servers": [],
            "total": 0,
            "active_count": 0,
            "feature_enabled": feature_enabled,
            "registry_ready": True,
            "evaluator_ready": _evaluator is not None,
            "proxy_ready": _proxy is not None,
            "vault_ready": _vault is not None,
            "network_policy_ready": _network_policy is not None,
            "runtime_degraded": True,
            "degraded_reasons": degraded_reasons,
            "runtime_errors": runtime_errors,
        }

    server_list = []
    for s in servers:
        try:
            info = s.safe_summary()
            info["kill_switch_id"] = s.kill_switch_id
            info["registered_at"] = s.registered_at
            info["tool_count"] = 0  # Populated on demand via detail/test
            server_list.append(info)
        except Exception as exc:
            logger.error("Failed to summarize MCP server %s: %s", getattr(s, "server_id", "unknown"), exc)
            degraded_reasons.append("MCP server summary unavailable")
            runtime_errors.append(f"server_summary_error: {exc}")

    active_count = sum(
        1 for s in servers
        if s.status.value in ("active", "validated")
    )

    return {
        "servers": server_list,
        "total": len(server_list),
        "active_count": active_count,
        "feature_enabled": feature_enabled,
        "registry_ready": True,
        "evaluator_ready": _evaluator is not None,
        "proxy_ready": _proxy is not None,
        "vault_ready": _vault is not None,
        "network_policy_ready": _network_policy is not None,
        "runtime_degraded": bool(degraded_reasons or runtime_errors),
        "degraded_reasons": degraded_reasons,
        "runtime_errors": runtime_errors,
    }


@router.post("/servers")
async def register_server(req: RegisterRequest):
    """Register a new MCP server."""
    _check_initialized()

    from src.mcp.registry import MCPServerConfig, MCPTransport, MCPAuthType, MCPRiskTier

    # Validate endpoint if network policy is available
    if _network_policy:
        validation = _network_policy.validate_endpoint(req.endpoint)
        if not validation.valid:
            raise HTTPException(
                status_code=400,
                detail=f"Endpoint validation failed: {'; '.join(validation.violations)}",
            )

    try:
        auth_type = MCPAuthType(req.auth_type)
    except ValueError:
        auth_type = MCPAuthType.NONE

    try:
        risk_tier = MCPRiskTier(req.default_risk_tier.upper())
    except ValueError:
        risk_tier = MCPRiskTier.T2

    config = MCPServerConfig(
        server_id=req.server_id,
        name=req.name,
        endpoint=req.endpoint,
        transport=MCPTransport.HTTP_SSE,
        auth_type=auth_type,
        vault_key=req.vault_key,
        auth_header=req.auth_header,
        default_risk_tier=risk_tier,
        network_domains=req.network_domains,
    )

    try:
        _registry.register(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "registered": True,
        "server_id": req.server_id,
        "kill_switch_id": config.kill_switch_id,
    }


@router.get("/servers/{server_id}")
async def server_detail(server_id: str):
    """Get detailed info for a single MCP server."""
    _check_initialized()

    config = _registry.get(server_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    info = config.safe_summary()
    info["kill_switch_id"] = config.kill_switch_id
    info["registered_at"] = config.registered_at

    # Soul permission check
    soul_permitted = True
    if _evaluator:
        perm = _evaluator.check_server_access(server_id)
        soul_permitted = perm.allowed

    # Kill switch check
    from src.mcp.kill_switches import check_mcp_kill_switches
    kill = check_mcp_kill_switches(config.kill_switch_id)

    # Network check
    network_allowed = True
    if _network_policy:
        network_allowed = _network_policy.check_invocation_allowed(config.endpoint)

    return {
        "server": info,
        "tools": [],  # Populated by test endpoint
        "soul_permitted": soul_permitted,
        "kill_switch_active": kill.allowed,
        "kill_switch": kill.to_dict(),
        "network_allowed": network_allowed,
    }


@router.delete("/servers/{server_id}")
async def remove_server(server_id: str):
    """Unregister an MCP server."""
    _check_initialized()

    removed = _registry.unregister(server_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    return {"removed": True}


@router.post("/servers/{server_id}/status")
async def update_status(server_id: str, req: StatusRequest):
    """Update an MCP server's lifecycle status."""
    _check_initialized()

    from src.mcp.registry import MCPServerStatus

    try:
        status = MCPServerStatus(req.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {req.status}. Valid: registered, validated, active, suspended, error",
        )

    updated = _registry.set_status(server_id, status)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    return {"server_id": server_id, "status": req.status}


@router.post("/servers/{server_id}/test")
async def test_server(server_id: str):
    """Test connection to an MCP server and discover tools."""
    _check_initialized()

    config = _registry.get(server_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    if not _proxy:
        raise HTTPException(status_code=503, detail="MCP proxy not initialized")

    start = int(time.time() * 1000)
    try:
        tools = await _proxy.list_server_tools(server_id)
        latency = int(time.time() * 1000) - start

        # Mark as validated if tools discovered
        if tools and config.status.value == "registered":
            from src.mcp.registry import MCPServerStatus
            _registry.set_status(server_id, MCPServerStatus.VALIDATED)

        return {
            "success": True,
            "tool_count": len(tools),
            "latency_ms": latency,
            "tools": [t.to_dict() for t in tools],
        }
    except Exception as e:
        latency = int(time.time() * 1000) - start
        return {
            "success": False,
            "tool_count": 0,
            "latency_ms": latency,
            "error": str(e),
        }


@router.post("/servers/{server_id}/credential")
async def store_credential(server_id: str, req: CredentialRequest):
    """Store a credential for an MCP server in the Vault."""
    _check_initialized()

    config = _registry.get(server_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    if not _vault:
        raise HTTPException(status_code=503, detail="Credential Vault not available")

    vault_key = req.vault_key or f"mcp.{server_id}"

    _vault.store(key=vault_key, value=req.value, type=req.type)

    # Update server config with vault key if not set
    if not config.vault_key:
        config.vault_key = vault_key
        _registry.register(config)  # Re-register to save updated config

    # Grant vault access policy
    _vault.access_policy.grant(f"mcp:{server_id}", vault_key)

    return {"stored": True}


@router.get("/receipts/summary")
async def receipt_summary():
    """Get MCP receipt statistics."""
    if not _receipt_service:
        return {
            "total_calls": 0,
            "total_blocked": 0,
            "block_gates": {},
            "recent_calls": [],
        }

    try:
        # Query MCP-specific receipts
        calls = _receipt_service.search(action_type="mcp_tool_call", limit=100)
        blocked = _receipt_service.search(action_type="mcp_tool_blocked", limit=100)

        # Aggregate block gates
        block_gates: Dict[str, int] = {}
        for r in blocked:
            gate = (r.metadata or {}).get("block_gate", "unknown")
            block_gates[gate] = block_gates.get(gate, 0) + 1

        # Recent calls
        recent = []
        for r in (calls + blocked)[:20]:
            recent.append({
                "server_id": (r.metadata or {}).get("mcp_server_id", ""),
                "tool_name": (r.metadata or {}).get("mcp_tool_name", ""),
                "status": r.status,
                "timestamp": r.timestamp,
            })
        recent.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "total_calls": len(calls),
            "total_blocked": len(blocked),
            "block_gates": block_gates,
            "recent_calls": recent[:10],
        }
    except Exception as e:
        logger.error("Failed to get MCP receipt summary: %s", e)
        return {
            "total_calls": 0,
            "total_blocked": 0,
            "block_gates": {},
            "recent_calls": [],
        }
