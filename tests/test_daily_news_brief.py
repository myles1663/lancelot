from src.core.skills.builtins import daily_news_brief, telegram_send


def test_daily_news_brief_skips_delivery_when_telegram_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_news_brief, "_RSS_FEEDS", [])
    monkeypatch.setattr(daily_news_brief, "_GOOGLE_NEWS_QUERIES", [])
    monkeypatch.setattr(
        telegram_send,
        "send_text",
        lambda message, chat_id_override=None: {
            "status": "error",
            "error": "Telegram not configured. Set LANCELOT_TELEGRAM_TOKEN and LANCELOT_TELEGRAM_CHAT_ID.",
        },
    )
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))

    result = daily_news_brief.execute(None, {"max_articles": 1})

    assert result["status"] == "skipped"
    assert result["delivery"] == "local_artifact"
    assert result["local_brief_path"]
    assert "AI News Briefing" in (tmp_path / "news_briefs").glob("*.md").__next__().read_text()


def test_daily_news_brief_preserves_real_telegram_errors(monkeypatch):
    monkeypatch.setattr(daily_news_brief, "_RSS_FEEDS", [])
    monkeypatch.setattr(daily_news_brief, "_GOOGLE_NEWS_QUERIES", [])
    monkeypatch.setattr(
        telegram_send,
        "send_text",
        lambda message, chat_id_override=None: {
            "status": "error",
            "error": "Telegram API rejected message",
        },
    )

    result = daily_news_brief.execute(None, {"max_articles": 1})

    assert result["status"] == "error"
    assert result["delivery"] == "local_artifact"
    assert result["local_brief_path"] is None
