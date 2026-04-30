"""
Tests for TelegramConnector — Telegram Bot API integration.

Tests HTTP request spec production. No actual Telegram API calls.
"""

from unittest.mock import MagicMock

import pytest

from src.connectors.connectors.telegram import TelegramConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def telegram():
    return TelegramConnector()


class TestManifest:
    def test_validates(self, telegram):
        telegram.manifest.validate()

    def test_target_domains(self, telegram):
        assert telegram.manifest.target_domains == ["api.telegram.org"]

    def test_required_credentials(self, telegram):
        assert len(telegram.manifest.required_credentials) == 2
        assert telegram.manifest.required_credentials[0].vault_key == "telegram.bot_token"
        assert telegram.manifest.required_credentials[1].vault_key == "telegram.chat_id"
        assert telegram.manifest.required_credentials[1].required is False


class TestOperations:
    def test_total_operations(self, telegram):
        assert len(telegram.get_operations()) == 8

    def test_read_write_delete_counts(self, telegram):
        ops = telegram.get_operations()
        assert len([o for o in ops if o.capability == "connector.read"]) == 4
        assert len([o for o in ops if o.capability == "connector.write"]) == 3
        assert len([o for o in ops if o.capability == "connector.delete"]) == 1

    def test_risk_tiers(self, telegram):
        ops = {o.id: o for o in telegram.get_operations()}
        assert ops["get_updates"].default_tier == RiskTier.T0_INERT
        assert ops["get_file"].default_tier == RiskTier.T1_REVERSIBLE
        assert ops["send_message"].default_tier == RiskTier.T1_REVERSIBLE
        assert ops["delete_message"].default_tier == RiskTier.T3_IRREVERSIBLE


class TestExecute:
    def test_get_updates_request(self, telegram):
        result = telegram.execute("get_updates", {"offset": 5, "timeout": 10, "limit": 20})

        assert result.method == HTTPMethod.GET
        assert "getUpdates?offset=5&timeout=10&limit=20" in result.url
        assert result.credential_vault_key == "telegram.bot_token"
        assert result.metadata["auth_type"] == "url_token"

    def test_get_me_request(self, telegram):
        result = telegram.execute("get_me", {})

        assert result.method == HTTPMethod.GET
        assert result.url.endswith("{token}/getMe")

    def test_get_chat_request(self, telegram):
        result = telegram.execute("get_chat", {"chat_id": "12345"})

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("{token}/getChat")
        assert result.body == {"chat_id": "12345"}

    def test_get_file_request(self, telegram):
        result = telegram.execute("get_file", {"file_id": "FILE123"})

        assert result.method == HTTPMethod.GET
        assert "getFile?file_id=FILE123" in result.url

    def test_send_message_request_with_parse_mode(self, telegram):
        result = telegram.execute(
            "send_message",
            {"chat_id": "12345", "text": "hello", "parse_mode": "HTML"},
        )

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("{token}/sendMessage")
        assert result.body == {"chat_id": "12345", "text": "hello", "parse_mode": "HTML"}

    def test_send_voice_request(self, telegram):
        result = telegram.execute(
            "send_voice",
            {"chat_id": "12345", "voice_url": "https://example.com/voice.ogg"},
        )

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("{token}/sendVoice")
        assert result.body == {
            "chat_id": "12345",
            "voice": "https://example.com/voice.ogg",
        }

    def test_send_photo_request_with_caption(self, telegram):
        result = telegram.execute(
            "send_photo",
            {
                "chat_id": "12345",
                "photo_url": "https://example.com/photo.jpg",
                "caption": "release proof",
            },
        )

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("{token}/sendPhoto")
        assert result.body == {
            "chat_id": "12345",
            "photo": "https://example.com/photo.jpg",
            "caption": "release proof",
        }

    def test_delete_message_request(self, telegram):
        result = telegram.execute(
            "delete_message",
            {"chat_id": "12345", "message_id": 42},
        )

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("{token}/deleteMessage")
        assert result.body == {"chat_id": "12345", "message_id": 42}

    def test_unknown_operation_raises(self, telegram):
        with pytest.raises(KeyError):
            telegram.execute("unknown_operation", {})


class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, telegram):
        assert telegram.validate_credentials() is False

    def test_validate_with_vault_uses_bot_token_key(self):
        vault = MagicMock()
        vault.exists.return_value = True
        telegram = TelegramConnector(vault=vault)

        assert telegram.validate_credentials() is True
        vault.exists.assert_called_once_with("telegram.bot_token")
