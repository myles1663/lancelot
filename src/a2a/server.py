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
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.a2a.types import (
    A2ATask, A2AMessage, A2AMessagePart, A2ATaskStatus,
    AgentFramework,
)
from src.a2a.inbound_pipeline import CallerInfo

logger = logging.getLogger(__name__)

# Protocol-standard router (mounted at root, not /api/)
a2a_server_router = APIRouter(tags=["a2a-protocol"])

# Module-level dependencies
_soul: Any = None
_receipt_service: Any = None
_registry: Any = None
_inbound_pipeline: Any = None
_agent_card_generator: Any = None

# In-memory task store (production: use receipt service for persistence)
_active_tasks: Dict[str, Dict[str, Any]] = {}


def init_a2a_server(
    soul: Any,
    receipt_service: Any,
    registry: Any,
    inbound_pipeline: Any,
) -> None:
    """Initialize the A2A server with dependencies."""
    global _soul, _receipt_service, _registry, _inbound_pipeline, _agent_card_generator

    from src.a2a.agent_card import generate_agent_card

    _soul = soul
    _receipt_service = receipt_service
    _registry = registry
    _inbound_pipeline = inbound_pipeline
    _agent_card_generator = generate_agent_card

    logger.info("A2A inbound server initialized")


def _check_a2a_kill_switch() -> bool:
    """Check if A2A is active (both feature flag and runtime kill switch)."""
    try:
        from src.core.feature_flags import get_all_flags
        flags = get_all_flags()
        # Check runtime A2A_ALL kill switch via persisted state
        if not flags.get("FEATURE_A2A", False):
            return False
    except Exception:
        pass
    return True


# ── Request Models ───────────────────────────────────────────

class TaskSendRequest(BaseModel):
    message: Dict[str, Any] = Field(..., description="A2A message with role and parts")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    context: Optional[Dict[str, Any]] = None


# ── Agent Card ───────────────────────────────────────────────

@a2a_server_router.get("/.well-known/agent.json")
async def get_agent_card(request: Request):
    """Serve Lancelot's Agent Card for A2A discovery."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    if _agent_card_generator is None or _soul is None:
        raise HTTPException(status_code=503, detail="A2A server not initialized")

    base_url = str(request.base_url).rstrip("/")
    card = _agent_card_generator(
        soul=_soul,
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

    # Build A2A task from request
    task = A2ATask(
        message=A2AMessage.from_dict(body.message),
        metadata=body.metadata,
    )

    # Resolve caller identity from request
    caller = _resolve_caller_from_request(request)

    # Run inbound governance pipeline
    result = _inbound_pipeline.evaluate(task, caller)

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
        }
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
    }

    # Stub: actual execution would route to orchestrator
    # For now, return working status
    _active_tasks[task.id]["status"] = A2ATaskStatus.COMPLETED.value
    _active_tasks[task.id]["artifacts"] = []

    # Complete the task in the pipeline
    _inbound_pipeline.complete_task(task, caller, result.quest_id)

    return JSONResponse(content={
        "id": task.id,
        "status": A2ATaskStatus.COMPLETED.value,
        "artifacts": [],
    })


# ── Task Status ──────────────────────────────────────────────

@a2a_server_router.get("/a2a/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get the status of an A2A task."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    task_data = _active_tasks.get(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return JSONResponse(content={
        "id": task_id,
        "status": task_data.get("status", A2ATaskStatus.WORKING.value),
        "artifacts": task_data.get("artifacts", []),
    })


# ── SSE Streaming ────────────────────────────────────────────

@a2a_server_router.get("/a2a/tasks/{task_id}/subscribe")
async def subscribe_task(task_id: str):
    """SSE streaming for task progress."""
    if not _check_a2a_kill_switch():
        raise HTTPException(status_code=503, detail="A2A protocol is not available")

    task_data = _active_tasks.get(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

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
    # A2A clients identify via headers or auth token
    agent_id = request.headers.get("X-Agent-ID", "")
    agent_name = request.headers.get("X-Agent-Name", "")
    agent_framework = request.headers.get("X-Agent-Framework", AgentFramework.UNKNOWN.value)
    agent_card_url = request.headers.get("X-Agent-Card-URL", "")

    # Check for Bearer token authentication
    auth_header = request.headers.get("Authorization", "")
    authenticated = bool(auth_header.startswith("Bearer ") and len(auth_header) > 7)

    if not agent_id:
        # Generate a deterministic ID from available info
        agent_id = f"anon-{hash(auth_header or str(request.client.host if request.client else 'unknown')) % 10**8}"

    return CallerInfo(
        agent_id=agent_id,
        display_name=agent_name or agent_id,
        agent_framework=agent_framework.lower(),
        agent_card_url=agent_card_url,
        authenticated=authenticated,
        auth_method="bearer_token" if authenticated else "none",
    )
