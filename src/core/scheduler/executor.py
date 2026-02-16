"""
Job Executor — execution pipeline with gating, receipts, and cron tick loop.

Executes scheduled jobs through a gating pipeline before invoking the
skill executor.  The tick loop evaluates cron/interval triggers every 60 s.

Public API:
    JobExecutor(scheduler_service, skill_executor, gates)
    execute_job(job_id) → JobExecutionResult
    start_tick_loop()   → starts background thread
    stop()              → stops background thread
    receipts            → list[dict]
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional

from src.core.scheduler.service import SchedulerService

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
# Cron matching (no external dependency)
# ---------------------------------------------------------------------------

def _cron_matches(expression: str, now: datetime) -> bool:
    """Check if a 5-field cron expression matches the current time.

    Supports: specific values, '*' (any), ',' (list), '-' (range).
    Day-of-week: 0=Sunday (cron convention).
    """
    fields = expression.strip().split()
    if len(fields) != 5:
        return False

    # Python weekday: 0=Mon..6=Sun → cron: 0=Sun..6=Sat
    cron_dow = (now.weekday() + 1) % 7

    checks = [
        (fields[0], now.minute),
        (fields[1], now.hour),
        (fields[2], now.day),
        (fields[3], now.month),
        (fields[4], cron_dow),
    ]
    for pattern, value in checks:
        if pattern == "*":
            continue
        if "," in pattern:
            if value not in [int(v) for v in pattern.split(",")]:
                return False
        elif "-" in pattern:
            lo, hi = pattern.split("-", 1)
            if not (int(lo) <= value <= int(hi)):
                return False
        else:
            try:
                if int(pattern) != value:
                    return False
            except ValueError:
                return False
    return True


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
        self._tick_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._job_locks: Dict[str, threading.Lock] = {}
        self._job_locks_guard = threading.Lock()

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

    # ------------------------------------------------------------------
    # Tick loop — evaluates cron/interval triggers every 60 seconds
    # ------------------------------------------------------------------

    def start_tick_loop(self) -> None:
        """Start the background scheduler tick loop."""
        if self._tick_thread and self._tick_thread.is_alive():
            logger.warning("Tick loop already running")
            return
        self._stop_event.clear()
        self._tick_thread = threading.Thread(
            target=self._tick_loop, daemon=True, name="scheduler-tick"
        )
        self._tick_thread.start()
        logger.info("Scheduler tick loop started (60s interval)")

    def stop(self) -> None:
        """Stop the tick loop."""
        self._stop_event.set()
        if self._tick_thread:
            self._tick_thread.join(timeout=5)
        logger.info("Scheduler tick loop stopped")

    def _tick_loop(self) -> None:
        """Background loop that checks jobs every 60 seconds."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            # Sleep in small increments so stop() is responsive
            for _ in range(60):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _tick(self) -> None:
        """Single tick — evaluate all jobs."""
        now_utc = datetime.now(timezone.utc)
        jobs = self._scheduler.list_jobs()
        fired = 0

        for job in jobs:
            if not job.enabled or not job.skill:
                continue

            should_run = False

            if job.trigger_type == "cron" and job.trigger_value:
                # Convert UTC to the job's timezone for cron evaluation
                job_tz = ZoneInfo(job.timezone) if job.timezone and job.timezone != "UTC" else timezone.utc
                now_local = now_utc.astimezone(job_tz)
                if _cron_matches(job.trigger_value, now_local):
                    # Prevent double-fire within the same minute
                    if job.last_run_at:
                        try:
                            last = datetime.fromisoformat(job.last_run_at)
                            last_local = last.astimezone(job_tz) if last.tzinfo else last
                            if (
                                last_local.year == now_local.year
                                and last_local.month == now_local.month
                                and last_local.day == now_local.day
                                and last_local.hour == now_local.hour
                                and last_local.minute == now_local.minute
                            ):
                                continue  # Already ran this minute
                        except (ValueError, TypeError):
                            pass
                    should_run = True

            elif job.trigger_type == "interval" and job.trigger_value:
                try:
                    interval_s = int(job.trigger_value)
                except ValueError:
                    continue
                if job.last_run_at:
                    try:
                        last = datetime.fromisoformat(job.last_run_at)
                        elapsed = (now_utc - last).total_seconds()
                        if elapsed >= interval_s:
                            should_run = True
                    except (ValueError, TypeError):
                        should_run = True
                else:
                    should_run = True  # Never run before

            if should_run:
                logger.info("Scheduler tick: firing job '%s' (skill=%s)", job.id, job.skill)
                self.execute_job(job.id)
                fired += 1

        if fired:
            logger.info("Scheduler tick: fired %d job(s)", fired)

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------

    def _get_job_lock(self, job_id: str) -> threading.Lock:
        """Get or create a per-job lock to prevent concurrent execution."""
        with self._job_locks_guard:
            if job_id not in self._job_locks:
                self._job_locks[job_id] = threading.Lock()
            return self._job_locks[job_id]

    def execute_job(self, job_id: str) -> JobExecutionResult:
        """Execute a job through the gating pipeline.

        Gating order:
        1. Job exists and is enabled
        2. All gates pass (onboarding READY, local model, etc.)
        3. Job requires_approvals (placeholder — logs if needed)
        4. Execute via skill function

        Per-job locking prevents concurrent execution of the same job
        from the tick loop and the run_now API.

        Returns:
            JobExecutionResult with execution details.
        """
        lock = self._get_job_lock(job_id)
        if not lock.acquire(blocking=False):
            logger.info("Job '%s' already running, skipping", job_id)
            receipt = self._emit_receipt(
                "scheduled_job_skipped",
                job_id=job_id,
                reason="Already running (concurrent execution blocked)",
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason="Already running (concurrent execution blocked)",
                receipt=receipt,
            )
        try:
            return self._execute_job_inner(job_id)
        finally:
            lock.release()

    def _execute_job_inner(self, job_id: str) -> JobExecutionResult:
        """Inner execution logic (called with per-job lock held)."""
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

        # Execute — pass job inputs to skill
        job_inputs = job.inputs if isinstance(job.inputs, dict) else {}
        start = time.monotonic()
        try:
            if self._skill_execute_fn and job.skill:
                self._skill_execute_fn(job.skill, job_inputs)

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
