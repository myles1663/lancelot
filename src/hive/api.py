"""
HIVE Agent Mesh — FastAPI Router.

Endpoints for managing HIVE sub-agents, tasks, and interventions.
All endpoints gated by FEATURE_HIVE flag via subsystem middleware.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hive", tags=["hive"])

# Module-level references set by _init_hive() in gateway
_architect = None
_lifecycle = None
_registry = None
_receipt_mgr = None
_config = None


_audit_logger = None


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
    if _architect is None:
        return {"status": "not_initialized", "enabled": False}
    status = _architect.get_status()
    status["enabled"] = True
    if _registry:
        status["active_agents"] = _registry.active_count()
        status["max_agents"] = _config.max_concurrent_agents if _config else 10
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
async def submit_task(req: TaskSubmitRequest):
    """Submit a new high-level task for HIVE to execute."""
    if _architect is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    result = await _architect.execute_task(req.goal, req.context)
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
async def pause_agent(agent_id: str, req: PauseRequest):
    """Pause an executing agent. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        _lifecycle.pause(agent_id, req.reason)
        _hive_audit("HIVE_AGENT_PAUSE", f"Paused agent {agent_id}: {req.reason}")
        return {"status": "paused", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """Resume a paused agent."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        _lifecycle.resume(agent_id)
        _hive_audit("HIVE_AGENT_RESUME", f"Resumed agent {agent_id}")
        return {"status": "resumed", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/kill")
async def kill_agent(agent_id: str, req: KillRequest):
    """Kill an agent. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    try:
        _lifecycle.kill(agent_id, req.reason)
        _hive_audit("HIVE_AGENT_KILL", f"Killed agent {agent_id}: {req.reason}")
        return {"status": "killed", "agent_id": agent_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/agents/{agent_id}/modify")
async def modify_agent(agent_id: str, req: ModifyRequest):
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
    result = await _architect.handle_intervention(intervention, req.feedback)
    _hive_audit("HIVE_AGENT_MODIFY", f"Modified agent {agent_id}: {req.reason}")
    return result


@router.post("/kill-all")
async def kill_all(req: KillAllRequest):
    """Kill all active agents. Requires reason."""
    if _lifecycle is None:
        raise HTTPException(status_code=503, detail="HIVE not initialized")
    collapsed = _lifecycle.kill_all(req.reason)
    _hive_audit("HIVE_KILL_ALL", f"Kill-all: {req.reason} ({len(collapsed)} agents)")
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

def _hive_audit(event_type: str, details: str) -> None:
    """Log an audit event for HIVE operator actions."""
    if _audit_logger:
        try:
            _audit_logger.log_event(event_type, details, user="WarRoom")
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
