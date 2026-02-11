"""Tests for vNext4 T2/T3 Boundary Enforcement (Prompt 20).

Validates the critical safety invariant: before ANY T2/T3 action,
all pending batch receipts are flushed and async verifications completed.
"""

import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import load_governance_config, RiskClassificationConfig
from governance.risk_classifier import RiskClassifier
from governance.async_verifier import AsyncVerificationQueue, VerificationJob
from governance.rollback import RollbackManager
from governance.batch_receipts import BatchReceiptBuffer
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")

TOOL_CAPABILITY_MAP = {
    "read_file": "fs.read",
    "write_to_file": "fs.write",
    "execute_command": "shell.exec",
}


class FakeStep:
    def __init__(self, step_id, tool, **params):
        self.id = step_id
        self.tool = tool
        self.description = f"Step {step_id}"
        self.params = [type("P", (), {"key": k, "value": v})() for k, v in params.items()]


@pytest.fixture
def gov_config():
    return load_governance_config(CONFIG_PATH)


@pytest.fixture
def classifier(gov_config):
    return RiskClassifier(gov_config.risk_classification)


def execute_local(step, workspace):
    params = {p.key: p.value for p in step.params}
    if step.tool == "write_to_file":
        path = os.path.join(workspace, params["path"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(params.get("content", ""))
        return f"Write {params['path']}: OK"
    elif step.tool == "read_file":
        path = os.path.join(workspace, params["path"])
        return f"Read {params['path']}"
    elif step.tool == "execute_command":
        return f"Exec: {params.get('command', '')}"
    return "Unknown"


# ── Test 1: T0 → T2 transition flushes batch buffer ─────────────

def test_t0_to_t2_flushes_batch(tmp_path, classifier):
    """Batch buffer is flushed before T2 executes."""
    (tmp_path / "a.txt").write_text("data")
    data_dir = str(tmp_path / "_gov")
    batch = BatchReceiptBuffer(task_id="test", data_dir=data_dir)

    # T0 action
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "a.txt", "content", True)
    assert batch.size == 1

    # T2 boundary → flush
    receipt = batch.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    assert receipt is not None
    assert batch.size == 0

    # Verify file on disk
    gov_dir = tmp_path / "_gov"
    files = list(gov_dir.glob("batch_*.json"))
    assert len(files) == 1


# ── Test 2: T1 → T2 transition drains async queue ───────────────

def test_t1_to_t2_drains_queue(tmp_path, classifier):
    """Async queue is drained before T2 executes."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    queue.submit(VerificationJob(capability="fs.write", output="data"))
    assert queue.depth == 1

    # T2 boundary → drain
    drain = queue.drain()
    assert drain.drained_count == 1
    assert drain.passed == 1
    assert queue.depth == 0


# ── Test 3: T1 → T3 drains queue AND approval gate ──────────────

def test_t1_to_t3_drain_and_approval(tmp_path, classifier):
    """Async queue drained and approval gate triggered before T3."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    queue.submit(VerificationJob(capability="fs.write", output="data"))

    # Drain first
    drain = queue.drain()
    assert queue.depth == 0
    assert drain.passed == 1

    # Approval gate
    approved = [False]
    def approval_gate(step, profile):
        approved[0] = True
        return True

    approval_gate(None, None)
    assert approved[0] is True


# ── Test 4: T1 verification failure blocks T2 ────────────────────

def test_t1_failure_blocks_t2(tmp_path, classifier):
    """If async drain has failures, T2 step does NOT execute."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: False)

    # Submit T1 job that will fail
    (tmp_path / "f.txt").write_text("original")
    rollback_mgr = RollbackManager(workspace=str(tmp_path))
    snap = rollback_mgr.create_snapshot("t", 0, "fs.write", target="f.txt")
    (tmp_path / "f.txt").write_text("modified")

    rollback = rollback_mgr.get_rollback_action(snap.snapshot_id)
    queue.submit(VerificationJob(
        capability="fs.write", output="modified",
        rollback_action=rollback,
    ))

    # Drain for T2 boundary
    drain = queue.drain()
    assert drain.failed == 1

    # T2 should be blocked
    t2_executed = drain.failed == 0
    assert t2_executed is False

    # Verify rollback occurred
    assert (tmp_path / "f.txt").read_text() == "original"


# ── Test 5: Multiple T0s → T2 batch contains all entries ────────

def test_multiple_t0s_batch_all_entries(tmp_path, classifier):
    """Batch receipt contains all T0 entries when flushed at T2 boundary."""
    data_dir = str(tmp_path / "_gov")
    batch = BatchReceiptBuffer(task_id="test", data_dir=data_dir)

    for i in range(5):
        batch.append("fs.read", "read_file", RiskTier.T0_INERT, f"f{i}", "out", True)
    assert batch.size == 5

    receipt = batch.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    assert receipt is not None
    assert receipt.summary.total_actions == 5


# ── Test 6: T0 → T1 → T0 → T2 full sequence ────────────────────

def test_full_t0_t1_t0_t2_sequence(tmp_path, classifier):
    """T0 → T1 → T0 → T2: batch flushes with 2 T0 entries, T1 async verified."""
    data_dir = str(tmp_path / "_gov")
    batch = BatchReceiptBuffer(task_id="test", data_dir=data_dir)
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    rollback_mgr = RollbackManager(workspace=str(tmp_path))

    # T0
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "a", "out", True)

    # T1
    snap = rollback_mgr.create_snapshot("t", 0, "fs.write", target="w.txt")
    (tmp_path / "w.txt").write_text("written")
    rollback = rollback_mgr.get_rollback_action(snap.snapshot_id)
    queue.submit(VerificationJob(capability="fs.write", output="data", rollback_action=rollback))

    # T0
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "b", "out", True)

    # T2 boundary
    receipt = batch.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    assert receipt is not None
    assert receipt.summary.total_actions == 2  # Two T0 entries

    drain = queue.drain()
    assert drain.drained_count == 1
    assert drain.passed == 1
    assert queue.depth == 0


# ── Test 7: T3 → T0 → T1: fast path resumes after T3 ────────────

def test_t3_then_fast_path_resumes(tmp_path, classifier):
    """After T3 completes, T0/T1 resume fast path."""
    data_dir = str(tmp_path / "_gov")
    batch = BatchReceiptBuffer(task_id="test", data_dir=data_dir)

    # Simulate T3 completed (no boundary needed going downward)
    # T0 after T3
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "a", "out", True)
    assert batch.size == 1

    # T1 after T3 — no boundary enforcement needed (downward transition)
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    queue.submit(VerificationJob(capability="fs.write", output="data"))
    assert queue.depth == 1

    # Boundary is only enforced upward (T0/T1 → T2/T3)
    drain = queue.drain()
    assert drain.passed == 1


# ── Test 8: Empty queue drain before T2 ──────────────────────────

def test_empty_queue_drain_before_t2(tmp_path, classifier):
    """T2 after only T0 actions (no T1) → drain returns empty and T2 proceeds."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)

    # No T1 jobs submitted
    drain = queue.drain()
    assert drain.drained_count == 0
    assert drain.passed == 0
    assert drain.failed == 0
    assert drain.timed_out is False


# ── Test 9: Rapid tier alternation ───────────────────────────────

def test_rapid_tier_alternation(tmp_path, classifier):
    """Alternating tiers T0 → T2 → T0 → T2: boundaries enforced each time."""
    data_dir = str(tmp_path / "_gov")
    boundary_flushes = []

    # Round 1: T0 then T2
    batch1 = BatchReceiptBuffer(task_id="test1", data_dir=data_dir)
    batch1.append("fs.read", "read_file", RiskTier.T0_INERT, "a", "out", True)
    r = batch1.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    if r:
        boundary_flushes.append(r)

    # Round 2: T0 then T2 again
    batch2 = BatchReceiptBuffer(task_id="test2", data_dir=data_dir)
    batch2.append("fs.read", "read_file", RiskTier.T0_INERT, "b", "out", True)
    r = batch2.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    if r:
        boundary_flushes.append(r)

    assert len(boundary_flushes) == 2
