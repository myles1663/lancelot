"""
HIVE Agent Mesh — FastAPI Router.

Endpoints for managing HIVE sub-agents, tasks, and interventions.
All endpoints gated by FEATURE_HIVE flag via subsystem middleware.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability
from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/hive",
    tags=["hive"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("hive.admin")),
    ],
)

# Module-level references set by _init_hive() in gateway
_architect = None
_lifecycle = None
_registry = None
_receipt_mgr = None
_config = None


_audit_logger = None


def _resolve_operator_context(request: Request):
    """Extract operator identity fields from a FastAPI request.

    Returns (operator_id, session_id, actor) where actor is the best
    human-readable display value for text audit logs.
    """
    try:
        from src.core.governance_receipts import _resolve_identity
        identity = _resolve_identity(request)
        if identity:
            actor = identity.display_name or identity.operator_id
            return identity.operator_id, identity.session_id, actor
    except Exception:
        pass
    return None, None, None


def _resolve_operator_ids(request: Request):
    """Backward-compatible helper for existing tests/callers."""
    operator_id, session_id, _actor = _resolve_operator_context(request)
    return operator_id, session_id


def init_hive_api(architect, lifecycle, registry, receipt_mgr, config, audit_logger=None):
    """Wire up module-level references from gateway init."""
    global _architect, _lifecycle, _registry, _receipt_mgr, _config, _audit_logger
    _architect = architect
    _lifecycle = lifecycle
    _registry = registry
    _receipt_mgr = receipt_mgr
    _config = config
    _audit_logger = audit_logger


def shutdown_hive_api():
    """Clear module-level references."""
    global _architect, _lifecycle, _registry, _receipt_mgr, _config
    _architect = None
    _lifecycle = None
    _registry = None
    _receipt_mgr = None
    _config = None


# ── Request Models ───────────────────────────────────────────────────

class TaskSubmitRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="High-level goal")
    context: Optional[Dict[str, Any]] = None


class PauseRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for pause (required)")


class KillRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for kill (required)")


class ModifyRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for modification")
    feedback: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None


class KillAllRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for kill all (required)")


# ── Status Endpoints ─────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Get HIVE system status."""
    degraded_reasons: List[str] = []
    runtime_errors: List[str] = []
    status: Dict[str, Any] = {
        "status": "not_initialized",
        "enabled": _architect is not None,
        "architect_ready": _architect is not None,
        "lifecycle_ready": _lifecycle is not None,
        "registry_ready": _registry is not None,
        "receipt_manager_ready": _receipt_mgr is not None,
        "config_ready": _config is not None,
        "active_agents": 0,
        "max_agents": _config.max_concurrent_agents if _config else 10,
        "runtime_degraded": False,
        "degraded_reasons": [],
        "runtime_errors": [],
    }

    if _architect is None:
        degraded_reasons.append("HIVE architect not initialized")
    else:
        try:
            status.update(_architect.get_status())
        except Exception as exc:
            logger.error("Failed to resolve HIVE architect status: %s", exc)
            status["status"] = "error"
            degraded_reasons.append("HIVE architect status unavailable")
            runtime_errors.append(f"architect_error: {exc}")

    if _lifecycle is None:
        degraded_reasons.append("HIVE lifecycle manager not initialized")
    if _registry is None:
        degraded_reasons.append("HIVE registry not initialized")
    else:
        try:
            status["active_agents"] = _registry.active_count()
        except Exception as exc:
            logger.error("Failed to resolve HIVE registry status: %s", exc)
            degraded_reasons.append("HIVE registry status unavailable")
            runtime_errors.append(f"registry_error: {exc}")

    if _receipt_mgr is None:
        degraded_reasons.append("HIVE receipt manager not initialized")
    if _config is None:
        degraded_reasons.append("HIVE config not initialized")

    status["runtime_degraded"] = bool(degraded_reasons or runtime_errors)
    status["degraded_reasons"] = degraded_reasons
    status["runtime_errors"] = runtime_errors
    return status


@router.get("/roster")
async def get_roster():
    """Get full agent roster (active + archived)."""
    if _registry is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    roster = _registry.get_full_roster()
    return {
        "active": [_agent_to_dict(r) for r in roster["active"]],
        "archived": [_agent_to_dict(r) for r in roster["archived"]],
    }


@router.get("/agents")
async def get_agents():
    """Get active agents."""
    if _registry is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    agents = _registry.list_active()
    return {"agents": [_agent_to_dict(a) for a in agents]}


@router.get("/agents/history")
async def get_agent_history():
    """Get archived (collapsed) agents."""
    if _registry is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    roster = _registry.get_full_roster()
    return {"agents": [_agent_to_dict(r) for r in roster["archived"]]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get a specific agent."""
    if _registry is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    record = _registry.get(agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return _agent_to_dict(record)


@router.get("/agents/{agent_id}/soul")
async def get_agent_soul(agent_id: str):
    """Get the scoped soul for an agent."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    runtime = _lifecycle.get_runtime(agent_id)
    if runtime is None:
        return {"agent_id": agent_id, "soul": None, "note": "No active runtime"}
    soul = getattr(runtime, "_scoped_soul", None)
    if soul is None:
        return {"agent_id": agent_id, "soul": None}
    return {"agent_id": agent_id, "soul": soul.dict() if hasattr(soul, "dict") else str(soul)}


# ── Task Endpoints ───────────────────────────────────────────────────

@router.post("/tasks")
async def submit_task(req: TaskSubmitRequest, request: Request):
    """Submit a new high-level task for HIVE to execute."""
    if _architect is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    if is_runtime_paused():
        pause_state = get_runtime_pause_status()
        raise HTTPException(
            status_code=423,
            detail=pause_state.get("reason") or "Runtime paused by operator",
        )
    operator_id, session_id, actor = _resolve_operator_context(request)
    result = await _architect.execute_task(
        req.goal,
        req.context,
        operator_id=operator_id or "",
        session_id=session_id or "",
        operator_name=actor or "",
    )
    return result


@router.get("/tasks/{quest_id}")
async def get_task(quest_id: str):
    """Get task status by quest ID."""
    if _receipt_mgr is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    tree = _receipt_mgr.get_task_receipt_tree(quest_id)
    return {"quest_id": quest_id, "receipts": tree}


@router.get("/tasks/{quest_id}/tree")
async def get_task_tree(quest_id: str):
    """Get full receipt tree for a task."""
    if _receipt_mgr is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    tree = _receipt_mgr.get_task_receipt_tree(quest_id)
    return {"quest_id": quest_id, "tree": tree}


# ── Agent Control Endpoints ──────────────────────────────────────────

@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: str, req: PauseRequest, request: Request):
    """Pause an executing agent. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        op_id, sess_id, actor = _resolve_operator_context(request)
        _lifecycle.pause(agent_id, req.reason, operator_id=op_id, session_id=sess_id)
        _hive_audit("HIVE_AGENT_PAUSE", f"Paused agent {agent_id}: {req.reason}", actor)

        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request, ActionType.HIVE_INTERVENTION_EVENT,
            action_name="pause_agent",
            inputs={"agent_id": agent_id, "reason": req.reason},
        )

        return {"status": "paused", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: str, request: Request):
    """Resume a paused agent."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        op_id, sess_id, actor = _resolve_operator_context(request)
        _lifecycle.resume(agent_id, operator_id=op_id, session_id=sess_id)
        _hive_audit("HIVE_AGENT_RESUME", f"Resumed agent {agent_id}", actor)

        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request, ActionType.HIVE_INTERVENTION_EVENT,
            action_name="resume_agent",
            inputs={"agent_id": agent_id},
        )

        return {"status": "resumed", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/kill")
async def kill_agent(agent_id: str, req: KillRequest, request: Request):
    """Kill an agent. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        op_id, sess_id, actor = _resolve_operator_context(request)
        _lifecycle.kill(agent_id, req.reason, operator_id=op_id, session_id=sess_id)
        _hive_audit("HIVE_AGENT_KILL", f"Killed agent {agent_id}: {req.reason}", actor)

        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request, ActionType.HIVE_INTERVENTION_EVENT,
            action_name="kill_agent",
            inputs={"agent_id": agent_id, "reason": req.reason},
        )

        return {"status": "killed", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/modify")
async def modify_agent(agent_id: str, req: ModifyRequest, request: Request):
    """Modify an agent (kill + replan). Requires reason."""
    if _architect is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    from src.hive.types import InterventionType, OperatorIntervention
    intervention = OperatorIntervention(
        intervention_type=InterventionType.MODIFY,
        agent_id=agent_id,
        reason=req.reason,
        feedback=req.feedback,
        constraints=req.constraints,
    )
    op_id, sess_id, actor = _resolve_operator_context(request)
    result = await _architect.handle_intervention(
        intervention, req.feedback, operator_id=op_id, session_id=sess_id,
    )
    _hive_audit("HIVE_AGENT_MODIFY", f"Modified agent {agent_id}: {req.reason}", actor)

    from src.core.governance_receipts import emit_governance_receipt
    from src.shared.receipts import ActionType
    emit_governance_receipt(
        request, ActionType.HIVE_INTERVENTION_EVENT,
        action_name="modify_agent",
        inputs={"agent_id": agent_id, "reason": req.reason},
    )

    return result


@router.post("/kill-all")
async def kill_all(req: KillAllRequest, request: Request):
    """Kill all active agents. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    op_id, sess_id, actor = _resolve_operator_context(request)
    collapsed = _lifecycle.kill_all(req.reason, operator_id=op_id, session_id=sess_id)
    _hive_audit("HIVE_KILL_ALL", f"Kill-all: {req.reason} ({len(collapsed)} agents)", actor)

    from src.core.governance_receipts import emit_governance_receipt
    from src.shared.receipts import ActionType
    emit_governance_receipt(
        request, ActionType.HIVE_INTERVENTION_EVENT,
        action_name="kill_all",
        inputs={"reason": req.reason, "collapsed_count": len(collapsed)},
    )

    return {"status": "killed_all", "collapsed": collapsed}


# ── Intervention Endpoints ───────────────────────────────────────────

@router.get("/interventions")
async def get_interventions():
    """Get all intervention receipts."""
    if _receipt_mgr is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    interventions = _receipt_mgr.get_interventions()
    return {"interventions": [_receipt_to_dict(r) for r in interventions]}


@router.get("/interventions/{quest_id}")
async def get_task_interventions(quest_id: str):
    """Get interventions for a specific task."""
    if _receipt_mgr is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    interventions = _receipt_mgr.get_interventions(quest_id=quest_id)
    return {"quest_id": quest_id, "interventions": [_receipt_to_dict(r) for r in interventions]}


# ── Audit Helper ────────────────────────────────────────────────────

def _hive_audit(event_type: str, details: str, actor: Optional[str] = None) -> None:
    """Log an audit event for HIVE operator actions."""
    if _audit_logger:
        try:
            _audit_logger.log_event(event_type, details, user=actor or "operator")
        except Exception as exc:
            logger.warning("Hive audit log failed: %s", exc)


# ── Helpers ──────────────────────────────────────────────────────────

def _agent_to_dict(record) -> Dict[str, Any]:
    """Convert SubAgentRecord to dict for JSON response."""
    return {
        "agent_id": record.agent_id,
        "state": record.state.value if hasattr(record.state, "value") else str(record.state),
        "task_description": record.task_spec.description if record.task_spec else "",
        "quest_id": record.quest_id,
        "action_count": record.action_count,
        "control_method": record.task_spec.control_method.value if record.task_spec and hasattr(record.task_spec.control_method, "value") else "supervised",
        "created_at": record.spawned_at,
        "collapse_reason": record.collapse_reason.value if record.collapse_reason and hasattr(record.collapse_reason, "value") else record.collapse_reason,
        "collapse_message": record.collapse_message,
        "interventions": record.interventions,
        "scoped_soul_hash": record.scoped_soul_hash,
    }


def _receipt_to_dict(receipt) -> Dict[str, Any]:
    """Convert Receipt to dict for JSON response."""
    if hasattr(receipt, "to_dict"):
        return receipt.to_dict()
    return {
        "id": getattr(receipt, "id", ""),
        "action_type": getattr(receipt, "action_type", ""),
        "action_name": getattr(receipt, "action_name", ""),
        "inputs": getattr(receipt, "inputs", {}),
        "status": getattr(receipt, "status", ""),
        "metadata": getattr(receipt, "metadata", {}),
        "created_at": getattr(receipt, "created_at", ""),
    }
