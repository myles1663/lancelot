from datetime import datetime, timedelta

from src.core.memory.promotion import evaluate_promotion, evaluate_working_to_episodic_promotion
from src.core.memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)


def _memory_item(
    *,
    tier=MemoryTier.episodic,
    confidence=0.8,
    tags=None,
    provenance_type=ProvenanceType.system,
    content="Operational summary",
) -> MemoryItem:
    return MemoryItem(
        tier=tier,
        namespace="quest:test",
        title="Candidate",
        content=content,
        tags=tags or [],
        confidence=confidence,
        expires_at=datetime.utcnow() + timedelta(hours=1) if tier == MemoryTier.working else None,
        provenance=[Provenance(type=provenance_type, ref="test")],
    )


def test_archival_promotion_allows_verified_summary():
    item = _memory_item(tags=["summary"], provenance_type=ProvenanceType.receipt)

    decision = evaluate_promotion(item, MemoryTier.archival)

    assert decision.allowed is True
    assert decision.suggested_status == MemoryStatus.active
    assert "verifiable provenance" in decision.reason


def test_archival_promotion_quarantines_inference_only_candidate():
    item = _memory_item(tags=["summary"], provenance_type=ProvenanceType.agent_inference)

    decision = evaluate_promotion(item, MemoryTier.archival)

    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.suggested_status == MemoryStatus.quarantined


def test_archival_promotion_rejects_raw_working_memory():
    item = _memory_item(tier=MemoryTier.working, tags=["active_objective"])

    decision = evaluate_promotion(item, MemoryTier.archival)

    assert decision.allowed is False
    assert decision.suggested_status == MemoryStatus.staged
    assert "Transient working context" in decision.reason


def test_archival_promotion_rejects_low_confidence_candidate():
    item = _memory_item(confidence=0.2, tags=["summary"])

    decision = evaluate_promotion(item, MemoryTier.archival)

    assert decision.allowed is False
    assert "below archival promotion floor" in decision.reason


def test_promotion_blocks_secret_material():
    item = _memory_item(
        tags=["summary"],
        content="Use token=supersecretvalue123 for the next API call",
    )

    decision = evaluate_promotion(item, MemoryTier.archival)

    assert decision.allowed is False
    assert decision.suggested_status == MemoryStatus.quarantined
    assert "secret patterns" in decision.reason


def test_working_promotion_warns_without_ttl():
    item = _memory_item(tier=MemoryTier.working)
    item.expires_at = None

    decision = evaluate_promotion(item, MemoryTier.working)

    assert decision.allowed is True
    assert "Working memory should have an expiry" in decision.warnings


def test_working_to_episodic_promotion_allows_explicit_learning():
    item = _memory_item(tier=MemoryTier.working, tags=["learning"], confidence=0.6)
    item.last_retrieved_at = datetime.utcnow()

    decision = evaluate_working_to_episodic_promotion(item)

    assert decision.allowed is True
    assert decision.suggested_status == MemoryStatus.active
    assert decision.requires_approval is False


def test_working_to_episodic_promotion_quarantines_review_candidate():
    item = _memory_item(tier=MemoryTier.working, tags=[], confidence=0.6)
    item.last_retrieved_at = datetime.utcnow()

    decision = evaluate_working_to_episodic_promotion(item)

    assert decision.allowed is True
    assert decision.suggested_status == MemoryStatus.quarantined
    assert decision.requires_approval is True


def test_working_to_episodic_promotion_requires_retrieval():
    item = _memory_item(tier=MemoryTier.working, tags=["learning"], confidence=0.6)

    decision = evaluate_working_to_episodic_promotion(item)

    assert decision.allowed is False
    assert "retrieval" in decision.reason
