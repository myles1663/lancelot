"""
Lancelot — ActionCard Models Unit Tests
========================================
Tests for ActionCard, ActionButton, and enum types.
"""

import time
import json
import pytest

from actioncard.models import (
    ActionCard, ActionButton,
    ActionCardType, ActionButtonStyle,
)


class TestActionButtonStyle:
    """Tests for ActionButtonStyle enum."""

    def test_styles_exist(self):
        assert ActionButtonStyle.PRIMARY.value == "primary"
        assert ActionButtonStyle.DANGER.value == "danger"
        assert ActionButtonStyle.SECONDARY.value == "secondary"


class TestActionCardType:
    """Tests for ActionCardType enum."""

    def test_types_exist(self):
        assert ActionCardType.APPROVAL.value == "approval"
        assert ActionCardType.CONFIRMATION.value == "confirmation"
        assert ActionCardType.CHOICE.value == "choice"
        assert ActionCardType.INFO.value == "info"


class TestActionButton:
    """Tests for the ActionButton dataclass."""

    def test_creation(self):
        btn = ActionButton(id="approve", label="Approve")
        assert btn.id == "approve"
        assert btn.label == "Approve"
        assert btn.style == "secondary"
        assert btn.callback_data == ""
        assert btn.requires_confirmation is False

    def test_to_dict(self):
        btn = ActionButton(
            id="deny", label="Deny",
            style=ActionButtonStyle.DANGER.value,
            requires_confirmation=True,
        )
        d = btn.to_dict()
        assert d["id"] == "deny"
        assert d["label"] == "Deny"
        assert d["style"] == "danger"
        assert d["requires_confirmation"] is True


class TestActionCard:
    """Tests for the ActionCard dataclass."""

    def _make_card(self, **kwargs):
        defaults = {
            "card_type": ActionCardType.APPROVAL.value,
            "title": "Approve Deployment",
            "description": "Deploy v2.0 to production",
            "source_system": "governance",
            "source_item_id": "req-123",
            "buttons": [
                ActionButton(id="approve", label="Approve",
                             style=ActionButtonStyle.PRIMARY.value),
                ActionButton(id="deny", label="Deny",
                             style=ActionButtonStyle.DANGER.value),
            ],
        }
        defaults.update(kwargs)
        return ActionCard(**defaults)

    def test_default_creation(self):
        """Card creates with UUID and sensible defaults."""
        card = ActionCard()
        assert len(card.card_id) == 36
        assert card.card_type == ActionCardType.INFO.value
        assert card.resolved is False
        assert card.created_at > 0
        assert card.buttons == []

    def test_full_creation(self):
        """Card accepts all parameters."""
        card = self._make_card()
        assert card.title == "Approve Deployment"
        assert card.source_system == "governance"
        assert len(card.buttons) == 2

    def test_short_id(self):
        """short_id returns first 8 chars."""
        card = ActionCard(card_id="abcdef12-3456-7890-abcd-ef1234567890")
        assert card.short_id() == "abcdef12"

    def test_to_dict(self):
        """Serialization includes all fields."""
        card = self._make_card()
        d = card.to_dict()
        assert d["card_type"] == "approval"
        assert d["title"] == "Approve Deployment"
        assert len(d["buttons"]) == 2
        assert d["buttons"][0]["id"] == "approve"
        assert d["resolved"] is False

    def test_from_dict_round_trip(self):
        """from_dict(to_dict()) produces equivalent card."""
        original = self._make_card(quest_id="q-123")
        d = original.to_dict()
        restored = ActionCard.from_dict(d)
        assert restored.card_id == original.card_id
        assert restored.title == original.title
        assert restored.quest_id == "q-123"
        assert len(restored.buttons) == 2
        assert restored.buttons[0].id == "approve"

    def test_to_telegram_keyboard(self):
        """Telegram keyboard has correct InlineKeyboardMarkup structure."""
        card = self._make_card()
        keyboard = card.to_telegram_keyboard()
        assert "inline_keyboard" in keyboard
        rows = keyboard["inline_keyboard"]
        assert len(rows) == 2
        # Each row is a list with one button
        assert rows[0][0]["text"] == "Approve"
        assert rows[1][0]["text"] == "Deny"

    def test_telegram_callback_data_format(self):
        """Callback data follows ac:{short_id}:{button_id} format."""
        card = self._make_card()
        keyboard = card.to_telegram_keyboard()
        cb = keyboard["inline_keyboard"][0][0]["callback_data"]
        assert cb.startswith("ac:")
        parts = cb.split(":")
        assert len(parts) == 3
        assert parts[0] == "ac"
        assert parts[1] == card.short_id()
        assert parts[2] == "approve"

    def test_telegram_callback_data_within_64_bytes(self):
        """Callback data fits within Telegram's 64-byte limit."""
        card = self._make_card()
        keyboard = card.to_telegram_keyboard()
        for row in keyboard["inline_keyboard"]:
            for btn in row:
                assert len(btn["callback_data"].encode("utf-8")) <= 64

    def test_telegram_callback_data_long_button_id(self):
        """Even with long button IDs, callback data stays within 64 bytes."""
        card = ActionCard(
            buttons=[
                ActionButton(id="a" * 50, label="Long"),
            ],
        )
        keyboard = card.to_telegram_keyboard()
        cb = keyboard["inline_keyboard"][0][0]["callback_data"]
        assert len(cb.encode("utf-8")) <= 64

    def test_to_telegram_text(self):
        """Telegram text has title and description in Markdown."""
        card = self._make_card()
        text = card.to_telegram_text()
        assert "*Approve Deployment*" in text
        assert "Deploy v2.0 to production" in text

    def test_to_event_bus(self):
        """Converts to EventBus Event with correct type."""
        card = self._make_card()
        event = card.to_event_bus()
        assert event.type == "actioncard_presented"
        assert event.payload["card_id"] == card.card_id

    def test_is_expired_no_expiry(self):
        """Card without expires_at is never expired."""
        card = ActionCard()
        assert not card.is_expired()

    def test_is_expired_future(self):
        """Card with future expires_at is not expired."""
        card = ActionCard(expires_at=time.time() + 3600)
        assert not card.is_expired()

    def test_is_expired_past(self):
        """Card with past expires_at is expired."""
        card = ActionCard(expires_at=time.time() - 1)
        assert card.is_expired()

    def test_resolution_fields(self):
        """Resolution fields track which button and channel resolved."""
        card = self._make_card(
            resolved=True,
            resolved_action="approve",
            resolved_at=time.time(),
            resolved_channel="telegram",
        )
        assert card.resolved is True
        assert card.resolved_action == "approve"
        assert card.resolved_channel == "telegram"

    def test_metadata_dict(self):
        """Metadata stores arbitrary key-value pairs."""
        card = self._make_card(metadata={"risk_level": "high", "count": 42})
        assert card.metadata["risk_level"] == "high"
        assert card.metadata["count"] == 42

    def test_json_serializable(self):
        """to_dict() output is JSON-serializable."""
        card = self._make_card(quest_id="q-1", metadata={"key": "val"})
        json_str = json.dumps(card.to_dict())
        assert json_str  # Should not raise
        parsed = json.loads(json_str)
        assert parsed["card_id"] == card.card_id
