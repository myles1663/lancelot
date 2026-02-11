"""Tests for vNext4 AsyncVerificationQueue (Prompts 12, 14)."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.async_verifier import (
    AsyncVerificationQueue,
    VerificationJob,
    VerificationResult,
    DrainResult,
)
from governance.config import AsyncVerificationConfig
from governance.models import RiskTier, VerificationStatus


@pytest.fixture
def config():
    return AsyncVerificationConfig(max_workers=2, queue_max_depth=10)


@pytest.fixture
def queue(config):
    return AsyncVerificationQueue(config=config)


def make_job(**kwargs):
    return VerificationJob(capability="fs.write", **kwargs)


# ── Core Queue Tests (Prompt 12) ────────────────────────────────

def test_submit_increases_depth(queue):
    queue.submit(make_job())
    assert queue.depth == 1


def test_process_pending_returns_results(queue):
    queue.submit(make_job())
    results = queue.process_pending()
    assert len(results) == 1
    assert results[0].passed is True


def test_process_pending_clears_queue(queue):
    queue.submit(make_job())
    queue.process_pending()
    assert queue.depth == 0


def test_verify_fn_none_auto_passes(queue):
    """No verify_fn means all verifications pass."""
    queue.submit(make_job())
    results = queue.process_pending()
    assert results[0].passed is True
    assert results[0].status == VerificationStatus.ASYNC_PASSED


def test_verify_fn_returns_true():
    q = AsyncVerificationQueue(verify_fn=lambda cap, out: True)
    q.submit(make_job(output="good"))
    results = q.process_pending()
    assert results[0].passed is True
    assert results[0].status == VerificationStatus.ASYNC_PASSED


def test_verify_fn_returns_false():
    q = AsyncVerificationQueue(verify_fn=lambda cap, out: False)
    q.submit(make_job(output="bad"))
    results = q.process_pending()
    assert results[0].passed is False
    assert results[0].status == VerificationStatus.ASYNC_FAILED


def test_verify_fn_raises():
    def bad_fn(cap, out):
        raise ValueError("boom")
    q = AsyncVerificationQueue(verify_fn=bad_fn)
    q.submit(make_job())
    results = q.process_pending()
    assert results[0].passed is False
    assert results[0].status == VerificationStatus.ASYNC_FAILED
    assert "boom" in results[0].error


def test_queue_full_sync_fallback():
    config = AsyncVerificationConfig(queue_max_depth=2, fallback_to_sync_on_full=True)
    q = AsyncVerificationQueue(config=config)
    q.submit(make_job())
    q.submit(make_job())
    # Queue is now full, 3rd should run synchronously
    q.submit(make_job())
    assert q.depth == 2  # Only 2 in queue, 3rd ran sync
    assert len(q.results) == 1  # sync result stored


def test_queue_full_no_fallback_raises():
    config = AsyncVerificationConfig(queue_max_depth=2, fallback_to_sync_on_full=False)
    q = AsyncVerificationQueue(config=config)
    q.submit(make_job())
    q.submit(make_job())
    with pytest.raises(RuntimeError):
        q.submit(make_job())


def test_drain_processes_all(queue):
    queue.submit(make_job())
    queue.submit(make_job())
    result = queue.drain()
    assert result.drained_count == 2
    assert result.passed == 2
    assert result.failed == 0
    assert result.timed_out is False


def test_drain_empty_queue(queue):
    result = queue.drain()
    assert result.drained_count == 0
    assert result.passed == 0
    assert result.failed == 0


def test_drain_counts_failures():
    q = AsyncVerificationQueue(verify_fn=lambda cap, out: False)
    q.submit(make_job())
    q.submit(make_job())
    result = q.drain()
    assert result.drained_count == 2
    assert result.passed == 0
    assert result.failed == 2


def test_pending_jobs_returns_ids(queue):
    job = make_job()
    queue.submit(job)
    assert job.job_id in queue.pending_jobs


def test_get_failed_results():
    q = AsyncVerificationQueue(verify_fn=lambda cap, out: False)
    q.submit(make_job())
    q.process_pending()
    failed = q.get_failed_results()
    assert len(failed) == 1


def test_multiple_jobs_ordered(queue):
    jobs = [make_job() for _ in range(3)]
    for j in jobs:
        queue.submit(j)
    results = queue.process_pending()
    assert len(results) == 3


# ── Rollback Integration Tests (Prompt 14) ──────────────────────

def test_rollback_on_failure():
    """Verification failure triggers rollback_action."""
    rolled_back = []

    def rollback():
        rolled_back.append(True)

    q = AsyncVerificationQueue(verify_fn=lambda cap, out: False)
    job = make_job(rollback_action=rollback)
    q.submit(job)
    q.process_pending()
    assert len(rolled_back) == 1


def test_no_rollback_on_success():
    """Verification success does NOT trigger rollback_action."""
    rolled_back = []

    def rollback():
        rolled_back.append(True)

    q = AsyncVerificationQueue(verify_fn=lambda cap, out: True)
    job = make_job(rollback_action=rollback)
    q.submit(job)
    q.process_pending()
    assert len(rolled_back) == 0


def test_has_failures():
    q = AsyncVerificationQueue(verify_fn=lambda cap, out: False)
    q.submit(make_job())
    q.process_pending()
    assert q.has_failures() is True


def test_clear_results(queue):
    queue.submit(make_job())
    queue.process_pending()
    assert len(queue.results) == 1
    queue.clear_results()
    assert len(queue.results) == 0
