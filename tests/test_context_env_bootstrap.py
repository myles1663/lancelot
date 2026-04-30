import logging
from pathlib import Path

from src.core.context_env import ContextEnvironment


def test_context_env_seeds_bootstrap_files(tmp_data_dir):
    env = ContextEnvironment(str(tmp_data_dir))

    rules_path = tmp_data_dir / "RULES.md"
    capabilities_path = tmp_data_dir / "CAPABILITIES.md"

    assert rules_path.exists()
    assert capabilities_path.exists()
    assert "Lancelot Operating Rules" in rules_path.read_text(encoding="utf-8")
    assert "Lancelot Capabilities" in capabilities_path.read_text(encoding="utf-8")

    rules_content = env.read_file("RULES.md")
    capabilities_content = env.read_file("CAPABILITIES.md")

    assert "Lancelot Operating Rules" in rules_content
    assert "Lancelot Capabilities" in capabilities_content


def test_context_env_logs_bad_chat_history(caplog, tmp_data_dir):
    chat_dir = tmp_data_dir / "chat"
    chat_dir.mkdir()
    (chat_dir / "chat_log.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        env = ContextEnvironment(str(tmp_data_dir))

    assert env.history == []
    assert "Error loading chat history" in caplog.text


def test_context_env_search_logs_unreadable_file_skip(monkeypatch, caplog, tmp_data_dir):
    env = ContextEnvironment(str(tmp_data_dir))
    unreadable = tmp_data_dir / "unreadable.txt"
    readable = tmp_data_dir / "readable.txt"
    unreadable.write_text("secret", encoding="utf-8")
    readable.write_text("needle in a haystack", encoding="utf-8")

    import builtins

    original_open = builtins.open

    def _raising_open(path, *args, **kwargs):
        if Path(path) == unreadable:
            raise OSError("permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)

    with caplog.at_level(logging.DEBUG):
        result = env.search_workspace("needle")

    assert "[MATCH] readable.txt" in result
    assert "ContextEnvironment skipped unreadable file" in caplog.text


def test_context_env_outline_logs_ast_parse_fallback(caplog, tmp_data_dir):
    env = ContextEnvironment(str(tmp_data_dir))
    broken = tmp_data_dir / "broken.py"
    broken.write_text("def nope(:\n    pass\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG):
        result = env.get_file_outline(str(broken))

    assert "(AST Parse Failed) Showing first 10 lines:" in result
    assert "ContextEnvironment AST parse failed" in caplog.text
