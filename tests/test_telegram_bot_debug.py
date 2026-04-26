import json
from pathlib import Path
from unittest.mock import patch

from telegram_bot import TelegramBot


class _Response:
    ok = True
    text = "ok"


@patch("telegram_bot.requests.post", return_value=_Response())
def test_send_message_does_not_write_debug_dump_without_flag(mock_post, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANCELOT_TELEGRAM_DEBUG_DUMP", raising=False)

    bot = TelegramBot(orchestrator=None)
    bot.token = "token"
    bot.chat_id = "chat-1"
    bot.send_message("hello world")

    debug_dir = Path(tmp_path) / "chat" / "debug"
    assert not debug_dir.exists()


@patch("telegram_bot.requests.post", return_value=_Response())
def test_send_message_writes_debug_dump_when_enabled(mock_post, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LANCELOT_TELEGRAM_DEBUG_DUMP", "true")

    bot = TelegramBot(orchestrator=None)
    bot.token = "token"
    bot.chat_id = "chat-1"
    bot.send_message("hello governed telegram")

    debug_dir = Path(tmp_path) / "chat" / "debug"
    text_path = debug_dir / "telegram_last_outbound_message.txt"
    meta_path = debug_dir / "telegram_last_outbound_message.json"

    assert text_path.read_text(encoding="utf-8") == "hello governed telegram"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["target_chat_id"] == "chat-1"
    assert metadata["length"] == len("hello governed telegram")
    assert metadata["chunk_count"] == 1
