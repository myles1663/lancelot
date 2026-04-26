from types import SimpleNamespace

from plan_types import PlanArtifact
from orchestrator_routing import (
    build_simple_action_plan,
    check_name_update,
    is_simple_for_local,
    previous_was_substantive,
)


def test_is_simple_for_local_routes_status_to_local():
    runtime = SimpleNamespace(_is_continuation=lambda _prompt: False)

    assert is_simple_for_local(runtime, "show current health status") is True


def test_is_simple_for_local_keeps_continuations_on_frontier():
    runtime = SimpleNamespace(_is_continuation=lambda _prompt: True)

    assert is_simple_for_local(runtime, "go for it") is False


def test_is_simple_for_local_keeps_complex_requests_on_frontier():
    runtime = SimpleNamespace(_is_continuation=lambda _prompt: False)

    assert is_simple_for_local(runtime, "review the architecture and recommend a plan") is False


def test_build_simple_action_plan_returns_plan_artifact_for_single_skill_request():
    artifact = build_simple_action_plan("Create a file called notes.txt")

    assert isinstance(artifact, PlanArtifact)
    assert artifact.goal == "Create a file called notes.txt."
    assert artifact.context == ["Single-action request mapped to skill: file_writer"]
    assert artifact.plan_steps == [
        "Create a file called notes.txt",
        "Verify the operation completed successfully",
        "Report the result to the user",
    ]
    assert artifact.next_action == "Create a file called notes.txt"


def test_build_simple_action_plan_returns_none_when_request_is_not_single_skill():
    assert build_simple_action_plan("Think through the release strategy") is None


def test_check_name_update_persists_user_name_and_refreshes_context(tmp_path):
    user_file = tmp_path / "USER.md"
    user_file.write_text("- Name: Old Name\n- Role: Commander\n", encoding="utf-8")
    context_env = SimpleNamespace(reads=[], read_file=lambda path: context_env.reads.append(path))
    runtime = SimpleNamespace(data_dir=str(tmp_path), context_env=context_env)

    check_name_update(runtime, "my name is Myles")

    assert user_file.read_text(encoding="utf-8") == "- Name: Myles\n- Role: Commander\n"
    assert context_env.reads == ["USER.md"]


def test_previous_was_substantive_detects_recent_tool_language():
    runtime = SimpleNamespace(
        context_env=SimpleNamespace(
            history=[
                {"role": "user", "content": "Check the repo"},
                {"role": "assistant", "content": "Tool: repo_reader\nResult: found files"},
            ],
            receipts=[],
        )
    )

    assert previous_was_substantive(runtime) is True


def test_previous_was_substantive_detects_recent_receipts(monkeypatch):
    import orchestrator_routing

    monkeypatch.setattr(orchestrator_routing.time, "time", lambda: 1000)
    runtime = SimpleNamespace(
        context_env=SimpleNamespace(
            history=[
                {"role": "user", "content": "Check the repo"},
                {"role": "assistant", "content": "Done"},
            ],
            receipts=[{"timestamp": 950}],
        )
    )

    assert previous_was_substantive(runtime) is True
