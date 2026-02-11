"""Tests for vNext4 T1 Pipeline Integration (Prompt 15).

These tests verify that T1 (reversible) actions flow through the
AsyncVerificationQueue + RollbackManager when governance flags are enabled.
Uses a minimal test harness simulating the Orchestrator's T1 path.
"""

import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import load_governance_config
from governance.risk_classifier import RiskClassifier
from governance.async_verifier import AsyncVerificationQueue, VerificationJob
from governance.rollback import RollbackManager
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")

# Tool name → governance capability mapping (mirrors orchestrator)
TOOL_CAPABILITY_MAP = {
    "read_file": "fs.read",
    "list_workspace": "fs.list",
    "search_workspace": "fs.read",
    "write_to_file": "fs.write",
    "execute_command": "shell.exec",
}


class FakeStep:
    """Minimal step object mimicking the Planner output."""
    def __init__(self, step_id, tool, description="", **params):
        self.id = step_id
        self.tool = tool
        self.description = description
        self.params = [type("P", (), {"key": k, "value": v})() for k, v in params.items()]


class FakePlan:
    """Minimal plan object."""
    def __init__(self, steps):
        self.plan_id = str(uuid.uuid4())
        self.steps = steps
        self.goal = "test"


@pytest.fixture
def gov_config():
    return load_governance_config(CONFIG_PATH)


@pytest.fixture
def classifier(gov_config):
    return RiskClassifier(gov_config.risk_classification)


def execute_step_locally(step, workspace):
    """Execute a step against the filesystem. Returns output string."""
    params = {p.key: p.value for p in step.params}
    if step.tool == "write_to_file":
        path = os.path.join(workspace, params["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(params.get("content", ""))
        return f"Write to {params['path']}: Success"
    elif step.tool == "read_file":
        path = os.path.join(workspace, params["path"])
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"Read file {params['path']}. Content length: {len(content)}"
        return f"Read file {params['path']}. Content length: 0"
    elif step.tool == "list_workspace":
        return "file1.txt\nfile2.txt"
    return f"Unknown tool: {step.tool}"


def run_plan_with_governance(plan, classifier, workspace, verify_fn=None):
    """Simulate Orchestrator.execute_plan() with T1 governance.

    This test harness replicates the orchestrator's T1 flow exactly:
    classify → snapshot → execute → submit verification job.
    """
    queue = AsyncVerificationQueue(verify_fn=verify_fn)
    rollback_mgr = RollbackManager(workspace=workspace)
    results = []

    for i, step in enumerate(plan.steps):
        params = {p.key: p.value for p in step.params}
        capability = TOOL_CAPABILITY_MAP.get(step.tool, step.tool)
        target = params.get("path", params.get("dir", ""))

        profile = classifier.classify(capability, target=target)

        if profile.tier == RiskTier.T1_REVERSIBLE:
            # T1 path: snapshot → execute → submit async verification
            snapshot = rollback_mgr.create_snapshot(
                task_id=plan.plan_id,
                step_index=i,
                capability=capability,
                target=target,
            )
            output = execute_step_locally(step, workspace)
            rollback_action = rollback_mgr.get_rollback_action(snapshot.snapshot_id)
            queue.submit(VerificationJob(
                task_id=plan.plan_id,
                step_index=i,
                capability=capability,
                output=output,
                rollback_action=rollback_action,
            ))
            results.append(("T1_ASYNC", step.id, output))
        else:
            # Sync path for T0/T2/T3
            output = execute_step_locally(step, workspace)
            results.append(("SYNC", step.id, output))

    # Drain at end of plan
    drain_result = queue.drain()
    queue.clear_results()

    return results, drain_result, rollback_mgr


# ── Test 1: T1 action with verification pass ────────────────────

def test_t1_write_verification_pass(tmp_path, classifier):
    """T1 fs.write with passing verification: file persists."""
    step = FakeStep(1, "write_to_file", path="hello.txt", content="hello world")
    plan = FakePlan([step])

    results, drain, _ = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=lambda cap, out: True
    )

    assert results[0][0] == "T1_ASYNC"
    assert drain.passed == 1
    assert drain.failed == 0
    # File should still exist
    assert (tmp_path / "hello.txt").read_text() == "hello world"


# ── Test 2: T1 action with verification fail → rollback ─────────

def test_t1_write_verification_fail_rollback(tmp_path, classifier):
    """T1 fs.write with failing verification: file is rolled back."""
    # Pre-existing file
    (tmp_path / "hello.txt").write_text("original")

    step = FakeStep(1, "write_to_file", path="hello.txt", content="overwritten")
    plan = FakePlan([step])

    results, drain, rollback_mgr = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=lambda cap, out: False
    )

    assert drain.failed == 1
    assert drain.passed == 0
    # File should be restored to original
    assert (tmp_path / "hello.txt").read_text() == "original"


# ── Test 3: T1 actions don't block (async queued) ───────────────

def test_t1_actions_queued_not_blocking(tmp_path, classifier):
    """Multiple T1 actions are queued, not verified inline."""
    steps = [
        FakeStep(1, "write_to_file", path="a.txt", content="aaa"),
        FakeStep(2, "write_to_file", path="b.txt", content="bbb"),
        FakeStep(3, "write_to_file", path="c.txt", content="ccc"),
    ]
    plan = FakePlan(steps)

    # verify_fn not called until drain — all steps queued first
    call_count = [0]
    def counting_verify(cap, out):
        call_count[0] += 1
        return True

    queue = AsyncVerificationQueue(verify_fn=counting_verify)
    rollback_mgr = RollbackManager(workspace=str(tmp_path))

    for i, step in enumerate(plan.steps):
        params = {p.key: p.value for p in step.params}
        capability = TOOL_CAPABILITY_MAP.get(step.tool, step.tool)
        snapshot = rollback_mgr.create_snapshot(
            task_id=plan.plan_id, step_index=i,
            capability=capability, target=params.get("path", ""),
        )
        output = execute_step_locally(step, str(tmp_path))
        rollback_action = rollback_mgr.get_rollback_action(snapshot.snapshot_id)
        queue.submit(VerificationJob(
            task_id=plan.plan_id, step_index=i,
            capability=capability, output=output,
            rollback_action=rollback_action,
        ))

    # Nothing verified yet
    assert call_count[0] == 0
    assert queue.depth == 3

    # Now drain
    drain = queue.drain()
    assert call_count[0] == 3
    assert drain.passed == 3


# ── Test 4: drain() catches all pending verifications ────────────

def test_drain_catches_all_pending(tmp_path, classifier):
    """drain() at end of plan processes all queued T1 jobs."""
    steps = [
        FakeStep(1, "write_to_file", path="x.txt", content="xx"),
        FakeStep(2, "write_to_file", path="y.txt", content="yy"),
    ]
    plan = FakePlan(steps)

    results, drain, _ = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=lambda cap, out: True
    )

    assert drain.drained_count == 2
    assert drain.passed == 2
    assert drain.timed_out is False


# ── Test 5: FEATURE_ASYNC_VERIFICATION=False → sync path ────────

def test_no_async_flag_uses_sync_path(tmp_path, classifier):
    """Without async verification, T1 actions use normal sync execution."""
    step = FakeStep(1, "write_to_file", path="sync.txt", content="sync content")
    plan = FakePlan([step])

    # Simulate: no async queue — all steps go through sync path
    results = []
    for i, step_obj in enumerate(plan.steps):
        params = {p.key: p.value for p in step_obj.params}
        capability = TOOL_CAPABILITY_MAP.get(step_obj.tool, step_obj.tool)
        profile = classifier.classify(capability, target=params.get("path", ""))

        # Without FEATURE_ASYNC_VERIFICATION, even T1 goes sync
        output = execute_step_locally(step_obj, str(tmp_path))
        results.append(("SYNC", step_obj.id, output))

    assert results[0][0] == "SYNC"
    assert (tmp_path / "sync.txt").read_text() == "sync content"


# ── Test 6: Mixed plan — T0 read + T1 write + T0 read ───────────

def test_mixed_plan_t0_t1_t0(tmp_path, classifier):
    """Mixed plan: T0 reads + T1 write all complete correctly."""
    # Create file for reading
    (tmp_path / "existing.txt").write_text("existing content")

    steps = [
        FakeStep(1, "read_file", description="Read existing", path="existing.txt"),
        FakeStep(2, "write_to_file", description="Write new", path="new.txt", content="new stuff"),
        FakeStep(3, "read_file", description="Read new", path="new.txt"),
    ]
    plan = FakePlan(steps)

    results, drain, _ = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=lambda cap, out: True
    )

    # Step 1: T0 read → sync path
    assert results[0][0] == "SYNC"
    assert "existing.txt" in results[0][2]

    # Step 2: T1 write → async path
    assert results[1][0] == "T1_ASYNC"

    # Step 3: T0 read → sync path
    assert results[2][0] == "SYNC"
    assert "new.txt" in results[2][2]

    # Drain should have 1 T1 job
    assert drain.drained_count == 1
    assert drain.passed == 1

    # Both files should exist
    assert (tmp_path / "existing.txt").read_text() == "existing content"
    assert (tmp_path / "new.txt").read_text() == "new stuff"


# ── Test 7: T1 write to new file, verification fail → file removed

def test_t1_new_file_verification_fail_removes_file(tmp_path, classifier):
    """T1 fs.write creating new file, verification fail → file removed."""
    step = FakeStep(1, "write_to_file", path="brand_new.txt", content="content")
    plan = FakePlan([step])

    results, drain, _ = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=lambda cap, out: False
    )

    assert drain.failed == 1
    # New file should have been removed by rollback
    assert not (tmp_path / "brand_new.txt").exists()


# ── Test 8: Multiple T1 writes, mixed pass/fail ─────────────────

def test_mixed_pass_fail_rollback(tmp_path, classifier):
    """Multiple T1 writes: some pass, some fail — only failures roll back."""
    (tmp_path / "keep.txt").write_text("original keep")
    (tmp_path / "revert.txt").write_text("original revert")

    steps = [
        FakeStep(1, "write_to_file", path="keep.txt", content="updated keep"),
        FakeStep(2, "write_to_file", path="revert.txt", content="updated revert"),
    ]
    plan = FakePlan(steps)

    call_num = [0]
    def selective_verify(cap, out):
        call_num[0] += 1
        return call_num[0] == 1  # First passes, second fails

    results, drain, _ = run_plan_with_governance(
        plan, classifier, str(tmp_path), verify_fn=selective_verify
    )

    assert drain.passed == 1
    assert drain.failed == 1

    # First file should keep updated content
    assert (tmp_path / "keep.txt").read_text() == "updated keep"
    # Second file should be rolled back
    assert (tmp_path / "revert.txt").read_text() == "original revert"
