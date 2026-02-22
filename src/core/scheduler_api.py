"""
Scheduler Management API — War Room endpoints for job management.

Provides endpoints for listing, enabling/disabling, and triggering
scheduled jobs from the War Room UI.

Router prefix: /api/scheduler
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

# Module-level references, set during app startup
_service = None  # SchedulerService
_executor = None  # JobExecutor (optional)


def init_scheduler_api(service, executor=None) -> None:
    """Initialize API with scheduler service and optional executor."""
    global _service, _executor
    _service = service
    _executor = executor


# ── Response Models ──────────────────────────────────────────────


class JobResponse(BaseModel):
    id: str
    name: str
    skill: str
    enabled: bool
    trigger_type: str
    trigger_value: str
    timezone: str = "UTC"
    requires_ready: bool
    requires_approvals: List[str]
    timeout_s: int
    description: str
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None
    run_count: int
    registered_at: str


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int
    enabled_count: int


class JobToggleResponse(BaseModel):
    id: str
    enabled: bool


class JobDeleteResponse(BaseModel):
    id: str
    deleted: bool


class JobTimezoneRequest(BaseModel):
    timezone: str


class JobTimezoneResponse(BaseModel):
    id: str
    timezone: str


class JobApprovalResponse(BaseModel):
    id: str
    approved: bool
    message: str


class PendingApprovalsResponse(BaseModel):
    pending: dict
    count: int


class JobTriggerResponse(BaseModel):
    id: str
    executed: bool
    success: bool
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/jobs", response_model=JobListResponse)
def list_jobs():
    """List all scheduled jobs."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    try:
        records = _service.list_jobs()
        jobs = [
            JobResponse(
                id=r.id,
                name=r.name,
                skill=r.skill,
                enabled=r.enabled,
                trigger_type=r.trigger_type,
                trigger_value=r.trigger_value,
                timezone=r.timezone,
                requires_ready=r.requires_ready,
                requires_approvals=r.requires_approvals,
                timeout_s=r.timeout_s,
                description=r.description,
                last_run_at=r.last_run_at,
                last_run_status=r.last_run_status,
                run_count=r.run_count,
                registered_at=r.registered_at,
            )
            for r in records
        ]
        enabled_count = sum(1 for j in jobs if j.enabled)
        return JobListResponse(
            jobs=jobs,
            total=len(jobs),
            enabled_count=enabled_count,
        )
    except Exception as exc:
        logger.exception("Failed to list scheduler jobs")
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    """Get a single scheduled job by ID."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    record = _service.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JobResponse(
        id=record.id,
        name=record.name,
        skill=record.skill,
        enabled=record.enabled,
        trigger_type=record.trigger_type,
        trigger_value=record.trigger_value,
        timezone=record.timezone,
        requires_ready=record.requires_ready,
        requires_approvals=record.requires_approvals,
        timeout_s=record.timeout_s,
        description=record.description,
        last_run_at=record.last_run_at,
        last_run_status=record.last_run_status,
        run_count=record.run_count,
        registered_at=record.registered_at,
    )


@router.post("/jobs/{job_id}/enable", response_model=JobToggleResponse)
def enable_job(job_id: str):
    """Enable a scheduled job."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    try:
        _service.enable_job(job_id)
        return JobToggleResponse(id=job_id, enabled=True)
    except Exception as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/jobs/{job_id}/disable", response_model=JobToggleResponse)
def disable_job(job_id: str):
    """Disable a scheduled job."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    try:
        _service.disable_job(job_id)
        return JobToggleResponse(id=job_id, enabled=False)
    except Exception as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.delete("/jobs/{job_id}", response_model=JobDeleteResponse)
def delete_job(job_id: str):
    """Delete a scheduled job permanently."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    record = _service.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    try:
        _service.delete_job(job_id)
        logger.info("Deleted scheduled job '%s'", job_id)
        return JobDeleteResponse(id=job_id, deleted=True)
    except Exception as exc:
        logger.exception("Failed to delete job %s", job_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/jobs/{job_id}/trigger", response_model=JobTriggerResponse)
def trigger_job(job_id: str):
    """Manually trigger a scheduled job."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )

    # Verify job exists
    record = _service.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    # Use executor if available (runs through gates + receipts)
    if _executor:
        try:
            result = _executor.execute_job(job_id)
            return JobTriggerResponse(
                id=job_id,
                executed=result.executed,
                success=result.success,
                skip_reason=result.skip_reason,
                error=result.error,
                duration_ms=round(result.duration_ms, 2),
            )
        except Exception as exc:
            logger.exception("Failed to execute job %s", job_id)
            return JSONResponse(status_code=500, content={"error": str(exc)})

    # Fallback: mark as triggered via service
    try:
        _service.run_now(job_id)
        return JobTriggerResponse(
            id=job_id,
            executed=True,
            success=True,
            duration_ms=0.0,
        )
    except Exception as exc:
        logger.exception("Failed to trigger job %s", job_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/jobs/{job_id}/approve", response_model=JobApprovalResponse)
def approve_job(job_id: str):
    """Approve a scheduled job that requires owner approval (F-008)."""
    if _executor is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Job executor not initialized"},
        )
    record = _service.get_job(job_id) if _service else None
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    approved = _executor.approve_job(job_id)
    if approved:
        return JobApprovalResponse(
            id=job_id, approved=True,
            message=f"Job '{job_id}' approved. Will execute on next scheduler tick.",
        )
    return JobApprovalResponse(
        id=job_id, approved=False,
        message=f"Job '{job_id}' has no pending approval request.",
    )


@router.get("/approvals/pending", response_model=PendingApprovalsResponse)
def list_pending_approvals():
    """List all jobs awaiting owner approval (F-008)."""
    if _executor is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Job executor not initialized"},
        )
    pending = _executor.pending_approvals
    return PendingApprovalsResponse(pending=pending, count=len(pending))


@router.patch("/jobs/{job_id}/timezone", response_model=JobTimezoneResponse)
def update_job_timezone(job_id: str, body: JobTimezoneRequest):
    """Update the timezone for a scheduled job."""
    if _service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Scheduler service not initialized"},
        )
    # Validate timezone
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(body.timezone)
    except (KeyError, Exception):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: '{body.timezone}'")

    try:
        _service.update_job_timezone(job_id, body.timezone)
        return JobTimezoneResponse(id=job_id, timezone=body.timezone)
    except Exception as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(status_code=500, content={"error": str(exc)})
