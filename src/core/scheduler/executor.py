"""
Job Executor — execution pipeline with gating and receipts (Prompt 13 / D4-D6).

Executes scheduled jobs through a gating pipeline before invoking the
skill executor.

Public API:
    JobExecutor(scheduler_service, skill_executor, gates)
    execute_job(job_id) → JobExecutionResult
    receipts            → list[dict]
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.core.scheduler.service import SchedulerService, JobRecord
from src.core.scheduler.schema import SchedulerError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate protocol
# ---------------------------------------------------------------------------

@dataclass
class Gate:
    """A gate check that must pass before a job can run."""
    name: str
    check_fn: Callable[[], bool]
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class JobExecutionResult:
    """Result of a job execution attempt."""
    job_id: str
    executed: bool = False
    skipped: bool = False
    skip_reason: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0
    receipt: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class JobExecutor:
    """Executes scheduled jobs through a gating pipeline."""

    def __init__(
        self,
        scheduler_service: SchedulerService,
        skill_execute_fn: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        gates: Optional[List[Gate]] = None,
    ):
        self._scheduler = scheduler_service
        self._skill_execute_fn = skill_execute_fn
        self._gates = gates or []
        self._receipts: List[Dict[str, Any]] = []

    @property
    def receipts(self) -> List[Dict[str, Any]]:
        return list(self._receipts)

    def _emit_receipt(self, event: str, **kwargs: Any) -> Dict[str, Any]:
        receipt = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        self._receipts.append(receipt)
        logger.info("%s: %s", event, {k: v for k, v in kwargs.items()})
        return receipt

    def execute_job(self, job_id: str) -> JobExecutionResult:
        """Execute a job through the gating pipeline.

        Gating order:
        1. Job exists and is enabled
        2. All gates pass (onboarding READY, local model, etc.)
        3. Job requires_approvals (placeholder — logs if needed)
        4. Execute via skill function

        Returns:
            JobExecutionResult with execution details.
        """
        # Check job exists
        job = self._scheduler.get_job(job_id)
        if job is None:
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason=f"Job '{job_id}' not found",
            )

        # Check enabled
        if not job.enabled:
            receipt = self._emit_receipt(
                "scheduled_job_skipped",
                job_id=job_id,
                reason="Job is disabled",
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason="Job is disabled",
                receipt=receipt,
            )

        # Run through gates
        for gate in self._gates:
            try:
                if not gate.check_fn():
                    reason = gate.skip_reason or f"Gate '{gate.name}' failed"
                    receipt = self._emit_receipt(
                        "scheduled_job_skipped",
                        job_id=job_id,
                        reason=reason,
                        gate=gate.name,
                    )
                    return JobExecutionResult(
                        job_id=job_id,
                        skipped=True,
                        skip_reason=reason,
                        receipt=receipt,
                    )
            except Exception as exc:
                reason = f"Gate '{gate.name}' error: {exc}"
                receipt = self._emit_receipt(
                    "scheduled_job_skipped",
                    job_id=job_id,
                    reason=reason,
                    gate=gate.name,
                )
                return JobExecutionResult(
                    job_id=job_id,
                    skipped=True,
                    skip_reason=reason,
                    receipt=receipt,
                )

        # Check approvals (placeholder — just log if required)
        if job.requires_approvals:
            logger.info(
                "Job '%s' requires approvals: %s (auto-skipping in current impl)",
                job_id, job.requires_approvals,
            )
            receipt = self._emit_receipt(
                "scheduled_job_skipped",
                job_id=job_id,
                reason="Approvals required but not granted",
                required_approvals=job.requires_approvals,
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason="Approvals required but not granted",
                receipt=receipt,
            )

        # Execute
        start = time.monotonic()
        try:
            if self._skill_execute_fn and job.skill:
                self._skill_execute_fn(job.skill, {})

            duration_ms = (time.monotonic() - start) * 1000

            # Update scheduler record
            self._scheduler.run_now(job_id)

            receipt = self._emit_receipt(
                "scheduled_job_run",
                job_id=job_id,
                skill=job.skill,
                duration_ms=round(duration_ms, 2),
            )
            return JobExecutionResult(
                job_id=job_id,
                executed=True,
                success=True,
                duration_ms=duration_ms,
                receipt=receipt,
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            receipt = self._emit_receipt(
                "scheduled_job_failed",
                job_id=job_id,
                error=str(exc),
                duration_ms=round(duration_ms, 2),
            )
            return JobExecutionResult(
                job_id=job_id,
                executed=True,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
                receipt=receipt,
            )
