from types import SimpleNamespace

from orchestrator_approval import (
    handle_approval,
    is_proceed_message,
    request_permission,
)
from src.core.tasking.schema import StepType, TaskGraph, TaskStep


def test_is_proceed_message_accepts_strong_signal_without_plan():
    runtime = SimpleNamespace(_last_plan_artifact=None, task_store=None)

    assert is_proceed_message(runtime, "go ahead please") is True
    assert is_proceed_message(runtime, "confirmed") is True


def test_is_proceed_message_requires_pending_plan_for_contextual_signal():
    runtime_without_plan = SimpleNamespace(_last_plan_artifact=None, task_store=None)
    runtime_with_plan = SimpleNamespace(_last_plan_artifact=object(), task_store=None)

    assert is_proceed_message(runtime_without_plan, "sounds good") is False
    assert is_proceed_message(runtime_with_plan, "sounds good") is True


def test_is_proceed_message_treats_pending_graph_as_plan_context():
    class TaskStore:
        def get_latest_graph_for_session(self, session_id):
            assert session_id == "session-1"
            return object()

    runtime = SimpleNamespace(
        _last_plan_artifact=None,
        _current_session_id="session-1",
        task_store=TaskStore(),
    )

    assert is_proceed_message(runtime, "go for it") is True


def test_request_permission_rejects_missing_required_inputs_before_approval():
    class Assembler:
        def assemble_permission_request(self, **_kwargs):
            raise AssertionError("assembler should not receive incomplete graphs")

    graph = TaskGraph(
        goal="Update a file",
        steps=[
            TaskStep(
                type=StepType.TOOL_CALL.value,
                inputs={
                    "tool_name": "repo_writer",
                    "action": "edit",
                    "description": "Update the deployment config",
                },
                risk_level="MED",
            )
        ],
    )

    response = request_permission(SimpleNamespace(assembler=Assembler()), graph)

    assert "Cannot request approval yet" in response
    assert "missing required input(s): 'path'" in response
    assert "generate a new governed execution request" in response


def test_request_permission_uses_assembler_with_resolved_authorities():
    calls = []

    class Assembler:
        def assemble_permission_request(self, **kwargs):
            calls.append(kwargs)
            return "approval text"

    graph = TaskGraph(
        goal="Update repository docs",
        steps=[
            TaskStep(
                type=StepType.FILE_EDIT.value,
                inputs={
                    "description": "Update README proof command",
                    "action": "edit",
                    "path": "README.md",
                },
                risk_level="MED",
            ),
            TaskStep(
                type=StepType.SKILL_CALL.value,
                inputs={
                    "description": "Run documentation hygiene check",
                    "skill_name": "doc_hygiene",
                },
                risk_level="HIGH",
            ),
        ],
    )

    response = request_permission(SimpleNamespace(assembler=Assembler()), graph)

    assert response == "approval text"
    assert calls == [
        {
            "what_i_will_do": [
                "Update README proof command",
                "Run documentation hygiene check",
            ],
            "tools_enabled": {"repo_writer", "doc_hygiene"},
            "risk_tier": "HIGH",
            "limits": {"duration": 300, "actions": 4},
        }
    ]


def test_handle_approval_mints_token_with_operator_identity_then_proceeds():
    minted = []
    proceeded = []
    graph = TaskGraph(
        goal="Ship the approved change",
        steps=[
            TaskStep(
                type=StepType.FILE_EDIT.value,
                inputs={
                    "description": "Update README proof command",
                    "action": "edit",
                    "path": "README.md",
                },
                risk_level="MED",
            )
        ],
    )

    class TaskStore:
        def get_latest_graph_for_session(self, session_id):
            assert session_id == "session-1"
            return graph

    class Minter:
        def mint_from_approval(self, **kwargs):
            minted.append(kwargs)
            return SimpleNamespace(id="token-1")

    class WarRoomState:
        def get_session(self, session_id):
            assert session_id == "session-1"
            return {
                "operator_identity": SimpleNamespace(
                    operator_id="operator-1",
                    display_name="Myles",
                )
            }

    runtime = SimpleNamespace(
        minter=Minter(),
        task_store=TaskStore(),
        warroom_state=WarRoomState(),
    )
    runtime._handle_proceed = lambda message, session_id="": proceeded.append((message, session_id)) or "executed"

    result = handle_approval(runtime, session_id="session-1")

    assert result == "executed"
    assert proceeded == [("proceed", "session-1")]
    assert minted == [
        {
            "scope": "Ship the approved change",
            "tools": ["repo_writer"],
            "skills": [],
            "risk_tier": "MED",
            "max_actions": 2,
            "session_id": "session-1",
            "operator_id": "operator-1",
            "operator_name": "Myles",
        }
    ]


def test_handle_approval_blocks_incomplete_graph_before_minting():
    class TaskStore:
        def get_latest_graph_for_session(self, _session_id):
            return TaskGraph(
                goal="Incomplete",
                steps=[
                    TaskStep(
                        type=StepType.TOOL_CALL.value,
                        inputs={"tool_name": "repo_writer", "action": "edit"},
                    )
                ],
            )

    class Minter:
        def mint_from_approval(self, **_kwargs):
            raise AssertionError("incomplete graph should not mint authority")

    response = handle_approval(
        SimpleNamespace(minter=Minter(), task_store=TaskStore()),
        session_id="session-1",
    )

    assert "Approval was not accepted" in response
    assert "missing required input(s): 'path'" in response
