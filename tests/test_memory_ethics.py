"""Deterministic memory ethics enforcement tests."""

from __future__ import annotations

from src.core.memory.compiler import ContextCompilerService
from src.core.memory.schemas import MemoryItem, MemoryStatus, MemoryTier
from src.core.memory.sqlite_store import MemoryStoreManager


def test_pii_without_consent_quarantines_long_term_memory(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="pii-memory",
        tier=MemoryTier.episodic,
        title="Contact note",
        content="Call Ada at 555-123-4567 about the deployment.",
        confidence=0.9,
    )

    manager.episodic.insert(item)

    stored = manager.episodic.get("pii-memory")
    assert stored.status == MemoryStatus.quarantined
    assert stored.metadata["ethics_rule"] == "pii_requires_consent"
    assert stored.metadata["flagged_reason"] == "memory_ethics"


def test_pii_with_consent_marker_can_remain_active(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="consented-pii-memory",
        tier=MemoryTier.episodic,
        title="Contact note",
        content="Call Ada at 555-123-4567 about the deployment.",
        confidence=0.9,
        metadata={"consent_marker": True},
    )

    manager.episodic.insert(item)

    stored = manager.episodic.get("consented-pii-memory")
    assert stored.status == MemoryStatus.active
    assert "ethics_rule" not in stored.metadata


def test_secret_material_is_redacted_before_persistence(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="secret-memory",
        tier=MemoryTier.archival,
        title="Secret note",
        content="The api_key=abcdefghijklmnopqrstuvwxyz123456 should not persist.",
        confidence=0.9,
    )

    manager.archival.insert(item)

    stored = manager.archival.get("secret-memory")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in stored.content
    assert "[REDACTED_SECRET]" in stored.content
    assert stored.metadata["ethics_rule"] == "secret_redact"


def test_ethics_violation_emits_receipt(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="ethics-receipt-memory",
        tier=MemoryTier.archival,
        title="PII note",
        content="Ada's SSN is 123-45-6789.",
        confidence=0.9,
    )

    manager.archival.insert(item)

    receipts = manager.archival._receipt_emitter.receipt_service.list(
        action_type="memory_ethics_evaluation"
    )
    assert len(receipts) == 1
    assert receipts[0].outputs["item_id"] == "ethics-receipt-memory"
    assert receipts[0].outputs["rule_name"] == "pii_requires_consent"


def test_soul_content_is_quarantined_and_excluded_from_context(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="soul-memory",
        tier=MemoryTier.archival,
        title="Soul copy",
        content="risk_rules:\n- destructive\nmemory_ethics:\n- no pii\nautonomy_posture:\n  mode: governed",
        confidence=0.9,
    )
    manager.archival.insert(item)
    service = ContextCompilerService(tmp_data_dir, memory_manager=manager)

    stored = manager.archival.get("soul-memory")
    ctx = service.compiler.compile(
        objective="Soul copy",
        retrieved_items=[stored],
    )

    assert stored.status == MemoryStatus.quarantined
    assert stored.metadata["ethics_rule"] == "soul_content_excluded_from_memory"
    assert any(
        exclusion["item_id"] == "soul-memory"
        and exclusion["reason"] == "memory_ethics"
        for exclusion in ctx.excluded_candidates
    )


def test_security_discussion_is_not_false_positive(tmp_data_dir):
    manager = MemoryStoreManager(tmp_data_dir)
    item = MemoryItem(
        id="security-discussion",
        tier=MemoryTier.episodic,
        title="Security review",
        content="The team discussed PII handling and secret redaction requirements.",
        confidence=0.9,
    )

    manager.episodic.insert(item)

    stored = manager.episodic.get("security-discussion")
    assert stored.status == MemoryStatus.active
    assert "ethics_rule" not in stored.metadata
