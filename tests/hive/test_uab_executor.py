"""Tests for HIVE UAB executor enforcement boundaries."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.core.execution_authority import UABAuthorityGrant
from src.core.soul.store import AutonomyPosture, Soul
from src.hive.errors import ScopedSoulViolationError
from src.hive.integration.governance_bridge import GovernanceResult
from src.hive.integration.uab_executor import HiveUABExecutor, _summarize_elements

TEST_GRANT_SECRET = "hive-uab-executor-test-secret"


@dataclass
class _ConnectResult:
    success: bool = True
    error_message: str | None = None
    duration_ms: int = 1
    state_changes: dict | None = None


@dataclass
class _StateResult:
    window_title: str = "Notepad"
    focused: bool = True


class _MockUABProvider:
    def __init__(self):
        self.act_calls = []
        self.keypress_calls = []
        self.hotkey_calls = []
        self.maximize_calls = []
        self.restore_calls = []
        self.query_calls = []
        self.connect_calls = []

    def connect(self, pid):
        self.connect_calls.append(pid)
        return _ConnectResult()

    def state(self, pid):
        return _StateResult()

    def enumerate(self, pid):
        return [
            {
                "id": "edit1",
                "type": "edit",
                "label": "Editor",
                "actions": ["click", "type"],
            }
        ]

    def act(self, pid, element_id, action, params):
        self.act_calls.append((pid, element_id, action, params))
        return _ConnectResult(state_changes={})

    def keypress(self, pid, key):
        self.keypress_calls.append((pid, key))
        return _ConnectResult()

    def hotkey(self, pid, keys):
        self.hotkey_calls.append((pid, keys))
        return _ConnectResult()

    def maximize(self, pid):
        self.maximize_calls.append(pid)
        return _ConnectResult()

    def restore(self, pid):
        self.restore_calls.append(pid)
        return _ConnectResult()

    def query(self, pid, selector):
        self.query_calls.append((pid, selector))
        return [{"id": "edit1"}]


class _MockLLM:
    def __init__(self, output=None, *, exc: Exception | None = None):
        self.output = output
        self.exc = exc
        self.calls = []

    def route(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(output=self.output)


class _DenyGovernance:
    def validate_action(self, **kwargs):
        return GovernanceResult(
            approved=False,
            tier="T3",
            reason="Denied by test",
        )


class _ApprovalRequiredGovernance:
    def validate_action(self, **kwargs):
        return GovernanceResult(
            approved=False,
            tier="T2",
            reason="Approval pending",
            requires_operator_approval=True,
            approval_request_id="approval-123",
        )


class _TrackingGovernance:
    def __init__(self):
        self.updates = []

    def validate_action(self, **kwargs):
        return GovernanceResult(
            approved=True,
            tier="T0",
            reason="allowed",
        )

    def update_trust(self, capability, scope, success):
        self.updates.append((capability, scope, success))


def test_summarize_elements_flattens_children_and_truncates():
    summary = _summarize_elements(
        [
            {
                "id": "window1",
                "type": "window",
                "children": [
                    {"id": "edit1", "type": "edit", "actions": ["click", "type"]},
                    {"id": "button1", "type": "button", "label": "Send"},
                ],
            }
        ],
        max_items=2,
    )

    assert 'id=window1  type=window' in summary
    assert 'id=edit1  type=edit  actions=[\'click\', \'type\']' in summary
    assert '... (truncated)' in summary


def test_executor_rejects_disallowed_app_before_connect():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    with pytest.raises(
        ScopedSoulViolationError,
        match="Scoped Soul forbids desktop app 'chrome'",
    ):
        executor({
            "spec": "verify the window is open",
            "context": {"target_pid": 101, "target_app": "chrome"},
            "allowed_apps": ["notepad"],
        })

    assert provider.act_calls == []


def test_executor_updates_trust_for_successful_mutating_steps():
    provider = _MockUABProvider()
    governance = _TrackingGovernance()
    executor = HiveUABExecutor(
        uab_provider=provider,
        governance_bridge=governance,
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    result = executor({
        "spec": "type 'hello world'",
        "agent_id": "agent-1",
        "context": {"target_pid": 202, "target_app": "notepad"},
    })

    assert result["success"] is True
    assert governance.updates == [
        ("uab_click", "notepad", True),
        ("uab_type", "notepad", True),
    ]
    _, click_element, click_action, click_params = provider.act_calls[0]
    _, type_element, type_action, type_params = provider.act_calls[1]
    assert (click_element, click_action) == ("edit1", "click")
    assert (type_element, type_action) == ("edit1", "type")
    click_grant = UABAuthorityGrant.from_dict(click_params["uabAuthorityGrant"])
    type_grant = UABAuthorityGrant.from_dict(type_params["uabAuthorityGrant"])
    assert click_grant.validate(
        TEST_GRANT_SECRET,
        app_name="notepad",
        app_pid=202,
        action="click",
        selector_scope="edit1",
    ).valid is True
    assert type_grant.validate(
        TEST_GRANT_SECRET,
        app_name="notepad",
        app_pid=202,
        action="type",
        selector_scope="edit1",
    ).valid is True
    assert click_params["selectorScope"] == "edit1"
    assert type_params["selectorScope"] == "edit1"


def test_executor_updates_trust_for_failed_mutating_step():
    class _FailingUABProvider(_MockUABProvider):
        def act(self, pid, element_id, action, params):
            self.act_calls.append((pid, element_id, action, params))
            return _ConnectResult(
                success=False,
                error_message="boom",
                state_changes={},
            )

    provider = _FailingUABProvider()
    governance = _TrackingGovernance()
    executor = HiveUABExecutor(
        uab_provider=provider,
        governance_bridge=governance,
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    result = executor({
        "spec": "click the text area",
        "agent_id": "agent-1",
        "context": {"target_pid": 202, "target_app": "notepad"},
    })

    assert result["success"] is False
    assert governance.updates == [
        ("uab_click", "notepad", False),
    ]


def test_executor_raises_scoped_violation_when_governance_denies_mutating_step():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(
        uab_provider=provider,
        governance_bridge=_DenyGovernance(),
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    with pytest.raises(
        ScopedSoulViolationError,
        match="Governance denied UAB capability 'uab_click'",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
        })

    assert provider.act_calls == []


def test_executor_missing_governance_denies_mutating_step_before_provider_call():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(
        uab_provider=provider,
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    with pytest.raises(
        ScopedSoulViolationError,
        match="Governance bridge required for UAB capability 'uab_click'",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
        })

    assert provider.act_calls == []


def test_executor_approval_required_denies_without_grant():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(
        uab_provider=provider,
        governance_bridge=_ApprovalRequiredGovernance(),
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    with pytest.raises(
        ScopedSoulViolationError,
        match="Governance denied UAB capability 'uab_click'",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
        })

    assert provider.act_calls == []


def test_executor_approved_non_act_governed_step_fails_closed_without_provider_call():
    provider = _MockUABProvider()
    llm = _MockLLM(output='[{"method": "maximize"}]')
    governance = _TrackingGovernance()
    executor = HiveUABExecutor(
        uab_provider=provider,
        llm_router=llm,
        governance_bridge=governance,
        uab_grant_secret=TEST_GRANT_SECRET,
    )

    result = executor({
        "spec": "maximize the window",
        "agent_id": "agent-1",
        "context": {"target_pid": 202, "target_app": "notepad"},
    })

    assert result["success"] is False
    assert result["steps"][1]["method"] == "maximize"
    assert "Grant-carrying provider path unavailable" in result["steps"][1]["error"]
    assert provider.maximize_calls == []
    assert governance.updates == [("uab_maximize", "notepad", False)]


def test_executor_blocks_mutating_uab_steps_outside_query_only_scope():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    with pytest.raises(
        ScopedSoulViolationError,
        match="outside scoped categories",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
            "allowed_categories": ["query"],
        })

    assert provider.act_calls == []


def test_executor_honors_explicit_scoped_soul_capabilities_for_uab_steps():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)
    scoped_soul = Soul(
        version="v1",
        mission="Test",
        allegiance="Test",
        autonomy_posture=AutonomyPosture(
            level="scoped",
            description="Query only",
            allowed_autonomous=["uab_query", "uab_state"],
            requires_approval=[],
        ),
    )

    with pytest.raises(
        ScopedSoulViolationError,
        match="does not permit UAB capability 'uab_click'",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
            "scoped_soul": scoped_soul,
        })

    assert provider.act_calls == []


def test_executor_requires_approval_for_scoped_uab_mutation():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)
    scoped_soul = Soul(
        version="v1",
        mission="Test",
        allegiance="Test",
        autonomy_posture=AutonomyPosture(
            level="scoped",
            description="Click requires approval",
            allowed_autonomous=["uab_query", "uab_state"],
            requires_approval=["uab_click"],
        ),
    )

    with pytest.raises(
        ScopedSoulViolationError,
        match="requires operator approval for UAB capability 'uab_click'",
    ):
        executor({
            "spec": "type 'hello world'",
            "agent_id": "agent-1",
            "context": {"target_pid": 202, "target_app": "notepad"},
            "scoped_soul": scoped_soul,
        })

    assert provider.act_calls == []


def test_plan_steps_accepts_markdown_fenced_single_step_object():
    provider = _MockUABProvider()
    llm = _MockLLM(output='```json\n{"method": "state"}\n```')
    executor = HiveUABExecutor(uab_provider=provider, llm_router=llm)

    steps = executor._plan_steps("verify state", 202, "notepad", "Notepad", provider.enumerate(202))

    assert steps == [{"method": "state"}]
    assert llm.calls


def test_plan_steps_falls_back_to_heuristic_when_llm_returns_no_output():
    provider = _MockUABProvider()
    llm = _MockLLM(output=None)
    executor = HiveUABExecutor(uab_provider=provider, llm_router=llm)

    steps = executor._plan_steps("verify the window is open", 202, "notepad", "Notepad", provider.enumerate(202))

    assert steps == [{"method": "state"}]


def test_plan_steps_falls_back_to_heuristic_when_llm_raises():
    provider = _MockUABProvider()
    llm = _MockLLM(exc=RuntimeError("planner offline"))
    executor = HiveUABExecutor(uab_provider=provider, llm_router=llm)

    steps = executor._plan_steps("type hello", 202, "notepad", "Notepad", [])

    assert steps == [
        {"method": "hotkey", "keys": ["ctrl", "a"]},
        {"method": "keypress", "key": "Delete"},
        {"method": "act", "element_id": "", "action": "type", "params": {"text": "hello"}},
    ]


def test_heuristic_plan_focuses_window_with_restore_then_maximize():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    steps = executor._heuristic_plan("bring the window to the foreground", 202, "notepad", [])

    assert steps == [{"method": "restore"}, {"method": "maximize"}]


def test_heuristic_plan_click_without_editor_falls_back_to_focus():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    steps = executor._heuristic_plan("click the text area", 202, "notepad", [])

    assert steps == [{"method": "act", "element_id": "", "action": "focus"}]


def test_heuristic_plan_verify_text_queries_editor():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    steps = executor._heuristic_plan("verify text is present", 202, "notepad", [{"id": "edit1", "type": "edit"}])

    assert steps == [{"method": "state"}, {"method": "query", "selector": {"id": "edit1"}}]


def test_heuristic_plan_defaults_to_state_when_no_pattern_matches():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    steps = executor._heuristic_plan("observe the application", 202, "notepad", [])

    assert steps == [{"method": "state"}]


def test_find_editor_element_recurses_by_label_and_input_action():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    by_label = executor._find_editor_element([
        {"id": "container1", "type": "panel", "children": [{"id": "child-editor", "type": "panel", "label": "Text Editor"}]}
    ])
    by_action = executor._find_editor_element([
        {"id": "container2", "type": "panel", "children": [{"id": "child-input", "type": "panel", "actions": ["type"]}]}
    ])

    assert by_label == "child-editor"
    assert by_action == "child-input"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ('type "hello world"', "hello world"),
        ("write 'hello world'", "hello world"),
        ("type: hello world.", "hello world"),
    ],
)
def test_extract_quoted_text_supports_multiple_formats(description, expected):
    assert HiveUABExecutor._extract_quoted_text(description) == expected


def test_extract_quoted_text_returns_none_without_extractable_text():
    assert HiveUABExecutor._extract_quoted_text("observe the application") is None


@pytest.mark.parametrize(
    ("step", "expected_scope", "expected_governed"),
    [
        ({"method": "connect"}, None, None),
        ({"method": "state"}, "uab_state", None),
        ({"method": "query"}, "uab_query", None),
        ({"method": "act", "action": "click"}, "uab_click", "uab_click"),
        ({"method": "act"}, None, None),
        ({"method": "keypress"}, "uab_keypress", "uab_keypress"),
        ({"method": "hotkey"}, "uab_hotkey", "uab_hotkey"),
        ({"method": "maximize"}, "uab_maximize", "uab_maximize"),
        ({"method": "restore"}, "uab_restore", "uab_restore"),
        ({"method": "noop"}, None, None),
    ],
)
def test_step_capability_mapping_covers_scope_and_governed_paths(step, expected_scope, expected_governed):
    assert HiveUABExecutor._scope_capability_for_step(step) == expected_scope
    assert HiveUABExecutor._governed_capability_for_step(step) == expected_governed


def test_record_step_outcome_skips_non_governed_steps():
    governance = _TrackingGovernance()
    executor = HiveUABExecutor(uab_provider=_MockUABProvider(), governance_bridge=governance)

    executor._record_step_outcome({"method": "state"}, "notepad", {"success": True})

    assert governance.updates == []


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ({"method": "state"}, {"method": "state"}),
        ({"method": "query", "selector": {"id": "edit1"}}, {"method": "query"}),
        ({"method": "connect"}, {"method": "connect"}),
    ],
)
def test_execute_step_covers_non_act_methods(step, expected):
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    result = executor._execute_step(step, 202)

    assert result["success"] is True
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.parametrize(
    "step",
    [
        {"method": "act", "element_id": "edit1", "action": "click"},
        {"method": "keypress", "key": "Enter"},
        {"method": "hotkey", "keys": ["ctrl", "a"]},
        {"method": "maximize"},
        {"method": "restore"},
    ],
)
def test_execute_step_fails_closed_for_governed_methods_without_grant(step):
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    result = executor._execute_step(step, 202)

    assert result["success"] is False
    assert result["error"] == "UAB authority grant required for governed HIVE UAB step"
    assert provider.act_calls == []
    assert provider.keypress_calls == []
    assert provider.hotkey_calls == []
    assert provider.maximize_calls == []
    assert provider.restore_calls == []


def test_execute_step_fails_closed_for_non_act_governed_method_even_with_grant():
    provider = _MockUABProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    result = executor._execute_step(
        {"method": "keypress", "key": "Enter"},
        202,
        {"grant_id": "test"},
    )

    assert result["success"] is False
    assert "Grant-carrying provider path unavailable" in result["error"]
    assert provider.keypress_calls == []


def test_execute_step_returns_unknown_method_error():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    result = executor._execute_step({"method": "unknown"}, 202)

    assert result["success"] is False
    assert "Unknown UAB method" in result["error"]


def test_execute_step_bounds_provider_exceptions():
    class _ExplodingProvider(_MockUABProvider):
        def act(self, pid, element_id, action, params):
            raise RuntimeError("boom" * 100)

    executor = HiveUABExecutor(uab_provider=_ExplodingProvider())

    result = executor._execute_step({"method": "act", "element_id": "edit1", "action": "click"}, 202)

    assert result["success"] is False
    assert len(result["error"]) <= 200


def test_executor_returns_error_when_target_pid_missing():
    executor = HiveUABExecutor(uab_provider=_MockUABProvider())

    result = executor({"spec": "verify state", "context": {"target_app": "notepad"}})

    assert result == {"success": False, "error": "No target_pid in context", "steps": []}


def test_executor_continues_after_soft_connect_failure():
    class _SoftFailConnectProvider(_MockUABProvider):
        def connect(self, pid):
            self.connect_calls.append(pid)
            return _ConnectResult(success=False, error_message="already connected")

    provider = _SoftFailConnectProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    result = executor({
        "spec": "verify the window is open",
        "context": {"target_pid": 202, "target_app": "notepad"},
    })

    assert result["success"] is True
    assert result["steps"][0]["method"] == "connect"
    assert result["steps"][0]["success"] is False
    assert any(step["method"] == "state" for step in result["steps"])


def test_executor_returns_partial_steps_when_provider_raises():
    class _ExplodingProvider(_MockUABProvider):
        def enumerate(self, pid):
            raise RuntimeError("enumerate exploded")

    provider = _ExplodingProvider()
    executor = HiveUABExecutor(uab_provider=provider)

    result = executor({
        "spec": "verify state",
        "context": {"target_pid": 202, "target_app": "notepad"},
    })

    assert result["success"] is False
    assert "enumerate exploded" in result["error"]
    assert result["steps"] == [
        {"method": "connect", "pid": 202, "success": True, "error": None}
    ]
