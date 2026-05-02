"""Tests for structured memory claims and contradiction handling."""

from __future__ import annotations

from src.core.memory.schemas import MemoryItem, MemoryStatus, MemoryTier
from src.core.memory.sqlite_store import MemoryStoreManager


def _claim_item(item_id: str, value: str) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        tier=MemoryTier.episodic,
        title=f"Atlas status {value}",
        content=f"Atlas status is {value}.",
        confidence=0.9,
        metadata={
            "claim": {
                "entity": "Atlas",
                "attribute": "status",
                "value": value,
            }
        },
    )


def test_new_contradictory_claim_is_quarantined_until_approval(tmp_data_dir):
    """A newer contradictory claim waits for review before superseding the old value."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    manager.episodic.insert(_claim_item("claim-old", "blocked"))
    manager.episodic.insert(_claim_item("claim-new", "ready"))

    history = manager.episodic.get_claim_history("Atlas", "status")
    old_entry = next(entry for entry in history if entry["item_id"] == "claim-old")
    pending = manager.episodic.get("claim-new")

    assert old_entry["valid_until"] is None
    assert old_entry["superseded_by"] is None
    assert pending is not None
    assert pending.status == MemoryStatus.quarantined
    assert pending.metadata["flagged_reason"] == "claim_supersession"


def test_approved_contradictory_claim_supersedes_prior_value(tmp_data_dir):
    """Approval applies the contradiction resolution and activates the new claim."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    manager.episodic.insert(_claim_item("claim-old", "blocked"))
    manager.episodic.insert(_claim_item("claim-new", "ready"))

    manager.episodic.update_status("claim-new", MemoryStatus.active)

    history = manager.episodic.get_claim_history("Atlas", "status")
    old_entry = next(entry for entry in history if entry["item_id"] == "claim-old")
    new_entry = next(entry for entry in history if entry["item_id"] == "claim-new")
    approved = manager.episodic.get("claim-new")

    assert old_entry["valid_until"] is not None
    assert old_entry["superseded_by"] == "claim-new"
    assert new_entry["valid_until"] is None
    assert new_entry["superseded_by"] is None
    assert approved is not None
    assert approved.status == MemoryStatus.active


def test_default_retrieval_prefers_current_claim(tmp_data_dir):
    """Default search excludes memories whose structured claims were superseded."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    manager.episodic.insert(_claim_item("claim-old", "blocked"))
    manager.episodic.insert(_claim_item("claim-new", "ready"))
    manager.episodic.update_status("claim-new", MemoryStatus.active)

    results = manager.search_all("Atlas status", tiers=[MemoryTier.episodic], limit=10)

    assert [item.id for item in results] == ["claim-new"]


def test_claim_supersede_emits_receipt(tmp_data_dir):
    """Contradiction resolution emits an audit receipt."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    manager.episodic.insert(_claim_item("claim-old", "blocked"))
    manager.episodic.insert(_claim_item("claim-new", "ready"))
    manager.episodic.update_status("claim-new", MemoryStatus.active)

    receipts = manager.episodic._receipt_emitter.receipt_service.list(
        action_type="memory_claim_supersede"
    )

    assert len(receipts) == 1
    assert receipts[0].outputs["superseded_by"] == "claim-new"
    assert receipts[0].outputs["superseded_claims"][0]["item_id"] == "claim-old"
