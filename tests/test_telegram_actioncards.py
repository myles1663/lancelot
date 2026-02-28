"""
Lancelot -- Telegram ActionCard Integration Tests
==================================================
Tests for ActionCard presentation via Telegram, callback_query handling,
security gates, and cross-channel resolution events.

All HTTP calls are mocked -- no real Telegram API requests are made.
"""

import asyncio
import time
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from telegram_bot import TelegramBot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bot():
    """Create a TelegramBot with token and chat_id set, no real orchestrator."""
    with patch.dict("os.environ", {
        "LANCELOT_TELEGRAM_TOKEN": "fake-token-123",
        "LANCELOT_TELEGRAM_CHAT_ID": "999888",
    }):
        b = TelegramBot(orchestrator=None)
        b.token = "fake-token-123"
        b.chat_id = "999888"
        return b


@pytest.fixture
def mock_resolver():
    """A mock ActionCardResolver."""
    resolver = MagicMock()
    resolver.resolve.return_value = {"status": "approved", "message": "Action approved"}
    return resolver


@pytest.fixture
def mock_store():
    """A mock ActionCardStore."""
    return MagicMock()


@pytest.fixture
def bot_with_resolver(bot, mock_resolver, mock_store):
    """Bot with resolver and store injected (as gateway does)."""
    bot._action_card_resolver = mock_resolver
    bot._action_card_store = mock_store
    return bot


def _make_callback_update(data="ac:abcdef12:approve", chat_id="999888",
                          message_id=42, query_id="cb-111"):
    """Build a Telegram callback_query update dict."""
    return {
        "update_id": 100,
        "callback_query": {
            "id": query_id,
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": int(chat_id)},
                "text": "*Approve Deployment*\n\nDeploy v2.0",
            },
            "from": {"id": 12345, "first_name": "Owner"},
        },
    }


# ---------------------------------------------------------------------------
# send_message_with_keyboard tests
# ---------------------------------------------------------------------------

class TestSendMessageWithKeyboard:
    """Tests for the send_message_with_keyboard method."""

    @patch("telegram_bot.requests.post")
    def test_sends_with_keyboard_returns_message_id(self, mock_post, bot):
        """Should POST to sendMessage with reply_markup and return message_id."""
        mock_post.return_value = MagicMock(
            ok=True,
            json=lambda: {"result": {"message_id": 42}},
        )

        keyboard = {"inline_keyboard": [[{"text": "Yes", "callback_data": "ac:abc:yes"}]]}
        result = bot.send_message_with_keyboard("Test text", keyboard=keyboard)

        assert result == 42
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["reply_markup"] == keyboard
        assert payload["text"] == "Test text"

    @patch("telegram_bot.requests.post")
    def test_sends_without_keyboard(self, mock_post, bot):
        """Should work without a keyboard (for progress messages)."""
        mock_post.return_value = MagicMock(
            ok=True,
            json=lambda: {"result": {"message_id": 99}},
        )

        result = bot.send_message_with_keyboard("Progress...", keyboard=None)
        assert result == 99
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "reply_markup" not in payload

    @patch("telegram_bot.requests.post")
    def test_returns_none_on_failure(self, mock_post, bot):
        """Should return None when both attempts fail."""
        mock_post.return_value = MagicMock(ok=False)
        result = bot.send_message_with_keyboard("Test", keyboard=None)
        assert result is None

    def test_returns_none_when_no_token(self):
        """Should return None when token is missing."""
        b = TelegramBot(orchestrator=None)
        b.token = ""
        result = b.send_message_with_keyboard("Test", keyboard=None)
        assert result is None


# ---------------------------------------------------------------------------
# edit_message tests
# ---------------------------------------------------------------------------

class TestEditMessage:
    """Tests for the edit_message method."""

    @patch("telegram_bot.requests.post")
    def test_edits_message_successfully(self, mock_post, bot):
        """Should POST to editMessageText and return True."""
        mock_post.return_value = MagicMock(ok=True)

        result = bot.edit_message(42, "Updated text")
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["message_id"] == 42
        assert payload["text"] == "Updated text"

    @patch("telegram_bot.requests.post")
    def test_edit_with_empty_keyboard(self, mock_post, bot):
        """Should include reply_markup when keyboard is provided (even empty)."""
        mock_post.return_value = MagicMock(ok=True)

        bot.edit_message(42, "Resolved", keyboard={"inline_keyboard": []})
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["reply_markup"] == {"inline_keyboard": []}

    @patch("telegram_bot.requests.post")
    def test_edit_not_modified_is_ok(self, mock_post, bot):
        """Telegram returns 400 when content is unchanged -- treat as success."""
        mock_post.return_value = MagicMock(
            ok=False,
            status_code=400,
            text='{"description":"Bad Request: message is not modified"}',
        )

        result = bot.edit_message(42, "Same text")
        assert result is True

    @patch("telegram_bot.requests.post")
    def test_edit_returns_false_on_error(self, mock_post, bot):
        """Should return False on actual errors."""
        mock_post.return_value = MagicMock(
            ok=False,
            status_code=500,
            text="Internal Server Error",
        )
        result = bot.edit_message(42, "Text")
        assert result is False


# ---------------------------------------------------------------------------
# answer_callback_query tests
# ---------------------------------------------------------------------------

class TestAnswerCallbackQuery:
    """Tests for the answer_callback_query method."""

    @patch("telegram_bot.requests.post")
    def test_answers_callback(self, mock_post, bot):
        """Should POST to answerCallbackQuery."""
        mock_post.return_value = MagicMock(ok=True)

        result = bot.answer_callback_query("cb-123", text="Approved!")
        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["callback_query_id"] == "cb-123"
        assert payload["text"] == "Approved!"

    @patch("telegram_bot.requests.post")
    def test_answer_truncates_long_text(self, mock_post, bot):
        """Text should be truncated to 200 chars."""
        mock_post.return_value = MagicMock(ok=True)

        bot.answer_callback_query("cb-123", text="x" * 300)
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert len(payload["text"]) == 200


# ---------------------------------------------------------------------------
# _handle_callback_query tests
# ---------------------------------------------------------------------------

class TestHandleCallbackQuery:
    """Tests for ActionCard callback_query routing."""

    @patch("telegram_bot.requests.post")
    def test_routes_to_resolver(self, mock_post, bot_with_resolver):
        """Should call resolver.resolve() with correct args."""
        mock_post.return_value = MagicMock(ok=True)

        callback_query = _make_callback_update()["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        bot_with_resolver._action_card_resolver.resolve.assert_called_once_with(
            "abcdef12", "approve", channel="telegram"
        )

    @patch("telegram_bot.requests.post")
    def test_answers_callback_after_resolve(self, mock_post, bot_with_resolver):
        """Should call answerCallbackQuery to stop the loading spinner."""
        mock_post.return_value = MagicMock(ok=True)

        callback_query = _make_callback_update()["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        # answerCallbackQuery should have been called
        calls = mock_post.call_args_list
        methods_called = [
            c.kwargs.get("json", {}).get("callback_query_id") or
            c[1].get("json", {}).get("callback_query_id")
            for c in calls
        ]
        assert "cb-111" in methods_called

    @patch("telegram_bot.requests.post")
    def test_edits_message_after_resolve(self, mock_post, bot_with_resolver):
        """Should edit the original message to show resolution status."""
        mock_post.return_value = MagicMock(ok=True)

        callback_query = _make_callback_update()["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        # Should have called editMessageText (at least one call with message_id)
        edit_calls = [
            c for c in mock_post.call_args_list
            if "editMessageText" in str(c)
        ]
        assert len(edit_calls) >= 1

    @patch("telegram_bot.requests.post")
    def test_security_gate_rejects_wrong_chat(self, mock_post, bot_with_resolver):
        """Should reject callbacks from unauthorized chats."""
        mock_post.return_value = MagicMock(ok=True)

        update = _make_callback_update(chat_id="666777")
        callback_query = update["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        # Resolver should NOT be called
        bot_with_resolver._action_card_resolver.resolve.assert_not_called()

    @patch("telegram_bot.requests.post")
    def test_ignores_non_actioncard_callbacks(self, mock_post, bot_with_resolver):
        """Should skip callbacks not starting with 'ac:'."""
        mock_post.return_value = MagicMock(ok=True)

        update = _make_callback_update(data="some_other_data")
        callback_query = update["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        bot_with_resolver._action_card_resolver.resolve.assert_not_called()

    @patch("telegram_bot.requests.post")
    def test_handles_malformed_callback_data(self, mock_post, bot_with_resolver):
        """Should handle callback_data with wrong number of parts."""
        mock_post.return_value = MagicMock(ok=True)

        update = _make_callback_update(data="ac:only_one_part")
        callback_query = update["callback_query"]
        bot_with_resolver._handle_callback_query(callback_query)

        bot_with_resolver._action_card_resolver.resolve.assert_not_called()

    @patch("telegram_bot.requests.post")
    def test_no_resolver_available(self, mock_post, bot):
        """Should handle missing resolver gracefully."""
        mock_post.return_value = MagicMock(ok=True)

        callback_query = _make_callback_update()["callback_query"]
        # bot has no _action_card_resolver attribute
        bot._handle_callback_query(callback_query)
        # Should not raise


# ---------------------------------------------------------------------------
# _handle_update routing tests
# ---------------------------------------------------------------------------

class TestHandleUpdateRouting:
    """Tests that _handle_update correctly routes callback_query updates."""

    @patch("telegram_bot.requests.post")
    def test_callback_query_routed(self, mock_post, bot_with_resolver):
        """callback_query updates should be routed to _handle_callback_query."""
        mock_post.return_value = MagicMock(ok=True)

        update = _make_callback_update()
        bot_with_resolver._handle_update(update)

        # Resolver should have been called (proving callback was routed)
        bot_with_resolver._action_card_resolver.resolve.assert_called_once()

    @patch("telegram_bot.requests.post")
    def test_callback_query_updates_offset(self, mock_post, bot_with_resolver):
        """callback_query should still advance the polling offset."""
        mock_post.return_value = MagicMock(ok=True)

        update = _make_callback_update()
        bot_with_resolver._handle_update(update)

        assert bot_with_resolver._offset == 101  # update_id(100) + 1


# ---------------------------------------------------------------------------
# _on_actioncard_event tests
# ---------------------------------------------------------------------------

class TestOnActioncardEvent:
    """Tests for event-driven ActionCard presentation to Telegram."""

    @patch("telegram_bot.requests.post")
    def test_sends_card_with_keyboard(self, mock_post, bot_with_resolver):
        """Should send a message with inline keyboard from event payload."""
        mock_post.return_value = MagicMock(
            ok=True,
            json=lambda: {"result": {"message_id": 77}},
        )

        event = MagicMock()
        event.payload = {
            "card_id": "abcdef12-3456-7890-abcd-ef1234567890",
            "title": "Approve Deploy",
            "description": "Deploy v2",
            "source_system": "governance",
            "buttons": [
                {"id": "approve", "label": "Approve", "style": "primary",
                 "callback_data": "", "requires_confirmation": False},
                {"id": "deny", "label": "Deny", "style": "danger",
                 "callback_data": "", "requires_confirmation": False},
            ],
        }

        asyncio.get_event_loop().run_until_complete(
            bot_with_resolver._on_actioncard_event(event)
        )

        # Should have sent a message (sendMessage call)
        assert mock_post.called
        # Store should have recorded the message_id
        bot_with_resolver._action_card_store.set_telegram_message_id.assert_called_once_with(
            "abcdef12-3456-7890-abcd-ef1234567890", 77
        )

    @patch("telegram_bot.requests.post")
    def test_handles_send_failure(self, mock_post, bot_with_resolver):
        """Should handle failure to send without raising."""
        mock_post.return_value = MagicMock(ok=False)

        event = MagicMock()
        event.payload = {
            "card_id": "abcdef12-0000-0000-0000-000000000000",
            "title": "Test",
            "description": "",
            "source_system": "test",
            "buttons": [],
        }

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            bot_with_resolver._on_actioncard_event(event)
        )
        # Store should NOT have been called (send failed)
        bot_with_resolver._action_card_store.set_telegram_message_id.assert_not_called()


# ---------------------------------------------------------------------------
# _on_actioncard_resolved_event tests
# ---------------------------------------------------------------------------

class TestOnActioncardResolvedEvent:
    """Tests for cross-channel resolution sync."""

    @patch("telegram_bot.requests.post")
    def test_edits_message_on_warroom_resolve(self, mock_post, bot):
        """Should edit Telegram message when card is resolved from War Room."""
        mock_post.return_value = MagicMock(ok=True)

        event = MagicMock()
        event.payload = {
            "card_id": "abc123",
            "button_id": "approve",
            "channel": "warroom",
            "source_system": "governance",
            "source_item_id": "req-1",
            "result": {"status": "approved", "message": "Done"},
            "telegram_message_id": 42,
        }

        asyncio.get_event_loop().run_until_complete(
            bot._on_actioncard_resolved_event(event)
        )

        # Should have called editMessageText
        assert mock_post.called
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["message_id"] == 42
        assert "APPROVED" in payload["text"]

    def test_skips_telegram_channel(self, bot):
        """Should skip when resolution came from Telegram (already handled)."""
        event = MagicMock()
        event.payload = {
            "channel": "telegram",
            "telegram_message_id": 42,
        }

        # Should return without doing anything (no HTTP call)
        asyncio.get_event_loop().run_until_complete(
            bot._on_actioncard_resolved_event(event)
        )

    def test_skips_without_message_id(self, bot):
        """Should skip when no telegram_message_id is present."""
        event = MagicMock()
        event.payload = {
            "channel": "warroom",
            "telegram_message_id": None,
        }

        asyncio.get_event_loop().run_until_complete(
            bot._on_actioncard_resolved_event(event)
        )
