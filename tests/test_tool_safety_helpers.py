from src.core.orch_helpers.safety_helpers import classify_tool_call_safety


def test_read_only_inspection_commands_do_not_require_approval():
    assert classify_tool_call_safety("command_runner", {"command": "more ticket_sentinel/README.md"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "rg -n Ticket src tests"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "sed -n '1,80p' src/core/tool_loop.py"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "git show --stat HEAD"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "git -C /home/lancelot/workspace/lancelot status --short"}) == "auto"


def test_write_like_inspection_binaries_still_require_approval():
    assert classify_tool_call_safety("command_runner", {"command": "sed -i s/a/b/ src/file.py"}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": "find . -delete"}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": "git checkout main"}) == "escalate"


def test_repo_writer_edit_stays_auto_outside_warroom():
    assert classify_tool_call_safety(
        "repo_writer",
        {"action": "edit", "path": "README.md"},
        channel="api",
    ) == "auto"


def test_repo_writer_edit_requires_approval_in_warroom():
    assert classify_tool_call_safety(
        "repo_writer",
        {"action": "edit", "path": "README.md"},
        channel="warroom",
    ) == "escalate"
