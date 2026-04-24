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

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional

from src.core.scheduler.service import SchedulerService
from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused

logger = logging.getLogger(__name__)

# Optional: import event_bus for War Room notifications
try:
    from event_bus import event_bus, Event
    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False


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


def _scheduled_skill_failure_reason(result: Any) -> Optional[str]:
    """Return an operator-readable failure reason from a scheduled skill result."""
    if result is None:
        return None

    success = getattr(result, "success", None)
    if success is False:
        return (
            getattr(result, "error", None)
            or "Scheduled skill returned success=False without an error message"
        )

    outputs = getattr(result, "outputs", None)
    if outputs is None and isinstance(result, dict):
        outputs = result

    if isinstance(outputs, dict):
        if str(outputs.get("status", "")).lower() == "error":
            return str(outputs.get("error") or "Scheduled skill returned status=error")

        if "return_code" in outputs:
            try:
                return_code = int(outputs.get("return_code"))
            except (TypeError, ValueError):
                return_code = 1
            if return_code != 0:
                detail = (
                    outputs.get("stderr")
                    or outputs.get("stdout")
                    or outputs.get("command")
                    or "no command output"
                )
                return f"Scheduled command exited with return_code={return_code}: {str(detail)[:500]}"

    return None


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
        self._tick_state_lock = threading.Lock()
        self._job_locks: Dict[str, threading.Lock] = {}
        self._job_locks_guard = threading.Lock()
        self._approval_state_lock = threading.Lock()
        self._approvals_file = Path(self._scheduler._data_dir) / "scheduler_approvals.json"
        # F-008: Pending approval tracking
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}
        self._granted_approvals: Dict[str, str] = {}  # job_id -> ISO timestamp
        self._load_approval_state()

    @property
    def receipts(self) -> List[Dict[str, Any]]:
        return list(self._receipts)

    @property
    def is_running(self) -> bool:
        """True when the scheduler tick loop thread is alive."""
        return bool(self._tick_thread and self._tick_thread.is_alive() and not self._stop_event.is_set())

    def _emit_receipt(self, event: str, **kwargs: Any) -> Dict[str, Any]:
        receipt = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        self._receipts.append(receipt)
        logger.info("%s: %s", event, {k: v for k, v in kwargs.items()})
        return receipt

    def _load_approval_state(self) -> None:
        """Restore pending and granted approvals from disk."""
        try:
            if not self._approvals_file.exists():
                self._pending_approvals = {}
                self._granted_approvals = {}
                return
            data = json.loads(self._approvals_file.read_text(encoding="utf-8"))
            self._pending_approvals = dict(data.get("pending", {}))
            self._granted_approvals = dict(data.get("granted", {}))
        except Exception as exc:
            logger.warning("Failed to load scheduler approval state: %s", exc)
            self._pending_approvals = {}
            self._granted_approvals = {}

    def _save_approval_state(self) -> None:
        """Persist pending and granted approvals to disk."""
        payload = {
            "pending": self._pending_approvals,
            "granted": self._granted_approvals,
        }
        with self._approval_state_lock:
            try:
                self._approvals_file.parent.mkdir(parents=True, exist_ok=True)
                self._approvals_file.write_text(
                    json.dumps(payload, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Failed to persist scheduler approval state: %s", exc)

    # ------------------------------------------------------------------
    # Tick loop — evaluates cron/interval triggers every 60 seconds
    # ------------------------------------------------------------------

    def start_tick_loop(self) -> None:
        """Start the background scheduler tick loop."""
        with self._tick_state_lock:
            if self._tick_thread and self._tick_thread.is_alive():
                logger.debug("Scheduler tick loop start skipped; loop is already running")
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._tick_loop, daemon=True, name="scheduler-tick"
            )
            self._tick_thread = thread

        thread.start()
        logger.info("Scheduler tick loop started (60s interval)")

    def stop(self) -> None:
        """Stop the tick loop."""
        with self._tick_state_lock:
            thread = self._tick_thread
            was_running = bool(thread and thread.is_alive())

        self._stop_event.set()
        if thread:
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning("Scheduler tick loop did not stop within 5s")
                return
            with self._tick_state_lock:
                if self._tick_thread is thread:
                    self._tick_thread = None

        if was_running:
            logger.info("Scheduler tick loop stopped")
        else:
            logger.debug("Scheduler tick loop stop skipped; loop was not running")

    def _tick_loop(self) -> None:
        """Background loop that checks jobs every 60 seconds."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            if self._stop_event.wait(timeout=60):
                return

    def _tick(self) -> None:
        """Single tick — evaluate all jobs."""
        now_utc = datetime.now(timezone.utc)
        self._scheduler.mark_scheduler_tick(now_utc.isoformat())
        if is_runtime_paused():
            logger.info("Scheduler tick skipped while runtime is paused")
            return
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
                        except (ValueError, TypeError) as exc:
                            logger.debug(
                                "Ignoring malformed scheduler last_run_at %r for job %s: %s",
                                job.last_run_at,
                                job.id,
                                exc,
                            )
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
        return self.execute_job_with_identity(job_id)

    def execute_job_with_identity(
        self,
        job_id: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
    ) -> JobExecutionResult:
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
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason="Already running (concurrent execution blocked)",
                receipt=receipt,
            )
        try:
            return self._execute_job_inner(
                job_id,
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
            )
        finally:
            lock.release()

    def _execute_job_inner(
        self,
        job_id: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
    ) -> JobExecutionResult:
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
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason="Job is disabled",
                receipt=receipt,
            )

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            reason = pause_state.get("reason") or "Runtime paused by operator"
            receipt = self._emit_receipt(
                "scheduled_job_skipped",
                job_id=job_id,
                reason=reason,
                gate="runtime_pause",
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
            )
            return JobExecutionResult(
                job_id=job_id,
                skipped=True,
                skip_reason=reason,
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
                        operator_id=operator_id,
                        session_id=session_id,
                        actor=actor,
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
                    operator_id=operator_id,
                    session_id=session_id,
                    actor=actor,
                )
                return JobExecutionResult(
                    job_id=job_id,
                    skipped=True,
                    skip_reason=reason,
                    receipt=receipt,
                )

        # F-008: Check approvals — skip unless owner has granted approval
        if job.requires_approvals:
            if job_id in self._granted_approvals:
                # Approval was granted — consume it and proceed
                del self._granted_approvals[job_id]
                self._pending_approvals.pop(job_id, None)
                self._save_approval_state()
                logger.info("Job '%s' approval granted, executing", job_id)
            else:
                # Request approval via War Room notification
                self._request_approval(job_id, job.name, job.requires_approvals)
                receipt = self._emit_receipt(
                    "scheduled_job_awaiting_approval",
                    job_id=job_id,
                    reason="Awaiting owner approval",
                    required_approvals=job.requires_approvals,
                    operator_id=operator_id,
                    session_id=session_id,
                    actor=actor,
                )
                return JobExecutionResult(
                    job_id=job_id,
                    skipped=True,
                    skip_reason="Awaiting owner approval",
                    receipt=receipt,
                )

        # Execute — pass job inputs to skill
        job_inputs = job.inputs if isinstance(job.inputs, dict) else {}
        start = time.monotonic()
        try:
            if not job.skill:
                raise RuntimeError("Scheduled job is missing a skill binding")
            if not self._skill_execute_fn:
                raise RuntimeError("Scheduler skill executor is not configured")

            skill_result = self._skill_execute_fn(job.skill, job_inputs)
            failure_reason = _scheduled_skill_failure_reason(skill_result)
            if failure_reason:
                raise RuntimeError(failure_reason)

            duration_ms = (time.monotonic() - start) * 1000

            # Update scheduler record
            self._scheduler.run_now(job_id)

            receipt = self._emit_receipt(
                "scheduled_job_run",
                job_id=job_id,
                skill=job.skill,
                duration_ms=round(duration_ms, 2),
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
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
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
            )
            return JobExecutionResult(
                job_id=job_id,
                executed=True,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
                receipt=receipt,
            )

    # ------------------------------------------------------------------
    # F-008: Approval workflow
    # ------------------------------------------------------------------

    def _request_approval(self, job_id: str, job_name: str, required: List[str]) -> None:
        """Emit a War Room event requesting owner approval for a job."""
        if job_id in self._pending_approvals:
            return  # Already requested, don't spam

        self._pending_approvals[job_id] = {
            "job_name": job_name,
            "required_approvals": required,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_approval_state()
        logger.info(
            "Job '%s' requires approvals %s — notifying via War Room",
            job_id, required,
        )

        if _HAS_EVENT_BUS:
            import asyncio
            try:
                event = Event(
                    type="scheduler_approval_required",
                    payload={
                        "job_id": job_id,
                        "job_name": job_name,
                        "required_approvals": required,
                        "message": f"Scheduled job '{job_name}' requires owner approval to execute.",
                    },
                )
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(event_bus.emit(event))
                else:
                    loop.run_until_complete(event_bus.emit(event))
            except Exception as exc:
                logger.warning("Failed to emit approval request event: %s", exc)

    def approve_job(
        self,
        job_id: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
    ) -> bool:
        """Grant approval for a pending job. Returns True if approval was pending."""
        if job_id not in self._pending_approvals:
            return False
        self._granted_approvals[job_id] = datetime.now(timezone.utc).isoformat()
        self._save_approval_state()
        logger.info(
            "Approval granted for job '%s' by %s",
            job_id,
            actor or operator_id or "operator",
        )

        self._emit_receipt(
            "scheduled_job_approved",
            job_id=job_id,
            approved_at=self._granted_approvals[job_id],
            operator_id=operator_id,
            session_id=session_id,
            actor=actor,
        )
        return True

    def clear_approval_state(
        self,
        *,
        reason: str = "",
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
    ) -> Dict[str, int]:
        """Clear pending/granted approvals during a runtime full stop."""
        pending_count = len(self._pending_approvals)
        granted_count = len(self._granted_approvals)
        if pending_count == 0 and granted_count == 0:
            return {"pending_cleared": 0, "granted_cleared": 0}

        self._pending_approvals = {}
        self._granted_approvals = {}
        self._save_approval_state()
        self._emit_receipt(
            "scheduled_job_approvals_cleared",
            pending_cleared=pending_count,
            granted_cleared=granted_count,
            reason=reason,
            operator_id=operator_id,
            session_id=session_id,
            actor=actor,
        )
        return {
            "pending_cleared": pending_count,
            "granted_cleared": granted_count,
        }

    @property
    def pending_approvals(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of pending approval requests."""
        return dict(self._pending_approvals)
