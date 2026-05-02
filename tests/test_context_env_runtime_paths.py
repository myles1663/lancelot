import subprocess
from types import SimpleNamespace

import src.core.context_env as context_env_module
from src.core.context_env import ContextEnvironment


class _Receipt:
    def __init__(self, action_name):
        self.action_name = action_name
        self.completed = None
        self.failed = None

    def complete(self, outputs, duration_ms, token_count=None):
        self.completed = {
            "outputs": outputs,
            "duration_ms": duration_ms,
            "token_count": token_count,
        }
        return self

    def fail(self, error, duration_ms):
        self.failed = {"error": error, "duration_ms": duration_ms}
        return self


class _ReceiptService:
    def __init__(self, listed=None):
        self.created = []
        self.updated = []
        self._listed = listed or []

    def create(self, receipt):
        self.created.append(receipt)

    def update(self, receipt):
        self.updated.append(receipt)

    def list(self, limit=10):
        return self._listed[:limit]


def _env(tmp_path, monkeypatch, listed=None):
    monkeypatch.setattr(
        context_env_module,
        "create_receipt",
        lambda _action_type, action_name, *_args, **_kwargs: _Receipt(action_name),
    )
    env = ContextEnvironment(str(tmp_path))
    env.receipt_service = _ReceiptService(listed=listed)
    return env


def test_read_file_records_success_and_guards_failure_modes(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    env.set_current_quest_id("quest-1")
    target = tmp_path / "note.txt"
    target.write_text("hello context", encoding="utf-8")

    assert env.read_file("note.txt", parent_id="parent-1") == "hello context"
    assert env.items["note.txt"].content == "hello context"
    assert env.current_tokens > 0
    assert env.receipt_service.created[-1].action_name == "read_context"
    assert env.receipt_service.updated[-1].completed["outputs"]["tokens"] > 0

    assert env.read_file("../outside.txt") is None
    assert env.receipt_service.created[-1].action_name == "read_context_blocked"

    assert env.read_file("missing.txt") is None
    assert env.receipt_service.created[-1].completed["outputs"] == {"status": "blocked"}

    monkeypatch.setattr(context_env_module, "MAX_FILES_IN_CONTEXT", 1)
    assert env.read_file("note.txt") is None
    assert env.receipt_service.created[-1].action_name == "read_context_blocked"


def test_read_file_token_limit_and_io_failure_are_receipted(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    target = tmp_path / "large.txt"
    target.write_text("x" * 128, encoding="utf-8")
    monkeypatch.setattr(context_env_module, "MAX_CONTEXT_TOKENS", 1)

    assert env.read_file("large.txt") is None
    assert env.receipt_service.updated[-1].failed["error"] == "Context Token Limit Exceeded"

    monkeypatch.setattr(context_env_module, "MAX_CONTEXT_TOKENS", 128000)
    import builtins

    original_open = builtins.open

    def _raising_open(path, *args, **kwargs):
        if str(path).endswith("large.txt"):
            raise OSError("disk read failed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)
    assert env.read_file("large.txt") is None
    assert "disk read failed" in env.receipt_service.updated[-1].failed["error"]


def test_context_receipts_history_and_clear_are_rendered(tmp_path, monkeypatch):
    listed = [
        SimpleNamespace(
            status="success",
            timestamp="2026-04-30T10:00:00Z",
            action_name="read_context",
            action_type="file_op",
            inputs={"path": "a" * 250},
            outputs={"result": "b" * 250},
        ),
        SimpleNamespace(
            status="failure",
            timestamp="2026-04-30T10:01:00Z",
            action_name="command_runner",
            action_type="tool_call",
            inputs={"command": "pytest"},
            outputs={},
        ),
    ]
    env = _env(tmp_path, monkeypatch, listed=listed)
    env.add_history("user", "[via warroom] inspect the receipt trail")
    env.items["note.txt"] = SimpleNamespace(content="file context")
    env.current_tokens = 12

    rendered = env.get_context_string(channel="warroom")

    assert "--- BEGIN FILE CONTEXT ---" in rendered
    assert "read_context" in rendered
    assert "inspect the receipt trail" in rendered

    env.clear()
    assert env.items == {}
    assert env.current_tokens == 0

    env.receipt_service.list = lambda limit=10: (_ for _ in ()).throw(RuntimeError("db offline"))
    assert env.get_recent_receipts() == "Error fetching receipts: db offline"


def test_workspace_search_limit_no_match_and_top_level_failure(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    for index in range(3):
        (tmp_path / f"hit-{index}.txt").write_text(f"needle {index}", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("needle hidden", encoding="utf-8")
    (tmp_path / "skip.json").write_text("needle json", encoding="utf-8")

    limited = env.search_workspace("needle", limit=2)

    assert limited.count("[MATCH]") == 2
    assert ".hidden" not in limited
    assert "skip.json" not in limited
    assert env.search_workspace("absent") == "No matches found."

    monkeypatch.setattr(context_env_module.os, "walk", lambda _path: (_ for _ in ()).throw(OSError("walk failed")))
    assert env.search_workspace("needle") == "Search failed: walk failed"
    assert "walk failed" in env.receipt_service.updated[-1].failed["error"]


def test_file_outline_python_text_missing_and_read_failure(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    py_file = tmp_path / "module.py"
    py_file.write_text(
        'class Service:\n    """Service docs."""\n    def run(self):\n        pass\n\ndef helper():\n    """Helper docs."""\n    pass\n',
        encoding="utf-8",
    )
    text_file = tmp_path / "note.md"
    text_file.write_text("\n".join(str(i) for i in range(20)), encoding="utf-8")

    outline = env.get_file_outline("module.py")
    assert "class Service" in outline
    assert "def run(...)" in outline
    assert "def helper(...) # Helper docs." in outline
    assert "(Text File) Showing first 10 lines:" in env.get_file_outline("note.md")
    assert env.get_file_outline("../outside.py") == "File access error."

    import builtins

    original_open = builtins.open

    def _raising_open(path, *args, **kwargs):
        if str(path).endswith("module.py"):
            raise OSError("outline read failed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)
    assert env.get_file_outline("module.py") == "Outline failed: outline read failed"


def test_workspace_diff_success_staged_truncation_and_errors(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    calls = []

    def _check_output(cmd, cwd, text, stderr):
        calls.append(cmd)
        if cmd[:2] == ["git", "status"]:
            return " M README.md\n"
        if cmd == ["git", "diff", "HEAD"]:
            return "x" * 6000
        if cmd == ["git", "diff", "--name-status", "--cached"]:
            return "M\tREADME.md\n"
        if cmd == ["git", "diff", "--cached"]:
            return "cached diff"
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "check_output", _check_output)

    diff = env.get_workspace_diff()
    assert "--- GIT STATUS ---" in diff
    assert "... [DIFF TRUNCATED]" in diff
    assert env.get_workspace_diff(staged=True).endswith("cached diff")
    assert ["git", "diff", "--name-status", "--cached"] in calls

    def _called_process_error(cmd, cwd, text, stderr):
        raise subprocess.CalledProcessError(1, cmd, output="not a git repo")

    monkeypatch.setattr(subprocess, "check_output", _called_process_error)
    assert env.get_workspace_diff().startswith("Git Error: not a git repo")

    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert env.get_workspace_diff() == "Diff failed: boom"


def test_list_workspace_success_guards_and_failure(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    (tmp_path / "folder").mkdir()
    (tmp_path / "note.txt").write_text("note", encoding="utf-8")
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")

    listing = env.list_workspace(".")

    assert "[DIR] folder/" in listing
    assert "[FILE] note.txt" in listing
    assert ".hidden" not in listing
    assert env.list_workspace("../outside") == "Access Denied: Path Traversal Detected"
    assert env.list_workspace("missing") == "Directory not found."

    monkeypatch.setattr(context_env_module.os, "listdir", lambda _path: (_ for _ in ()).throw(OSError("list failed")))
    assert env.list_workspace(".") == "List failed: list failed"
