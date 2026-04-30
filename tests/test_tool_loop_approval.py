from tool_loop_approval import (
    approval_context,
    approval_group_reason,
    approval_reason,
    looks_like_pending_approval_response,
    pending_approval_response,
    tool_input_error,
)


def test_pending_approval_response_uses_group_context_for_multiple_actions():
    response = pending_approval_response("repo_writer", "group-1", approval_count=3)

    assert "Paused for Commander approval before running 3 governed actions." in response
    assert "Approval group ID: `group-1`." in response
    assert "Continue control to resume the same run" in response
    assert looks_like_pending_approval_response(response) is True


def test_approval_context_summarizes_user_request_and_input_fields():
    context = approval_context(
        "please update the README and docs",
        "repo_writer",
        {"path": "README.md", "action": "edit", "content": "new text"},
    )

    assert context == (
        "User request: please update the README and docs. "
        "Requested governed tool: repo_writer. "
        "Input fields present: action, content, path."
    )


def test_approval_reason_uses_domain_specific_language():
    assert approval_reason("repo_writer") == (
        "This can create or modify files in the workspace or repository."
    )
    assert approval_reason("network_client") == (
        "This can reach external systems or move data across a connector boundary."
    )


def test_approval_group_reason_names_repo_write_grouping():
    reason = approval_group_reason([
        {"tool_name": "repo_writer", "params": {"path": "README.md"}},
        {"tool_name": "repo_writer", "params": {"path": "docs/architecture.md"}},
    ])

    assert reason == (
        "This grouped approval covers multiple bounded file changes for the same user request."
    )


def test_tool_input_error_identifies_required_missing_fields():
    assert tool_input_error("repo_writer", {"action": "edit"}) == (
        "repo_writer missing required input(s): path."
    )
    assert tool_input_error("network_client", {"method": "GET"}) == (
        "network_client missing required input(s): url."
    )
    assert tool_input_error("command_runner", {"command": "pytest -q"}) == ""
    assert "blocked shell metacharacter" in tool_input_error(
        "command_runner",
        {"command": "pytest -q; git status"},
    )
    assert tool_input_error("repo_writer", "bad-input") == (
        "repo_writer inputs must be a JSON object."
    )
