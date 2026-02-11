"""
Lancelot vNext4: Async Verification Queue

Processes verification jobs for T1 actions in the background.
Supports sync fallback when queue is full, drain for tier boundaries,
and rollback wiring for verification failures.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .config import AsyncVerificationConfig
from .models import RiskTier, VerificationStatus

logger = logging.getLogger(__name__)


@dataclass
class VerificationJob:
    """A pending verification task for a T1 action."""
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    step_index: int = 0
    capability: str = ""
    output: Any = None
    risk_tier: RiskTier = RiskTier.T1_REVERSIBLE
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    rollback_action: Optional[Callable] = None
    status: VerificationStatus = VerificationStatus.ASYNC_PENDING


@dataclass
class VerificationResult:
    """Result of a verification job."""
    job_id: str
    passed: bool
    status: VerificationStatus
    error: Optional[str] = None
    completed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class DrainResult:
    """Summary of a drain operation."""
    drained_count: int
    passed: int
    failed: int
    timed_out: bool


class AsyncVerificationQueue:
    """Background verification queue for T1 actions.

    Runs verification asynchronously, one step behind execution.
    Supports sync fallback when queue is full, and drain before
    T2/T3 actions.
    """

    def __init__(
        self,
        verify_fn: Optional[Callable] = None,
        config: Optional[AsyncVerificationConfig] = None,
        on_failure: Optional[Callable] = None,
    ):
        """
        Args:
            verify_fn: Callable(capability, output) -> bool. If None, auto-passes.
            config: Async verification configuration.
            on_failure: Optional callback for verification failures.
        """
        self._verify_fn = verify_fn
        self._config = config
        self._on_failure = on_failure
        self._queue: list[VerificationJob] = []
        self._results: list[VerificationResult] = []
        self._max_depth = config.queue_max_depth if config else 10
        self._fallback_to_sync = config.fallback_to_sync_on_full if config else True

    def submit(self, job: VerificationJob) -> str:
        """Submit a verification job.

        If queue is full and fallback_to_sync is True, runs synchronously.

        Returns:
            The job_id.
        """
        if len(self._queue) >= self._max_depth:
            if self._fallback_to_sync:
                result = self._verify_sync(job)
                self._results.append(result)
                if not result.passed and self._on_failure:
                    self._on_failure(job, result)
                return job.job_id
            else:
                raise RuntimeError(f"Verification queue full ({self._max_depth})")

        self._queue.append(job)
        return job.job_id

    def process_pending(self) -> list[VerificationResult]:
        """Process all pending jobs in the queue.

        Returns list of VerificationResults.
        """
        results = []
        while self._queue:
            job = self._queue.pop(0)
            result = self._verify_sync(job)
            results.append(result)
            self._results.append(result)
            if not result.passed and self._on_failure:
                self._on_failure(job, result)
        return results

    def drain(self, timeout_seconds: float = 30.0) -> DrainResult:
        """Process ALL pending jobs. Used before T2/T3 actions and at shutdown.

        Returns DrainResult summarizing outcomes.
        """
        results = self.process_pending()
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        return DrainResult(
            drained_count=len(results),
            passed=passed,
            failed=failed,
            timed_out=False,
        )

    def _verify_sync(self, job: VerificationJob) -> VerificationResult:
        """Run verification synchronously for a single job."""
        try:
            if self._verify_fn is None:
                passed = True
            else:
                passed = self._verify_fn(job.capability, job.output)
            status = (
                VerificationStatus.ASYNC_PASSED if passed
                else VerificationStatus.ASYNC_FAILED
            )
            result = VerificationResult(job_id=job.job_id, passed=passed, status=status)
            if not passed and job.rollback_action:
                try:
                    job.rollback_action()
                    logger.info("Rollback executed for job %s", job.job_id)
                except Exception as rb_err:
                    logger.error("Rollback failed for job %s: %s", job.job_id, rb_err)
            return result
        except Exception as e:
            result = VerificationResult(
                job_id=job.job_id,
                passed=False,
                status=VerificationStatus.ASYNC_FAILED,
                error=str(e),
            )
            if job.rollback_action:
                try:
                    job.rollback_action()
                except Exception as rb_err:
                    logger.error("Rollback failed for job %s: %s", job.job_id, rb_err)
            return result

    @property
    def depth(self) -> int:
        """Current queue depth."""
        return len(self._queue)

    @property
    def pending_jobs(self) -> list[str]:
        """Job IDs currently in the queue."""
        return [job.job_id for job in self._queue]

    @property
    def results(self) -> list[VerificationResult]:
        """All verification results (completed)."""
        return list(self._results)

    def get_failed_results(self) -> list[VerificationResult]:
        """Get only failed verification results."""
        return [r for r in self._results if not r.passed]

    def has_failures(self) -> bool:
        """Check if any verification has failed."""
        return any(not r.passed for r in self._results)

    def clear_results(self) -> None:
        """Clear completed results."""
        self._results.clear()
