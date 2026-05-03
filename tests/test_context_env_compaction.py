import json

import src.core.context_env as context_env_module
from src.core.context_env import ContextEnvironment


def _set_small_compaction_window(monkeypatch, max_messages=6, recent_keep=4, batch_size=2):
    monkeypatch.setattr(context_env_module, "MAX_CHAT_HISTORY_MESSAGES", max_messages)
    monkeypatch.setattr(context_env_module, "CHAT_HISTORY_RECENT_KEEP", recent_keep)
    monkeypatch.setattr(context_env_module, "CHAT_HISTORY_COMPACT_BATCH", batch_size)
    monkeypatch.setattr(context_env_module, "MAX_CHAT_SUMMARIES", 10)


def test_chat_history_compacts_old_messages_without_discarding_context(monkeypatch, tmp_data_dir):
    _set_small_compaction_window(monkeypatch)
    env = ContextEnvironment(str(tmp_data_dir))

    for index in range(8):
        if index % 2 == 0:
            env.add_history("user", f"[via warroom] Continue task segment {index}")
        else:
            env.add_history("assistant", f"Completed task segment {index}")

    assert len(env.history) <= context_env_module.MAX_CHAT_HISTORY_MESSAGES
    assert env.chat_summaries

    summary_path = tmp_data_dir / "chat" / "chat_summaries.json"
    summaries = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summaries[0]["source"] == "deterministic_chat_compaction"
    assert "Continue task segment 0" in summaries[0]["summary"]

    rendered = env.get_history_string(limit=10, channel="warroom")
    assert "--- COMPACTED CHAT HISTORY ---" in rendered
    assert "--- RECENT CHAT HISTORY ---" in rendered
    assert "Continue task segment 0" in rendered
    assert "Completed task segment 7" in rendered

    reloaded = ContextEnvironment(str(tmp_data_dir))
    assert reloaded.chat_summaries


def test_chat_history_compaction_keeps_channel_summaries_separate(monkeypatch, tmp_data_dir):
    _set_small_compaction_window(monkeypatch, max_messages=3, recent_keep=1, batch_size=10)
    env = ContextEnvironment(str(tmp_data_dir))

    env.add_history("user", "[via warroom] War Room objective")
    env.add_history("assistant", "War Room answer")
    env.add_history("user", "[via warroom] War Room follow up")
    env.add_history("assistant", "War Room second answer")
    env.add_history("user", "[via telegram] Telegram objective")
    env.add_history("assistant", "Telegram answer")
    env.add_history("user", "[via telegram] Telegram follow up")
    env.add_history("assistant", "Telegram second answer")
    env.history = []

    warroom_context = env.get_history_string(channel="warroom")
    telegram_context = env.get_history_string(channel="telegram")

    assert "War Room objective" in warroom_context
    assert "Telegram objective" not in warroom_context
    assert "Telegram objective" in telegram_context
    assert "War Room objective" not in telegram_context


def test_chat_history_compaction_bounds_summary_previews(monkeypatch, tmp_data_dir):
    _set_small_compaction_window(monkeypatch, max_messages=3, recent_keep=1, batch_size=10)
    monkeypatch.setattr(context_env_module, "CHAT_SUMMARY_PREVIEW_CHARS", 64)
    env = ContextEnvironment(str(tmp_data_dir))
    large_payload = "payload " * 500

    env.add_history("user", f"[via warroom] {large_payload}")
    env.add_history("assistant", large_payload)
    env.add_history("user", "[via warroom] keep going")
    env.add_history("assistant", "done")

    rendered = env.get_history_string(channel="warroom")

    assert "... [truncated]" in rendered
    assert len(rendered) < 2000


def test_chat_history_compaction_emits_structured_schema(monkeypatch, tmp_data_dir):
    _set_small_compaction_window(monkeypatch, max_messages=3, recent_keep=1, batch_size=10)
    env = ContextEnvironment(str(tmp_data_dir))

    env.add_history(
        "user",
        "[via warroom] I want public-neutral docs. We should not mention launch targeting. "
        "What remains unresolved? receipt-abc123",
    )
    env.add_history(
        "assistant",
        "Decision: use public-neutral release wording. Next step: update docs. "
        "Blocked action: do not publish private wording.",
    )
    env.add_history(
        "user",
        "[via warroom] Keep memory phase two focused on schema compaction and session briefs.",
    )
    env.add_history("assistant", "Done")

    summary = env.chat_summaries[0]
    rendered = env.get_history_string(channel="warroom")

    assert summary["schema_version"] == 2
    assert summary["decisions_made"]
    assert summary["user_preferences"]
    assert summary["unresolved_questions"]
    assert summary["durable_facts"]
    assert summary["rejected_or_blocked_actions"]
    assert summary["next_steps"]
    assert "receipt-abc123" in summary["receipt_references"]
    assert "Decisions:" in rendered
    assert "User Preferences:" in rendered
    assert "Rejected/Blocked Actions:" in rendered
    assert "Receipt References:" in rendered
