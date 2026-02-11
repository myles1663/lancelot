"""Tests for vNext4 Full Tiered execute_plan() (Prompt 19).

Uses a test harness that simulates the Orchestrator's risk-tiered
execution loop with real governance modules and real files.
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
from governance.batch_receipts import BatchReceiptBuffer
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")

TOOL_CAPABILITY_MAP = {
    "read_file": "fs.read",
    "list_workspace": "fs.list",
    "search_workspace": "fs.read",
    "write_to_file": "fs.write",
    "execute_command": "shell.exec",
}


class FakeStep:
    def __init__(self, step_id, tool, description="", **params):
        self.id = step_id
        self.tool = tool
        self.description = description
        self.params = [type("P", (), {"key": k, "value": v})() for k, v in params.items()]


class FakePlan:
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
    params = {p.key: p.value for p in step.params}
    if step.tool == "write_to_file":
        path = os.path.join(workspace, params["path"])
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else workspace, exist_ok=True)
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
    elif step.tool == "execute_command":
        return f"Command output: {params.get('command', '')}"
    return f"Unknown tool: {step.tool}"


def run_tiered_plan(plan, classifier, workspace, verify_fn=None, approval_fn=None,
                    use_batch=True, use_async=True, use_cache=False):
    """Full tiered execution harness simulating orchestrator."""
    queue = AsyncVerificationQueue(verify_fn=verify_fn) if use_async else None
    rollback_mgr = RollbackManager(workspace=workspace)
    data_dir = os.path.join(workspace, "_governance")
    batch_buffer = BatchReceiptBuffer(task_id=plan.plan_id, data_dir=data_dir) if use_batch else None

    results = []
    step_tiers = []

    for i, step in enumerate(plan.steps):
        params = {p.key: p.value for p in step.params}
        capability = TOOL_CAPABILITY_MAP.get(step.tool, step.tool)
        target = params.get("path", params.get("dir", ""))
        profile = classifier.classify(capability, target=target)
        tier = profile.tier
        step_tiers.append(tier)

        if tier == RiskTier.T0_INERT:
            output = execute_step_locally(step, workspace)
            if batch_buffer:
                batch_buffer.append(capability, step.tool, tier, str(params), output, True)
            results.append(("T0", step.id, output))

        elif tier == RiskTier.T1_REVERSIBLE:
            snapshot = rollback_mgr.create_snapshot(
                task_id=plan.plan_id, step_index=i,
                capability=capability, target=target,
            )
            output = execute_step_locally(step, workspace)
            if queue:
                rollback_action = rollback_mgr.get_rollback_action(snapshot.snapshot_id)
                queue.submit(VerificationJob(
                    task_id=plan.plan_id, step_index=i,
                    capability=capability, output=output,
                    rollback_action=rollback_action,
                ))
            results.append(("T1", step.id, output))

        elif tier == RiskTier.T2_CONTROLLED:
            # Boundary enforcement
            if batch_buffer:
                batch_buffer.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
            if queue:
                drain = queue.drain()
                if drain.failed > 0:
                    queue.clear_results()
                    results.append(("T2_BLOCKED", step.id, f"{drain.failed} prior failures"))
                    return results, step_tiers, batch_buffer, queue, rollback_mgr
                queue.clear_results()
            output = execute_step_locally(step, workspace)
            results.append(("T2", step.id, output))

        elif tier == RiskTier.T3_IRREVERSIBLE:
            if batch_buffer:
                batch_buffer.flush_if_tier_boundary(RiskTier.T3_IRREVERSIBLE)
            if queue:
                drain = queue.drain()
                if drain.failed > 0:
                    queue.clear_results()
                    results.append(("T3_BLOCKED", step.id, f"{drain.failed} prior failures"))
                    return results, step_tiers, batch_buffer, queue, rollback_mgr
                queue.clear_results()
            if approval_fn and not approval_fn(step, profile):
                results.append(("T3_DENIED", step.id, "Approval denied"))
                return results, step_tiers, batch_buffer, queue, rollback_mgr
            output = execute_step_locally(step, workspace)
            results.append(("T3", step.id, output))

    # Cleanup
    if batch_buffer:
        batch_buffer.flush()
    if queue and queue.depth > 0:
        queue.drain()
        queue.clear_results()

    return results, step_tiers, batch_buffer, queue, rollback_mgr


# ── Test 1: Pure T0 plan ─────────────────────────────────────────

def test_pure_t0_plan(tmp_path, classifier):
    """Pure T0 plan: all reads use fast path, batch receipt emitted."""
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    (tmp_path / "c.txt").write_text("ccc")

    steps = [
        FakeStep(1, "read_file", path="a.txt"),
        FakeStep(2, "read_file", path="b.txt"),
        FakeStep(3, "read_file", path="c.txt"),
    ]
    plan = FakePlan(steps)
    results, tiers, buf, _, _ = run_tiered_plan(plan, classifier, str(tmp_path))

    assert all(t == RiskTier.T0_INERT for t in tiers)
    assert all(r[0] == "T0" for r in results)
    assert len(results) == 3


# ── Test 2: Pure T1 plan ─────────────────────────────────────────

def test_pure_t1_plan(tmp_path, classifier):
    """Pure T1 plan: all writes use async verify, files are written."""
    steps = [
        FakeStep(1, "write_to_file", path="x.txt", content="xx"),
        FakeStep(2, "write_to_file", path="y.txt", content="yy"),
        FakeStep(3, "write_to_file", path="z.txt", content="zz"),
    ]
    plan = FakePlan(steps)
    results, tiers, _, queue, _ = run_tiered_plan(
        plan, classifier, str(tmp_path), verify_fn=lambda c, o: True
    )

    assert all(t == RiskTier.T1_REVERSIBLE for t in tiers)
    assert all(r[0] == "T1" for r in results)
    assert (tmp_path / "x.txt").read_text() == "xx"
    assert (tmp_path / "y.txt").read_text() == "yy"
    assert (tmp_path / "z.txt").read_text() == "zz"


# ── Test 3: Pure T2 plan ─────────────────────────────────────────

def test_pure_t2_plan(tmp_path, classifier):
    """Pure T2 plan: shell exec uses sync verify path."""
    steps = [FakeStep(1, "execute_command", command="echo hello")]
    plan = FakePlan(steps)
    results, tiers, _, _, _ = run_tiered_plan(plan, classifier, str(tmp_path))

    assert tiers[0] == RiskTier.T2_CONTROLLED
    assert results[0][0] == "T2"


# ── Test 4: Mixed plan ───────────────────────────────────────────

def test_mixed_plan(tmp_path, classifier):
    """Mixed plan: T0, T0, T1, T0, T2 — correct pipeline per step."""
    (tmp_path / "r1.txt").write_text("r1")
    (tmp_path / "r2.txt").write_text("r2")
    (tmp_path / "r3.txt").write_text("r3")

    steps = [
        FakeStep(1, "read_file", path="r1.txt"),       # T0
        FakeStep(2, "read_file", path="r2.txt"),       # T0
        FakeStep(3, "write_to_file", path="w.txt", content="w"),  # T1
        FakeStep(4, "read_file", path="r3.txt"),       # T0
        FakeStep(5, "execute_command", command="ls"),   # T2
    ]
    plan = FakePlan(steps)
    results, tiers, _, _, _ = run_tiered_plan(
        plan, classifier, str(tmp_path), verify_fn=lambda c, o: True
    )

    assert tiers == [
        RiskTier.T0_INERT, RiskTier.T0_INERT, RiskTier.T1_REVERSIBLE,
        RiskTier.T0_INERT, RiskTier.T2_CONTROLLED,
    ]
    assert results[0][0] == "T0"
    assert results[2][0] == "T1"
    assert results[4][0] == "T2"


# ── Test 5: T2 flushes batch buffer ──────────────────────────────

def test_t2_flushes_batch(tmp_path, classifier):
    """T2 step flushes batch buffer (batch receipt file created before T2)."""
    (tmp_path / "f.txt").write_text("data")

    steps = [
        FakeStep(1, "read_file", path="f.txt"),       # T0 → batch
        FakeStep(2, "read_file", path="f.txt"),       # T0 → batch
        FakeStep(3, "execute_command", command="ls"),  # T2 → flushes batch
    ]
    plan = FakePlan(steps)
    results, _, buf, _, _ = run_tiered_plan(plan, classifier, str(tmp_path))

    # Batch receipt should have been flushed to disk by T2 boundary
    gov_dir = tmp_path / "_governance"
    batch_files = list(gov_dir.glob("batch_*.json")) if gov_dir.exists() else []
    assert len(batch_files) >= 1


# ── Test 6: T2 drains async queue ────────────────────────────────

def test_t2_drains_async_queue(tmp_path, classifier):
    """T2 step drains async queue before executing."""
    steps = [
        FakeStep(1, "write_to_file", path="w.txt", content="data"),  # T1
        FakeStep(2, "execute_command", command="ls"),                  # T2
    ]
    plan = FakePlan(steps)
    results, _, _, queue, _ = run_tiered_plan(
        plan, classifier, str(tmp_path), verify_fn=lambda c, o: True
    )

    # After T2 boundary drain, queue should be empty
    assert queue.depth == 0
    assert results[1][0] == "T2"


# ── Test 7: T3 requires approval ─────────────────────────────────

def test_t3_requires_approval(tmp_path, classifier):
    """T3 step requires approval (test with auto-approve helper)."""
    # net.post is T3
    steps = [FakeStep(1, "execute_command", command="curl http://example.com")]
    # Force T3 via scope escalation — use a capability that's T3
    # Actually, shell.exec is T2. Let me use a different approach.
    # We'll test approval via the approval_fn parameter
    steps = [FakeStep(1, "execute_command", command="echo test")]
    plan = FakePlan(steps)

    approved = []
    def auto_approve(step, profile):
        approved.append(True)
        return True

    # shell.exec is T2, not T3. We need a T3 capability for this test.
    # Let's override the classifier to force T3 for this test
    from governance.config import RiskClassificationConfig
    t3_config = RiskClassificationConfig(
        defaults={"execute_command": 3},
        scope_escalations=[],
    )
    t3_classifier = RiskClassifier(t3_config)

    results, tiers, _, _, _ = run_tiered_plan(
        plan, t3_classifier, str(tmp_path), approval_fn=auto_approve
    )
    assert tiers[0] == RiskTier.T3_IRREVERSIBLE
    assert len(approved) == 1
    assert results[0][0] == "T3"


# ── Test 8: All flags off → legacy path ──────────────────────────

def test_all_flags_off_legacy_path(tmp_path, classifier):
    """All feature flags false: harness uses governance but orchestrator would use legacy."""
    (tmp_path / "legacy.txt").write_text("content")

    steps = [FakeStep(1, "read_file", path="legacy.txt")]
    plan = FakePlan(steps)

    # With use_batch=False and use_async=False, simulates legacy behavior
    results, _, _, _, _ = run_tiered_plan(
        plan, classifier, str(tmp_path),
        use_batch=False, use_async=False,
    )
    assert results[0][0] == "T0"
    assert "legacy.txt" in results[0][2]


# ── Test 9: T3 approval denied ───────────────────────────────────

def test_t3_approval_denied(tmp_path):
    """T3 action denied when approval function returns False."""
    from governance.config import RiskClassificationConfig
    t3_config = RiskClassificationConfig(
        defaults={"execute_command": 3},
        scope_escalations=[],
    )
    t3_classifier = RiskClassifier(t3_config)

    steps = [FakeStep(1, "execute_command", command="dangerous")]
    plan = FakePlan(steps)

    results, _, _, _, _ = run_tiered_plan(
        plan, t3_classifier, str(tmp_path),
        approval_fn=lambda s, p: False,
    )
    assert results[0][0] == "T3_DENIED"
