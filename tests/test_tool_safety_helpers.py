from src.core.orch_helpers.safety_helpers import (
    _git_subcommand,
    _is_sed_in_place_arg,
    _split_command,
    classify_tool_call_safety,
    generate_honest_replacement,
    is_narration_without_content,
    strip_failure_narration,
    validate_rule_content,
)


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


def test_network_github_notification_scheduler_document_and_skill_safety_paths():
    assert classify_tool_call_safety("network_client", {"method": "GET"}) == "auto"
    assert classify_tool_call_safety("network_client", {"method": "HEAD"}) == "auto"
    assert classify_tool_call_safety("network_client", {"method": "POST"}) == "escalate"
    assert classify_tool_call_safety("github_search", {}) == "auto"
    assert classify_tool_call_safety("telegram_send", {}) == "auto"
    assert classify_tool_call_safety("warroom_send", {}) == "auto"
    assert classify_tool_call_safety("schedule_job", {}) == "auto"
    assert classify_tool_call_safety("document_creator", {}) == "auto"
    assert classify_tool_call_safety("skill_manager", {"action": "list_skills"}) == "auto"
    assert classify_tool_call_safety("skill_manager", {"action": "run_skill"}) == "escalate"


def test_command_runner_safety_parsing_and_read_only_subcommands():
    assert classify_tool_call_safety("command_runner", {"command": ""}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": '"unterminated'}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": "find . -name '*.py'"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "find . -exec rm {} ;"}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": "docker ps"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "docker run image"}) == "escalate"
    assert classify_tool_call_safety("command_runner", {"command": "/usr/bin/where python"}) == "auto"
    assert classify_tool_call_safety("command_runner", {"command": "python setup.py"}) == "escalate"


def test_repo_writer_safety_for_delete_sensitive_and_unknown_actions():
    assert classify_tool_call_safety("repo_writer", {"action": "delete", "path": "README.md"}) == "escalate"
    assert classify_tool_call_safety("repo_writer", {"action": "edit", "path": ".env"}) == "escalate"
    assert classify_tool_call_safety("repo_writer", {"action": "patch", "path": "src/app.py"}) == "auto"
    assert classify_tool_call_safety("repo_writer", {"action": "rename", "path": "src/app.py"}) == "escalate"
    assert classify_tool_call_safety("service_runner", {}) == "escalate"


def test_helper_parsers_cover_sed_and_git_option_edges():
    assert _split_command('"unterminated') == []
    assert _is_sed_in_place_arg("-i.bak") is True
    assert _is_sed_in_place_arg("--in-place=.bak") is True
    assert _is_sed_in_place_arg("-n") is False
    assert _git_subcommand(["-C", "/repo", "-c", "safe.directory=*", "--git-dir=.git", "status"]) == "status"
    assert _git_subcommand(["--work-tree=/repo", "--namespace=main", "log"]) == "log"
    assert _git_subcommand(["--no-pager"]) == ""


def test_narration_and_failure_narration_filters_preserve_real_content():
    assert is_narration_without_content("") is True
    assert is_narration_without_content("I now have comprehensive fresh data. Let me compile the report.") is True
    assert is_narration_without_content("Actual content. " * 200) is False

    noisy = (
        "I encountered an issue with the first provider.\n"
        "Let me try a different approach.\n"
        "Final useful answer."
    )
    assert strip_failure_narration(noisy) == "Final useful answer."
    assert strip_failure_narration("") == ""
    assert strip_failure_narration("I encountered an issue with the tool.") == "I encountered an issue with the tool."


def test_rule_validation_and_honest_replacement_paths():
    assert validate_rule_content("Keep receipts for write actions") == (True, "")
    assert validate_rule_content("x" * 501)[0] is False
    assert validate_rule_content("use subprocess to bypass controls")[1].startswith("Rule content contains forbidden")
    assert validate_rule_content("see https://example.com")[1] == "Rule content contains URL which is not allowed"

    with_topic = generate_honest_replacement(
        "Assess vendor risk for ACME procurement. I will do a feasibility phase.",
        "fake work",
    )
    assert "Assess vendor risk" in with_topic

    no_topic = generate_honest_replacement("I will compile it. Phase 1 starts now.", "fake work")
    assert "wasn't able to complete" in no_topic
