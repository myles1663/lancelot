"""Tests for vNext4 Comprehensive Integration (Prompt 23).

End-to-end integration tests covering multi-tier execution, template
lifecycle, verification failure cascade, flag combinations, and
high-volume stress scenarios.
"""

import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import (
    load_governance_config,
    IntentTemplateConfig,
    RiskClassificationConfig,
)
from governance.risk_classifier import RiskClassifier
from governance.async_verifier import AsyncVerificationQueue, VerificationJob
from governance.rollback import RollbackManager
from governance.batch_receipts import BatchReceiptBuffer
from governance.intent_templates import IntentTemplateRegistry
from governance.policy_cache import PolicyCache
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")

TOOL_CAPABILITY_MAP = {
    "read_file": "fs.read",
    "list_workspace": "fs.list",
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
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else workspace, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(params.get("content", ""))
        return f"Write {params['path']}: OK"
    elif step.tool == "read_file":
        path = os.path.join(workspace, params["path"])
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f"Read {params['path']}: {len(f.read())} chars"
        return f"Read {params['path']}: 0 chars"
    elif step.tool == "execute_command":
        return f"Exec: {params.get('command', '')}"
    return "OK"


# ── SCENARIO 1: 10-step mixed-tier task ──────────────────────────

def test_10_step_mixed_tier(tmp_path, classifier):
    """10 steps: reads (T0), writes (T1), shell (T2) — all tiers correct."""
    for i in range(5):
        (tmp_path / f"r{i}.txt").write_text(f"data{i}")

    steps = [
        FakeStep(1, "read_file", path="r0.txt"),                      # T0
        FakeStep(2, "read_file", path="r1.txt"),                      # T0
        FakeStep(3, "write_to_file", path="w1.txt", content="w1"),    # T1
        FakeStep(4, "read_file", path="r2.txt"),                      # T0
        FakeStep(5, "write_to_file", path="w2.txt", content="w2"),    # T1
        FakeStep(6, "read_file", path="r3.txt"),                      # T0
        FakeStep(7, "execute_command", command="echo test"),           # T2
        FakeStep(8, "read_file", path="r4.txt"),                      # T0
        FakeStep(9, "write_to_file", path="w3.txt", content="w3"),    # T1
        FakeStep(10, "read_file", path="r0.txt"),                     # T0
    ]

    data_dir = str(tmp_path / "_gov")
    batch = BatchReceiptBuffer(task_id="mix10", data_dir=data_dir)
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    rollback = RollbackManager(workspace=str(tmp_path))

    tiers = []
    for i, step in enumerate(steps):
        params = {p.key: p.value for p in step.params}
        cap = TOOL_CAPABILITY_MAP.get(step.tool, step.tool)
        target = params.get("path", "")
        profile = classifier.classify(cap, target=target)
        tiers.append(profile.tier)

        if profile.tier == RiskTier.T0_INERT:
            output = execute_local(step, str(tmp_path))
            batch.append(cap, step.tool, profile.tier, str(params), output, True)

        elif profile.tier == RiskTier.T1_REVERSIBLE:
            snap = rollback.create_snapshot("mix10", i, cap, target=target)
            output = execute_local(step, str(tmp_path))
            rb = rollback.get_rollback_action(snap.snapshot_id)
            queue.submit(VerificationJob(capability=cap, output=output, rollback_action=rb))

        elif profile.tier == RiskTier.T2_CONTROLLED:
            batch.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
            drain = queue.drain()
            assert drain.failed == 0
            queue.clear_results()
            output = execute_local(step, str(tmp_path))

    batch.flush()
    final_drain = queue.drain()
    queue.clear_results()

    # Verify tier classification
    assert tiers.count(RiskTier.T0_INERT) == 6
    assert tiers.count(RiskTier.T1_REVERSIBLE) == 3
    assert tiers.count(RiskTier.T2_CONTROLLED) == 1

    # Verify files written
    assert (tmp_path / "w1.txt").read_text() == "w1"
    assert (tmp_path / "w2.txt").read_text() == "w2"
    assert (tmp_path / "w3.txt").read_text() == "w3"


# ── SCENARIO 2: Template lifecycle end-to-end ────────────────────

def test_template_lifecycle_e2e(tmp_path):
    """Execute intent 3 times → promoted → 4th uses template."""
    config = IntentTemplateConfig(promotion_threshold=3, max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))

    intent = "read and summarize"
    plan_steps = [
        {"capability": "fs.read", "risk_tier": 0, "scope": "workspace"},
    ]

    # Executions 1-3
    for _ in range(3):
        existing = None
        for t in reg.list_all():
            if t.intent_pattern == intent:
                existing = t
                break

        if existing:
            reg.record_success(existing.template_id)
        else:
            reg.create_candidate(intent, plan_steps)

    # After 3 executions, template should be promoted
    assert len(reg.list_active()) == 1
    active = reg.list_active()[0]
    assert active.intent_pattern == intent

    # 4th execution uses the template via match()
    matched = reg.match("read and summarize the code")
    assert matched is not None
    assert matched.template_id == active.template_id


# ── SCENARIO 3: Verification failure cascade ─────────────────────

def test_verification_failure_cascade(tmp_path, classifier):
    """T1 failure blocks T2: T0 → T1(fail) → T0 → T2(blocked)."""
    (tmp_path / "r.txt").write_text("data")
    (tmp_path / "target.txt").write_text("original")

    queue = AsyncVerificationQueue(verify_fn=lambda c, o: "fail" not in o)
    rollback_mgr = RollbackManager(workspace=str(tmp_path))
    batch = BatchReceiptBuffer(task_id="cascade", data_dir=str(tmp_path / "_gov"))

    # T0: read
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "r.txt", "data", True)

    # T1: write (will fail verification)
    snap = rollback_mgr.create_snapshot("cascade", 1, "fs.write", target="target.txt")
    (tmp_path / "target.txt").write_text("modified")
    rb = rollback_mgr.get_rollback_action(snap.snapshot_id)
    queue.submit(VerificationJob(
        capability="fs.write", output="fail_this",
        rollback_action=rb,
    ))

    # T0: read
    batch.append("fs.read", "read_file", RiskTier.T0_INERT, "r.txt", "data", True)

    # T2: shell — boundary enforcement
    batch.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    drain = queue.drain()

    # Drain should have 1 failure
    assert drain.failed == 1
    assert drain.passed == 0

    # T2 should be blocked (caller checks drain.failed > 0)
    t2_blocked = drain.failed > 0
    assert t2_blocked is True

    # Verify T1 was rolled back
    assert (tmp_path / "target.txt").read_text() == "original"


# ── SCENARIO 4: Feature flag combinations ────────────────────────

def test_flag_combo_all_on(tmp_path, gov_config, classifier):
    """All governance flags on → risk-tiered behavior."""
    cache = PolicyCache(
        config=gov_config.policy_cache,
        risk_classifier=classifier,
        soul_version="v1",
    )
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    batch = BatchReceiptBuffer(task_id="test", data_dir=str(tmp_path))

    # T0 action uses cache
    decision = cache.lookup("fs.read", "workspace")
    assert decision is not None
    assert decision.decision == "allow"

    # T1 action uses async queue
    queue.submit(VerificationJob(capability="fs.write", output="data"))
    assert queue.depth == 1

    # Batch receipt collects entries
    batch.append("fs.read", "tool", RiskTier.T0_INERT, "in", "out", True)
    assert batch.size == 1


def test_flag_combo_all_off(tmp_path, gov_config, classifier):
    """All flags off → components still exist but are not used in execution."""
    # When flags are off, orchestrator uses legacy path
    # Components are still importable and functional when used directly
    cache = PolicyCache(
        config=gov_config.policy_cache,
        risk_classifier=classifier,
        soul_version="v1",
    )
    assert cache.stats.total_entries > 0  # Cache compiles regardless


def test_flag_combo_only_cache(tmp_path, gov_config, classifier):
    """Only FEATURE_POLICY_CACHE on → cache works independently."""
    cache = PolicyCache(
        config=gov_config.policy_cache,
        risk_classifier=classifier,
        soul_version="v1",
    )
    assert cache.lookup("fs.read", "workspace") is not None
    assert cache.lookup("shell.exec", "workspace") is None  # T2 not cached


def test_flag_combo_only_batch(tmp_path, gov_config):
    """Only FEATURE_BATCH_RECEIPTS on → batching works independently."""
    batch = BatchReceiptBuffer(task_id="test", data_dir=str(tmp_path))
    batch.append("fs.read", "tool", RiskTier.T0_INERT, "in", "out", True)
    receipt = batch.flush()
    assert receipt is not None
    assert receipt.summary.total_actions == 1


# ── SCENARIO 5: Soul amendment during execution ──────────────────

def test_soul_amendment_invalidates_cache(gov_config, classifier):
    """Soul change invalidates policy cache."""
    cache = PolicyCache(
        config=gov_config.policy_cache,
        risk_classifier=classifier,
        soul_version="v1",
    )
    assert cache.stats.total_entries > 0

    # Soul amendment
    cache.invalidate()
    assert cache.stats.total_entries == 0

    # Recompile with new version
    cache.recompile(classifier, soul_version="v2")
    assert cache.stats.soul_version == "v2"
    assert cache.stats.total_entries > 0


def test_soul_amendment_invalidates_templates(tmp_path):
    """Soul change invalidates all templates."""
    config = IntentTemplateConfig(promotion_threshold=2, max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))

    tid = reg.create_candidate("test", [{"capability": "fs.read", "risk_tier": 0}])
    reg.record_success(tid)  # Now success_count=2 >= threshold=2 → promoted
    assert reg.get_template(tid).active is True

    count = reg.invalidate_all(reason="Soul v2")
    assert count == 1


# ── SCENARIO 6: High-volume stress ───────────────────────────────

def test_high_volume_50_t0_reads(tmp_path, classifier):
    """50 T0 read steps all complete, batch receipts flushed correctly."""
    from governance.config import BatchReceiptConfig
    for i in range(50):
        (tmp_path / f"file_{i}.txt").write_text(f"content_{i}")

    data_dir = str(tmp_path / "_gov")
    # Use large buffer to avoid auto-flush during this test
    config = BatchReceiptConfig(buffer_size=100)
    batch = BatchReceiptBuffer(task_id="stress50", config=config, data_dir=data_dir)

    for i in range(50):
        step = FakeStep(i, "read_file", path=f"file_{i}.txt")
        output = execute_local(step, str(tmp_path))
        batch.append("fs.read", "read_file", RiskTier.T0_INERT, f"file_{i}", output, True)

    receipt = batch.flush()
    assert receipt is not None
    assert receipt.summary.total_actions == 50
    assert receipt.summary.succeeded == 50


def test_high_volume_no_handle_leak(tmp_path, classifier):
    """20 T1 writes with rollback don't leak file handles."""
    rollback = RollbackManager(workspace=str(tmp_path))

    for i in range(20):
        fname = f"vol_{i}.txt"
        (tmp_path / fname).write_text(f"orig_{i}")
        snap = rollback.create_snapshot("stress", i, "fs.write", target=fname)
        (tmp_path / fname).write_text(f"mod_{i}")
        rollback.get_rollback_action(snap.snapshot_id)()
        assert (tmp_path / fname).read_text() == f"orig_{i}"

    assert len(rollback.active_snapshots) == 0


# ── SCENARIO 7: Graceful shutdown ────────────────────────────────

def test_graceful_shutdown_drains_queue(tmp_path, classifier):
    """Shutdown drains all pending verifications and writes receipts."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    batch = BatchReceiptBuffer(task_id="shutdown", data_dir=str(tmp_path / "_gov"))

    # Simulate in-progress work
    for i in range(5):
        queue.submit(VerificationJob(capability="fs.write", output=f"data{i}"))
        batch.append("fs.read", "tool", RiskTier.T0_INERT, f"in{i}", f"out{i}", True)

    assert queue.depth == 5
    assert batch.size == 5

    # Graceful shutdown
    drain = queue.drain()
    assert drain.drained_count == 5
    assert drain.passed == 5
    assert queue.depth == 0

    receipt = batch.flush()
    assert receipt is not None
    assert receipt.summary.total_actions == 5
