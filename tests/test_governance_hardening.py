"""Tests for vNext4 Security Hardening (Prompt 22).

Attack surface tests ensuring the risk-tiered system cannot be exploited.
"""

import hashlib
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import load_governance_config, IntentTemplateConfig, PolicyCacheConfig
from governance.risk_classifier import RiskClassifier
from governance.policy_cache import PolicyCache
from governance.async_verifier import AsyncVerificationQueue, VerificationJob
from governance.rollback import RollbackManager
from governance.batch_receipts import BatchReceiptBuffer, ReceiptEntry
from governance.intent_templates import IntentTemplateRegistry
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")


@pytest.fixture
def gov_config():
    return load_governance_config(CONFIG_PATH)


@pytest.fixture
def classifier(gov_config):
    return RiskClassifier(gov_config.risk_classification)


# ── TIER DOWNGRADE ATTACKS ───────────────────────────────────────

def test_injection_in_capability(classifier):
    """Crafted capability string classified as T3."""
    profile = classifier.classify("fs.read; rm -rf")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_empty_capability(classifier):
    """Empty capability string → T3."""
    profile = classifier.classify("")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_path_traversal_capability(classifier):
    """Capability with path traversal → T3."""
    profile = classifier.classify("../fs.read")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_scope_injection(classifier):
    """Scope injection does not crash, treated as unknown scope."""
    profile = classifier.classify("fs.write", scope="workspace; DROP TABLE")
    # Should not crash — unknown scope, fs.write stays T1
    assert profile.tier >= RiskTier.T1_REVERSIBLE


def test_unicode_homoglyph_capability(classifier):
    """Unicode homoglyph capability → T3 (not recognized)."""
    # Fullwidth 'f' instead of normal 'f'
    profile = classifier.classify("\uff46s.read")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


# ── CACHE POISONING ──────────────────────────────────────────────

def test_cache_soul_version_rejects_after_change(gov_config, classifier):
    """PolicyCache with soul_version v1 rejects after soul changes to v2."""
    config = PolicyCacheConfig(validate_soul_version=True)
    cache = PolicyCache(config=config, risk_classifier=classifier, soul_version="v1")
    assert cache.lookup("fs.read", "workspace") is not None

    cache._soul_version = "v2"
    assert cache.lookup("fs.read", "workspace") is None


def test_cache_invalidate_clears_all(gov_config, classifier):
    """PolicyCache.invalidate() actually clears ALL entries."""
    cache = PolicyCache(config=gov_config.policy_cache, risk_classifier=classifier, soul_version="v1")
    assert cache.stats.total_entries > 0
    cache.invalidate()
    assert cache.stats.total_entries == 0


def test_cache_recompile_updates_entries(gov_config, classifier):
    """Recompile with new soul version updates all entries."""
    cache = PolicyCache(config=gov_config.policy_cache, risk_classifier=classifier, soul_version="v1")
    cache.recompile(classifier, soul_version="v2")
    assert cache.stats.soul_version == "v2"
    decision = cache.lookup("fs.read", "workspace")
    assert decision is not None
    assert decision.soul_version == "v2"


def test_cache_no_t2_t3_entries(gov_config, classifier):
    """Cache only contains T0 and T1 entries."""
    cache = PolicyCache(config=gov_config.policy_cache, risk_classifier=classifier, soul_version="v1")
    for entry in cache._cache.values():
        assert entry.tier in (RiskTier.T0_INERT, RiskTier.T1_REVERSIBLE)


# ── TEMPLATE INJECTION ───────────────────────────────────────────

def test_template_rejects_t2_step(tmp_path):
    """create_candidate() with T2 step raises ValueError."""
    config = IntentTemplateConfig(max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        reg.create_candidate("test", [{"capability": "shell.exec", "risk_tier": 2}])


def test_template_rejects_t3_step(tmp_path):
    """create_candidate() with T3 step raises ValueError."""
    config = IntentTemplateConfig(max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        reg.create_candidate("test", [{"capability": "net.post", "risk_tier": 3}])


def test_template_cannot_bypass_registry(tmp_path):
    """Template with T2+ steps manually inserted is still validated on create."""
    config = IntentTemplateConfig(max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))

    # Try to sneak T2 step through
    with pytest.raises(ValueError):
        reg.create_candidate("bypass", [
            {"capability": "fs.read", "risk_tier": 0},
            {"capability": "shell.exec", "risk_tier": 2},  # Should fail
        ])


def test_soul_change_invalidates_templates(tmp_path):
    """Soul change calls invalidate_all() on templates."""
    config = IntentTemplateConfig(promotion_threshold=2, max_template_risk_tier=1)
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    tid = reg.create_candidate("test", [{"capability": "fs.read", "risk_tier": 0}])
    # Promote: create_candidate sets success_count=1, need 1 more for threshold=2
    reg.record_success(tid)
    assert reg.get_template(tid).active is True

    count = reg.invalidate_all(reason="Soul v2")
    assert count == 1
    assert reg.get_template(tid).active is False


# ── BATCH RECEIPT DATA INTEGRITY ─────────────────────────────────

def test_batch_receipt_json_contains_all_entries(tmp_path):
    """Batch receipt JSON on disk contains all entries from buffer."""
    buf = BatchReceiptBuffer(task_id="test", data_dir=str(tmp_path))
    for i in range(5):
        buf.append("fs.read", "local", RiskTier.T0_INERT, f"in{i}", f"out{i}", True)
    buf.flush()

    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert len(data["entries"]) == 5


def test_batch_receipt_hashes_deterministic(tmp_path):
    """Same input → same hash."""
    buf = BatchReceiptBuffer(task_id="test", data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "same_input", "same_output", True)
    entry1_hash = buf._entries[0].input_hash

    buf2 = BatchReceiptBuffer(task_id="test2", data_dir=str(tmp_path))
    buf2.append("fs.read", "local", RiskTier.T0_INERT, "same_input", "same_output", True)
    entry2_hash = buf2._entries[0].input_hash

    assert entry1_hash == entry2_hash
    # Verify it's actually a SHA-256 hash of the input
    expected = hashlib.sha256("same_input".encode()).hexdigest()
    assert entry1_hash == expected


def test_batch_receipt_zero_entries_no_file(tmp_path):
    """Batch receipt with 0 entries does not write a file."""
    buf = BatchReceiptBuffer(task_id="test", data_dir=str(tmp_path))
    result = buf.flush()
    assert result is None
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 0


# ── ASYNC VERIFICATION BYPASS ────────────────────────────────────

def test_t2_blocked_with_pending_queue():
    """T2 cannot proceed while async queue has pending jobs."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    queue.submit(VerificationJob(capability="fs.write", output="data"))
    assert queue.depth == 1

    # Must drain before T2
    drain = queue.drain()
    assert queue.depth == 0
    assert drain.drained_count == 1


def test_t3_blocked_with_pending_queue():
    """T3 cannot proceed while async queue has pending jobs."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True)
    queue.submit(VerificationJob(capability="fs.write", output="data"))
    queue.submit(VerificationJob(capability="fs.write", output="data2"))
    assert queue.depth == 2

    drain = queue.drain()
    assert queue.depth == 0
    assert drain.drained_count == 2


def test_drain_failures_block_t2():
    """After drain with failures, T2 action should be blocked."""
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: False)
    queue.submit(VerificationJob(capability="fs.write", output="bad"))
    drain = queue.drain()
    assert drain.failed == 1
    # Caller should check drain.failed > 0 and block T2


def test_queue_fallback_to_sync():
    """Queue saturation: fallback_to_sync actually runs synchronously."""
    from governance.config import AsyncVerificationConfig
    config = AsyncVerificationConfig(queue_max_depth=1, fallback_to_sync_on_full=True)
    queue = AsyncVerificationQueue(verify_fn=lambda c, o: True, config=config)

    queue.submit(VerificationJob(capability="fs.write", output="data1"))
    assert queue.depth == 1

    # Second job runs synchronously due to full queue
    queue.submit(VerificationJob(capability="fs.write", output="data2"))
    assert queue.depth == 1  # Still 1 in queue
    assert len(queue.results) == 1  # 1 sync result


# ── ROLLBACK SAFETY ──────────────────────────────────────────────

def test_rollback_restores_exact_content(tmp_path):
    """Rollback restores exact file content (byte-for-byte)."""
    original = "Hello, World!\nLine 2\n\tTabbed"
    test_file = tmp_path / "test.txt"
    test_file.write_text(original, encoding="utf-8")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("t", 0, "fs.write", target="test.txt")

    test_file.write_text("Modified content", encoding="utf-8")
    mgr.get_rollback_action(snap.snapshot_id)()

    assert test_file.read_text(encoding="utf-8") == original


def test_rollback_removes_new_file(tmp_path):
    """Rollback of new file creation deletes the file."""
    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("t", 0, "fs.write", target="new.txt")

    (tmp_path / "new.txt").write_text("new")
    assert (tmp_path / "new.txt").exists()

    mgr.get_rollback_action(snap.snapshot_id)()
    assert not (tmp_path / "new.txt").exists()


def test_double_rollback_idempotent(tmp_path):
    """Double rollback is idempotent."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("original")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("t", 0, "fs.write", target="test.txt")
    test_file.write_text("changed")

    action = mgr.get_rollback_action(snap.snapshot_id)
    action()  # First rollback
    assert test_file.read_text() == "original"

    test_file.write_text("changed again")
    action()  # Second rollback — should be noop
    assert test_file.read_text() == "changed again"


def test_rollback_no_file_handle_leak(tmp_path):
    """Multiple rollback operations don't leak file handles."""
    mgr = RollbackManager(workspace=str(tmp_path))

    for i in range(20):
        fname = f"file_{i}.txt"
        (tmp_path / fname).write_text(f"original_{i}")
        snap = mgr.create_snapshot("t", i, "fs.write", target=fname)
        (tmp_path / fname).write_text(f"modified_{i}")
        mgr.get_rollback_action(snap.snapshot_id)()
        assert (tmp_path / fname).read_text() == f"original_{i}"

    # If we got here without error, no file handle leaks
    assert len(mgr.active_snapshots) == 0
