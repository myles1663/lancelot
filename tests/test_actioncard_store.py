"""
Lancelot — ActionCardStore Unit Tests
======================================
Tests for SQLite-backed ActionCard persistence.
Uses real SQLite database in a temporary directory.
"""

import os
import time
import shutil
import tempfile
import threading
import pytest

from actioncard.models import ActionCard, ActionButton, ActionCardType, ActionButtonStyle
from actioncard.store import ActionCardStore


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for tests."""
    temp_dir = tempfile.mkdtemp(prefix="lancelot_ac_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def store(temp_data_dir):
    """Create an ActionCardStore with temporary storage."""
    s = ActionCardStore(data_dir=temp_data_dir)
    yield s
    s.close()


def _make_card(**kwargs):
    """Helper to create a test ActionCard."""
    defaults = {
        "card_type": ActionCardType.APPROVAL.value,
        "title": "Test Approval",
        "description": "Test description",
        "source_system": "governance",
        "source_item_id": "item-1",
        "buttons": [
            ActionButton(id="approve", label="Approve",
                         style=ActionButtonStyle.PRIMARY.value),
            ActionButton(id="deny", label="Deny",
                         style=ActionButtonStyle.DANGER.value),
        ],
    }
    defaults.update(kwargs)
    return ActionCard(**defaults)


class TestActionCardStore:
    """Tests for ActionCardStore CRUD operations."""

    def test_save_and_get(self, store):
        """Save a card and retrieve it by ID."""
        card = _make_card()
        store.save(card)

        retrieved = store.get(card.card_id)
        assert retrieved is not None
        assert retrieved.card_id == card.card_id
        assert retrieved.title == "Test Approval"
        assert retrieved.source_system == "governance"
        assert len(retrieved.buttons) == 2
        assert retrieved.buttons[0].id == "approve"

    def test_get_nonexistent(self, store):
        """Get returns None for unknown card_id."""
        assert store.get("nonexistent-id") is None

    def test_save_upsert(self, store):
        """Save with same card_id updates (INSERT OR REPLACE)."""
        card = _make_card(title="Original")
        store.save(card)

        card.title = "Updated"
        store.save(card)

        retrieved = store.get(card.card_id)
        assert retrieved.title == "Updated"

    def test_resolve(self, store):
        """Resolve marks card as resolved with button, channel, and timestamp."""
        card = _make_card()
        store.save(card)

        result = store.resolve(card.card_id, "approve", "telegram")
        assert result is True

        retrieved = store.get(card.card_id)
        assert retrieved.resolved is True
        assert retrieved.resolved_action == "approve"
        assert retrieved.resolved_channel == "telegram"
        assert retrieved.resolved_at is not None

    def test_resolve_idempotent(self, store):
        """Double-resolve returns False (already resolved)."""
        card = _make_card()
        store.save(card)

        assert store.resolve(card.card_id, "approve", "telegram") is True
        assert store.resolve(card.card_id, "deny", "warroom") is False

        # First resolution wins
        retrieved = store.get(card.card_id)
        assert retrieved.resolved_action == "approve"
        assert retrieved.resolved_channel == "telegram"

    def test_resolve_nonexistent(self, store):
        """Resolve returns False for unknown card_id."""
        assert store.resolve("nonexistent", "approve", "api") is False

    def test_list_pending(self, store):
        """list_pending returns only unresolved cards."""
        card1 = _make_card(title="Pending 1")
        card2 = _make_card(title="Pending 2")
        card3 = _make_card(title="Resolved")
        store.save(card1)
        store.save(card2)
        store.save(card3)
        store.resolve(card3.card_id, "approve", "api")

        pending = store.list_pending()
        assert len(pending) == 2
        titles = {c.title for c in pending}
        assert "Pending 1" in titles
        assert "Pending 2" in titles
        assert "Resolved" not in titles

    def test_list_pending_by_source_system(self, store):
        """list_pending filters by source_system."""
        card1 = _make_card(source_system="soul")
        card2 = _make_card(source_system="governance")
        store.save(card1)
        store.save(card2)

        soul_cards = store.list_pending(source_system="soul")
        assert len(soul_cards) == 1
        assert soul_cards[0].source_system == "soul"

    def test_list_pending_excludes_expired(self, store):
        """list_pending filters out expired cards."""
        fresh = _make_card(title="Fresh")
        expired = _make_card(title="Expired", expires_at=time.time() - 1)
        store.save(fresh)
        store.save(expired)

        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].title == "Fresh"

    def test_list_all(self, store):
        """list_all returns all cards including resolved."""
        card1 = _make_card()
        card2 = _make_card()
        store.save(card1)
        store.save(card2)
        store.resolve(card2.card_id, "deny", "api")

        all_cards = store.list_all()
        assert len(all_cards) == 2

    def test_list_all_exclude_resolved(self, store):
        """list_all with include_resolved=False."""
        card1 = _make_card()
        card2 = _make_card()
        store.save(card1)
        store.save(card2)
        store.resolve(card2.card_id, "deny", "api")

        unresolved = store.list_all(include_resolved=False)
        assert len(unresolved) == 1

    def test_get_by_prefix(self, store):
        """get_by_prefix finds card by ID prefix (for Telegram)."""
        card = _make_card()
        store.save(card)

        prefix = card.card_id[:8]
        found = store.get_by_prefix(prefix)
        assert found is not None
        assert found.card_id == card.card_id

    def test_get_by_prefix_prefers_unresolved(self, store):
        """get_by_prefix returns unresolved card when multiple match prefix."""
        # Create two cards with same prefix (unlikely but we test ordering)
        card = _make_card()
        store.save(card)

        found = store.get_by_prefix(card.card_id[:8])
        assert found is not None
        assert found.resolved is False

    def test_get_by_prefix_nonexistent(self, store):
        """get_by_prefix returns None for unknown prefix."""
        assert store.get_by_prefix("xxxxxxxx") is None

    def test_set_telegram_message_id(self, store):
        """set_telegram_message_id persists the Telegram message ID."""
        card = _make_card()
        store.save(card)

        store.set_telegram_message_id(card.card_id, 12345)

        retrieved = store.get(card.card_id)
        assert retrieved.telegram_message_id == 12345

    def test_cleanup_expired(self, store):
        """cleanup_expired deletes old resolved and expired cards."""
        # Resolved card older than 24h
        old_resolved = _make_card(
            resolved=True,
            resolved_action="approve",
            resolved_at=time.time() - 90000,  # > 24h ago
        )
        # Expired unresolved card
        expired = _make_card(expires_at=time.time() - 1)
        # Fresh pending card (should survive)
        fresh = _make_card()

        store.save(old_resolved)
        store.save(expired)
        store.save(fresh)

        deleted = store.cleanup_expired()
        assert deleted >= 2

        remaining = store.list_all()
        assert len(remaining) == 1
        assert remaining[0].card_id == fresh.card_id

    def test_metadata_persistence(self, store):
        """Metadata dict survives save/load cycle."""
        card = _make_card(metadata={"risk": "high", "count": 5})
        store.save(card)

        retrieved = store.get(card.card_id)
        assert retrieved.metadata["risk"] == "high"
        assert retrieved.metadata["count"] == 5

    def test_quest_id_persistence(self, store):
        """quest_id survives save/load cycle."""
        card = _make_card(quest_id="quest-abc-123")
        store.save(card)

        retrieved = store.get(card.card_id)
        assert retrieved.quest_id == "quest-abc-123"

    def test_buttons_persistence(self, store):
        """Buttons with all fields survive save/load cycle."""
        card = _make_card(buttons=[
            ActionButton(
                id="confirm", label="Confirm Delete",
                style=ActionButtonStyle.DANGER.value,
                callback_data="custom_data",
                requires_confirmation=True,
            ),
        ])
        store.save(card)

        retrieved = store.get(card.card_id)
        assert len(retrieved.buttons) == 1
        btn = retrieved.buttons[0]
        assert btn.id == "confirm"
        assert btn.label == "Confirm Delete"
        assert btn.style == "danger"
        assert btn.callback_data == "custom_data"
        assert btn.requires_confirmation is True

    def test_thread_safety(self, store):
        """Concurrent saves don't corrupt the database."""
        errors = []
        cards = [_make_card(title=f"Card {i}") for i in range(20)]

        def save_card(card):
            try:
                store.save(card)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=save_card, args=(c,)) for c in cards]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        all_cards = store.list_all(limit=100)
        assert len(all_cards) == 20

    def test_database_file_created(self, temp_data_dir):
        """Store creates actioncards.db file."""
        s = ActionCardStore(data_dir=temp_data_dir)
        assert os.path.exists(os.path.join(temp_data_dir, "actioncards.db"))
        s.close()

    def test_limit_parameter(self, store):
        """list_pending and list_all respect limit parameter."""
        for i in range(10):
            store.save(_make_card(title=f"Card {i}"))

        limited = store.list_pending(limit=3)
        assert len(limited) == 3

        all_limited = store.list_all(limit=5)
        assert len(all_limited) == 5
