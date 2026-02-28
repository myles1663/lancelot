"""
Lancelot — ActionCardResolver Unit Tests
=========================================
Tests for routing button clicks to approval subsystems.
"""

import tempfile
import shutil
import time
import pytest
from unittest.mock import MagicMock

from actioncard.models import ActionCard, ActionButton, ActionCardType, ActionButtonStyle
from actioncard.store import ActionCardStore
from actioncard.resolver import ActionCardResolver


@pytest.fixture
def temp_data_dir():
    temp_dir = tempfile.mkdtemp(prefix="lancelot_acr_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def store(temp_data_dir):
    s = ActionCardStore(data_dir=temp_data_dir)
    yield s
    s.close()


@pytest.fixture
def resolver(store):
    event_bus = MagicMock()
    receipt_service = MagicMock()
    r = ActionCardResolver(
        card_store=store,
        event_bus=event_bus,
        receipt_service=receipt_service,
    )
    return r


def _make_card(store, source_system="governance", **kwargs):
    defaults = {
        "card_type": ActionCardType.APPROVAL.value,
        "title": "Test",
        "source_system": source_system,
        "source_item_id": "item-1",
        "buttons": [
            ActionButton(id="approve", label="Approve",
                         style=ActionButtonStyle.PRIMARY.value),
            ActionButton(id="deny", label="Deny",
                         style=ActionButtonStyle.DANGER.value),
        ],
    }
    defaults.update(kwargs)
    card = ActionCard(**defaults)
    store.save(card)
    return card


class TestActionCardResolver:

    def test_resolve_calls_handler(self, resolver, store):
        """resolve() calls the registered handler with correct args."""
        card = _make_card(store)

        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        result = resolver.resolve(card.card_id, "approve", "warroom")

        handler.assert_called_once_with("item-1", "approve")
        assert result["status"] == "approved"

    def test_resolve_marks_card_resolved(self, resolver, store):
        """resolve() marks the card as resolved in store."""
        card = _make_card(store)
        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        resolver.resolve(card.card_id, "approve", "telegram")

        updated = store.get(card.card_id)
        assert updated.resolved is True
        assert updated.resolved_action == "approve"
        assert updated.resolved_channel == "telegram"

    def test_double_resolve_rejected(self, resolver, store):
        """Second resolve returns error."""
        card = _make_card(store)
        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        resolver.resolve(card.card_id, "approve", "telegram")
        result = resolver.resolve(card.card_id, "deny", "warroom")

        assert result["status"] == "error"
        assert "Already resolved" in result["message"]

    def test_card_not_found(self, resolver):
        """resolve() returns error for unknown card."""
        result = resolver.resolve("nonexistent", "approve", "api")
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_no_handler_registered(self, resolver, store):
        """resolve() returns error when no handler for source_system."""
        card = _make_card(store, source_system="unknown_system")
        result = resolver.resolve(card.card_id, "approve", "api")
        assert result["status"] == "error"
        assert "No handler" in result["message"]

    def test_handler_exception(self, resolver, store):
        """resolve() handles handler exceptions gracefully."""
        card = _make_card(store)
        handler = MagicMock(side_effect=RuntimeError("DB connection failed"))
        resolver.register_handler("governance", handler)

        result = resolver.resolve(card.card_id, "approve", "api")
        assert result["status"] == "error"
        assert "DB connection failed" in result["message"]

    def test_expired_card_rejected(self, resolver, store):
        """resolve() rejects expired cards."""
        card = _make_card(store, expires_at=time.time() - 1)
        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        result = resolver.resolve(card.card_id, "approve", "api")
        assert result["status"] == "error"
        assert "expired" in result["message"].lower()

    def test_prefix_lookup(self, resolver, store):
        """resolve() supports prefix-based card lookup."""
        card = _make_card(store)
        handler = MagicMock(return_value={"status": "denied", "message": "No"})
        resolver.register_handler("governance", handler)

        # Use 8-char prefix like Telegram would
        result = resolver.resolve(card.card_id[:8], "deny", "telegram")
        assert result["status"] == "denied"

    def test_emits_resolution_event(self, resolver, store):
        """resolve() emits actioncard_resolved event via EventBus."""
        card = _make_card(store)
        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        resolver.resolve(card.card_id, "approve", "warroom")

        resolver._event_bus.publish_sync.assert_called_once()
        event = resolver._event_bus.publish_sync.call_args[0][0]
        assert event.type == "actioncard_resolved"
        assert event.payload["card_id"] == card.card_id

    def test_creates_receipt(self, resolver, store):
        """resolve() creates an audit receipt."""
        card = _make_card(store, quest_id="q-123")
        handler = MagicMock(return_value={"status": "approved", "message": "OK"})
        resolver.register_handler("governance", handler)

        resolver.resolve(card.card_id, "approve", "telegram")

        resolver._receipt_service.create.assert_called_once()
        receipt = resolver._receipt_service.create.call_args[0][0]
        assert receipt.action_type == "action_card_resolved"
        assert receipt.quest_id == "q-123"

    def test_register_multiple_handlers(self, resolver, store):
        """Multiple handlers for different source_systems."""
        gov_handler = MagicMock(return_value={"status": "approved", "message": "Gov OK"})
        sched_handler = MagicMock(return_value={"status": "approved", "message": "Sched OK"})
        resolver.register_handler("governance", gov_handler)
        resolver.register_handler("scheduler", sched_handler)

        card1 = _make_card(store, source_system="governance")
        card2 = _make_card(store, source_system="scheduler")

        result1 = resolver.resolve(card1.card_id, "approve", "api")
        result2 = resolver.resolve(card2.card_id, "approve", "api")

        assert result1["message"] == "Gov OK"
        assert result2["message"] == "Sched OK"

    def test_deny_action(self, resolver, store):
        """Deny button routes correctly."""
        card = _make_card(store)
        handler = MagicMock(return_value={"status": "denied", "message": "Rejected"})
        resolver.register_handler("governance", handler)

        result = resolver.resolve(card.card_id, "deny", "telegram")

        handler.assert_called_once_with("item-1", "deny")
        assert result["status"] == "denied"
