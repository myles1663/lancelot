import pytest

from src.core.outbound_http import OutboundNetworkError
from src.integrations.telegram_bot import TelegramBot
from src.ui.onboarding import OnboardingOrchestrator


def _blocked(*args, **kwargs):
    raise OutboundNetworkError("blocked by network allowlist")


def test_onboarding_api_key_validation_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr("src.ui.onboarding.assert_url_allowed", _blocked)
    orchestrator = OnboardingOrchestrator(data_dir=str(tmp_path))

    result = orchestrator._validate_api_key_live("openai", "sk-test")

    assert result["valid"] is False
    assert "network allowlist" in result["error"]


def test_onboarding_telegram_handshake_reports_allowlist_block(monkeypatch, tmp_path):
    monkeypatch.setattr("src.ui.onboarding.assert_url_allowed", _blocked)
    orchestrator = OnboardingOrchestrator(data_dir=str(tmp_path))
    orchestrator.temp_data["telegram_token"] = "123456:ABCDEF"
    orchestrator.temp_data["telegram_chat_id"] = "999888"

    result = orchestrator._initiate_handshake("telegram")

    assert "Connection Blocked" in result
    assert "network allowlist" in result


def test_telegram_send_message_with_keyboard_returns_none_when_allowlist_blocks(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram_bot.assert_url_allowed", _blocked)
    monkeypatch.setattr("src.integrations.telegram_bot.time.sleep", lambda _: None)
    bot = TelegramBot(orchestrator=None)
    bot.token = "123456:ABCDEF"
    bot.chat_id = "999888"

    assert bot.send_message_with_keyboard("Test", keyboard=None) is None


def test_telegram_download_file_raises_when_allowlist_blocks(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram_bot.assert_url_allowed", _blocked)
    bot = TelegramBot(orchestrator=None)
    bot.token = "123456:ABCDEF"
    bot.chat_id = "999888"

    with pytest.raises(OutboundNetworkError, match="network allowlist"):
        bot._download_file("file-1")
