from tool_loop_completion import (
    claims_completion,
    completion_contract_note,
    find_successful_tool_receipt,
    tool_target_key,
    unresolved_tool_failures,
)


def test_tool_target_key_identifies_repo_writer_targets(monkeypatch):
    monkeypatch.setenv("LANCELOT_WORKSPACE", "/workspace")

    assert (
        tool_target_key("repo_writer", {"action": "create", "path": "README.md"})
        == "repo_writer:/workspace:create:README.md"
    )
    assert tool_target_key("repo_writer", {"action": "edit"}) == "repo_writer:input_validation"


def test_tool_target_key_identifies_command_and_network_targets():
    assert tool_target_key("command_runner", {"command": "pytest -q"}) == "command_runner:pytest:pytest -q"
    assert tool_target_key("network_client", {"url": "https://example.com"}) == (
        "network_client:GET:https://example.com"
    )
    assert tool_target_key("network_client", {}) == "network_client:input_validation"


def test_unresolved_tool_failures_clears_corrected_input_validation_failure():
    receipts = [
        {
            "skill": "repo_writer",
            "inputs": {"action": "create"},
            "result": "REJECTED - repo_writer missing required input: path",
        },
        {
            "skill": "repo_writer",
            "inputs": {"action": "create", "path": "README.md"},
            "result": "SUCCESS",
        },
    ]

    assert unresolved_tool_failures(receipts) == []


def test_unresolved_tool_failures_keeps_failed_target_without_success():
    failed = {
        "skill": "command_runner",
        "inputs": {"command": "type README.md"},
        "result": "FAILED: Windows shell command in POSIX runtime",
    }

    assert unresolved_tool_failures([failed]) == [failed]


def test_find_successful_tool_receipt_matches_exact_tool_target():
    first = {
        "skill": "repo_writer",
        "inputs": {"action": "create", "path": "one.txt", "workspace": "/workspace"},
        "result": "SUCCESS",
        "outputs": {"path": "/workspace/one.txt"},
    }
    second = {
        "skill": "repo_writer",
        "inputs": {"action": "create", "path": "two.txt", "workspace": "/workspace"},
        "result": "SUCCESS",
    }

    assert find_successful_tool_receipt(
        [first, second],
        "repo_writer",
        {"action": "create", "path": "one.txt", "workspace": "/workspace"},
    ) == first
    assert find_successful_tool_receipt(
        [first, second],
        "repo_writer",
        {"action": "edit", "path": "one.txt", "workspace": "/workspace"},
    ) is None


def test_claims_completion_ignores_explicitly_blocked_or_failed_language():
    assert claims_completion("Done, I created the file.") is True
    assert claims_completion("I could not complete this because approval is pending.") is False
    assert claims_completion("The command failed, so this is incomplete.") is False


def test_completion_contract_note_summarizes_targets_and_overflow():
    unresolved = [
        {"skill": "repo_writer", "inputs": {"path": "a.txt"}, "result": "FAILED: missing content"},
        {"skill": "command_runner", "inputs": {"command": "pytest"}, "result": "EXCEPTION: boom"},
        {"skill": "network_client", "inputs": {"url": "https://example.com"}, "result": "FAILED: 500"},
        {"skill": "repo_writer", "inputs": {"path": "b.txt"}, "result": "ESCALATED - approval"},
    ]

    note = completion_contract_note(unresolved)

    assert "- repo_writer on `a.txt`: FAILED: missing content" in note
    assert "- command_runner on `pytest`: EXCEPTION: boom" in note
    assert "- network_client on `https://example.com`: FAILED: 500" in note
    assert "- plus 1 more unresolved tool issue(s)" in note
