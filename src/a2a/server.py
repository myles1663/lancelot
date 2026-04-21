# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Inbound Server — Protocol-standard endpoints for external A2A clients.

Exposes:
    GET  /.well-known/agent.json          — Agent Card discovery
    POST /a2a/tasks/send                  — Submit a task
    GET  /a2a/tasks/{task_id}             — Get task status
    GET  /a2a/tasks/{task_id}/subscribe   — SSE streaming

These are protocol-standard URLs that external agents expect at root level.
Internal management endpoints are at /api/a2a/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.a2a.types import (
    A2ATask, A2AMessage, A2AMessagePart, A2ATaskStatus,
    AgentFramework,
)
from src.a2a.inbound_pipeline import CallerInfo
from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused

logger = logging.getLogger(__name__)

# Protocol-standard router (mounted at root, not /api/)
a2a_server_router = APIRouter(tags=["a2a-protocol"])

# Module-level dependencies
_soul: Any = None
_receipt_service: Any = None
_registry: Any = None
_inbound_pipeline: Any = None
_agent_card_generator: Any = None
_task_executor: Any = None
_task_store_file: Optional[Path] = None

# In-memory task store (production: use receipt service for persistence)
_active_tasks: Dict[str, Dict[str, Any]] = {}


def _get_soul():
    """Resolve the live A2A Soul."""
    soul = _soul
    if soul is None:
        return None
    if hasattr(soul, "inbound_a2a_permissions") or hasattr(soul, "outbound_a2a_permissions"):
        return soul
    return soul() if callable(soul) else soul


def _load_task_state() -> None:
    """Restore inbound task state from disk."""
    global _active_tasks
    if _task_store_file is None or not _task_store_file.exists():
        _active_tasks = {}
        return
    try:
        data = json.loads(_task_store_file.read_text(encoding="utf-8"))
        _active_tasks = data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load A2A task state: %s", exc)
        _active_tasks = {}


def _save_task_state() -> None:
    """Persist inbound task state to disk."""
    if _task_store_file is None:
        return
    try:
        _task_store_file.parent.mkdir(parents=True, exist_ok=True)
        _task_store_file.write_text(
            json.dumps(_active_tasks, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to persist A2A task state: %s", exc)


def init_a2a_server(
    soul: Any,
    receipt_service: Any,
    registry: Any,
    inbound_pipeline: Any,
    task_executor: Any = None,
    data_dir: str = "/home/lancelot/data",
) -> None:
    """Initialize the A2A server with dependencies."""
    global _soul, _receipt_service, _registry, _inbound_pipeline, _agent_card_generator, _task_executor, _task_store_file

    from src.a2a.agent_card import generate_agent_card

    _soul = soul
    _receipt_service = receipt_service
    _registry = registry
    _inbound_pipeline = inbound_pipeline
    _agent_card_generator = generate_agent_card
    _task_executor = task_executor
    _task_store_file = Path(data_dir) / "a2a_tasks.json"
    _load_task_state()

    logger.info("A2A inbound server initialized")


def _check_a2a_kill_switch() -> bool:
    """Check if A2A is active (both feature flag and runtime kill switch)."""
    try:
        from src.core.feature_flags import get_all_flags
        flags = get_all_flags()
        # Check runtime A2A_ALL kill switch via persisted state
        if not flags.get("FEATURE_A2A", False):
            return False
    except Exception as exc:
        logger.warning("Failed to inspect A2A feature flags; leaving A2A enabled: %s", exc)
    return True


# ── Request Models ───────────────────────────────────────────

class TaskSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Dict[str, Any] = Field(..., description="A2A message with role and parts")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    context: Optional[Dict[str, Any]] = None


# ── Agent Card ───────────────────────────────────────────────

@a2a_server_router.get("/.well-known/agent.json")
async def get_agent_card(request: Request):
    """Serve Lancelot's Agent Card for A2A discovery."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    soul = _get_soul()
    if _agent_card_generator is None or soul is None:
        raise HTTPException(status_code=503, detail="A2A server not initialized")

    base_url = str(request.base_url).rstrip("/")
    card = _agent_card_generator(
        soul=soul,
        base_url=base_url,
    )
    return JSONResponse(content=card.to_dict())


# ── Task Submission ──────────────────────────────────────────

@a2a_server_router.post("/a2a/tasks/send")
async def send_task(body: TaskSendRequest, request: Request):
    """Accept an inbound A2A task for governed execution."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    if _inbound_pipeline is None:
        raise HTTPException(status_code=503, detail="A2A server not initialized")

    if is_runtime_paused():
        pause_state = get_runtime_pause_status()
        task_id = str(uuid.uuid4())
        return JSONResponse(
            status_code=423,
            content={
                "id": task_id,
                "status": A2ATaskStatus.FAILED.value,
                "error": pause_state.get("reason") or "Runtime paused by operator",
            },
        )

    # Build A2A task from request
    task = A2ATask(
        message=A2AMessage.from_dict(body.message),
        metadata=body.metadata,
    )

    # Resolve caller identity from request
    caller = _resolve_caller_from_request(request)

    # Run inbound governance pipeline
    result = _inbound_pipeline.evaluate(task, caller)
    resolved_caller = result.resolved_caller or caller

    if not result.allowed:
        # Return governance-neutral error per spec
        return JSONResponse(
            status_code=403,
            content={
                "id": task.id,
                "status": A2ATaskStatus.FAILED.value,
                "error": result.external_reason,
            },
        )

    if result.requires_approval:
        # T3 gate — task is held for approval
        _active_tasks[task.id] = {
            "task": task.to_dict(),
            "status": A2ATaskStatus.WORKING.value,
            "quest_id": result.quest_id,
            "message": "Task is under human review",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "caller_agent_id": resolved_caller.agent_id,
        }
        _save_task_state()
        return JSONResponse(
            status_code=202,
            content={
                "id": task.id,
                "status": A2ATaskStatus.WORKING.value,
                "message": "Task submitted for review",
            },
        )

    # Task cleared all gates — execute as quest
    _active_tasks[task.id] = {
        "task": task.to_dict(),
        "status": A2ATaskStatus.WORKING.value,
        "quest_id": result.quest_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "caller_agent_id": resolved_caller.agent_id,
    }
    _save_task_state()

    if _task_executor is None:
        raise HTTPException(status_code=503, detail="A2A task executor not initialized")

    try:
        execution = _task_executor(task=task, caller=resolved_caller, quest_id=result.quest_id)
    except Exception as exc:
        logger.error("A2A inbound execution failed for task %s: %s", task.id, exc)
        _active_tasks[task.id]["status"] = A2ATaskStatus.FAILED.value
        _active_tasks[task.id]["error"] = str(exc)
        _save_task_state()
        return JSONResponse(
            status_code=500,
            content={
                "id": task.id,
                "status": A2ATaskStatus.FAILED.value,
                "error": "Task execution failed.",
            },
        )

    task_status = execution.get("status", A2ATaskStatus.COMPLETED.value)
    artifacts = execution.get("artifacts", [])
    _active_tasks[task.id]["status"] = task_status
    _active_tasks[task.id]["artifacts"] = artifacts
    if execution.get("message"):
        _active_tasks[task.id]["message"] = execution["message"]
    _save_task_state()

    if task_status == A2ATaskStatus.COMPLETED.value:
        _inbound_pipeline.complete_task(task, resolved_caller, result.quest_id)

    return JSONResponse(content={
        "id": task.id,
        "status": task_status,
        "artifacts": artifacts,
    })


# ── Task Status ──────────────────────────────────────────────

@a2a_server_router.get("/a2a/tasks/{task_id}")
async def get_task_status(task_id: str, request: Request):
    """Get the status of an A2A task."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    task_data = _require_task_access(task_id, request)

    return JSONResponse(content={
        "id": task_id,
        "status": task_data.get("status", A2ATaskStatus.WORKING.value),
        "artifacts": task_data.get("artifacts", []),
    })


# ── SSE Streaming ────────────────────────────────────────────

@a2a_server_router.get("/a2a/tasks/{task_id}/subscribe")
async def subscribe_task(task_id: str, request: Request):
    """SSE streaming for task progress."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    task_data = _require_task_access(task_id, request)

    async def event_stream():
        """Generate SSE events for task progress."""
        # Send current status
        yield f"data: {json.dumps({'id': task_id, 'status': task_data.get('status', 'working')})}\n\n"

        # Poll for updates (simplified — production would use event bus)
        for _ in range(60):  # 60 second max stream
            await asyncio.sleep(1)
            current = _active_tasks.get(task_id, {})
            status = current.get("status", A2ATaskStatus.WORKING.value)

            if status in (A2ATaskStatus.COMPLETED.value, A2ATaskStatus.FAILED.value,
                          A2ATaskStatus.CANCELED.value):
                yield f"data: {json.dumps({'id': task_id, 'status': status, 'artifacts': current.get('artifacts', [])})}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ── Helpers ──────────────────────────────────────────────────

def _resolve_caller_from_request(request: Request) -> CallerInfo:
    """Extract caller identity from request headers."""
    agent_id = request.headers.get("X-Agent-ID", "").strip()
    agent_name = request.headers.get("X-Agent-Name", "").strip()
    agent_framework = request.headers.get("X-Agent-Framework", AgentFramework.UNKNOWN.value)
    agent_card_url = request.headers.get("X-Agent-Card-URL", "").strip()

    auth_header = request.headers.get("Authorization", "").strip()
    api_key = request.headers.get("X-API-Key", "").strip()
    auth_method = "none"
    credential_value = ""
    if auth_header.startswith("Bearer ") and len(auth_header) > 7:
        auth_method = "bearer_token"
        credential_value = auth_header.split(" ", 1)[1].strip()
    elif api_key:
        auth_method = "api_key"
        credential_value = api_key

    return CallerInfo(
        agent_id=agent_id,
        display_name=agent_name or agent_id,
        agent_framework=agent_framework.lower(),
        agent_card_url=agent_card_url,
        authenticated=False,
        auth_method=auth_method,
        credential_value=credential_value,
    )


def _require_task_access(task_id: str, request: Request) -> Dict[str, Any]:
    """Require the same authenticated peer identity for task reads."""
    if task_id not in _active_tasks:
        _load_task_state()
    task_data = _active_tasks.get(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if _inbound_pipeline is None:
        raise HTTPException(status_code=503, detail="A2A server not initialized")

    caller = _inbound_pipeline.authenticate_caller(_resolve_caller_from_request(request))
    if not caller.authenticated:
        raise HTTPException(status_code=401, detail="Inbound peer authentication required")

    owner_agent_id = task_data.get("caller_agent_id", "")
    if owner_agent_id and caller.agent_id != owner_agent_id:
        raise HTTPException(status_code=403, detail="Task belongs to a different A2A peer")

    return task_data
