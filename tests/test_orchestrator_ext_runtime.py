import sys
import types
from types import SimpleNamespace

import pytest

from src.core import orchestrator_ext as ext


class DummyOrchestrator(SimpleNamespace):
    def _build_self_awareness(self):
        return "SELF AWARENESS"

    def _build_reasoning_instruction(self):
        return "REASON"

    def _build_frontier_user_message(self, message):
        return {"role": "user", "content": message}

    def _get_deep_model(self):
        return "deep-model"

    def _llm_call_with_retry(self, fn):
        return fn()


def test_build_system_instruction_reflects_soul_channel_host_bridge_and_connectors(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "src.core.feature_flags",
        SimpleNamespace(FEATURE_TOOLS_HOST_BRIDGE=True),
    )
    connector_entry = SimpleNamespace(
        connector=SimpleNamespace(id="slack", status=SimpleNamespace(name="MISSING"))
    )
    configured_entry = SimpleNamespace(
        connector=SimpleNamespace(id="telegram", status=SimpleNamespace(name="ACTIVE"))
    )
    monkeypatch.setitem(
        sys.modules,
        "connectors.base",
        SimpleNamespace(
            ConnectorStatus=SimpleNamespace(
                CONFIGURED=SimpleNamespace(name="CONFIGURED"),
                ACTIVE=configured_entry.connector.status,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "crusader",
        SimpleNamespace(
            CrusaderPromptModifier=SimpleNamespace(
                modify_prompt=lambda instruction: instruction + "\nCRUSADER"
            )
        ),
    )
    orch = DummyOrchestrator(
        soul=SimpleNamespace(
            mission="protect workflows",
            allegiance="operator",
            tone_invariants=["precise", "warm"],
        ),
        rules_context="rule context",
        user_context="user context",
        memory_summary="memory context",
        _current_channel="telegram",
        _connector_registry=SimpleNamespace(list_connectors=lambda: [connector_entry, configured_entry]),
    )

    instruction = ext._build_system_instruction(orch, crusader_mode=True)

    assert "Mission: protect workflows" in instruction
    assert "CHANNEL: This message arrived via Telegram" in instruction
    assert "HOST OS ACCESS (ACTIVE)" in instruction
    assert "Configured and usable: telegram" in instruction
    assert "slack" in instruction
    assert "CRUSADER" in instruction


def test_tool_declarations_include_core_tools_and_optional_github(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_GITHUB_SEARCH", True, raising=False)
    declarations = ext._build_tool_declarations(DummyOrchestrator())
    names = {tool.name for tool in declarations}

    assert {
        "network_client",
        "command_runner",
        "repo_writer",
        "telegram_send",
        "document_creator",
        "schedule_job",
        "skill_manager",
        "github_search",
    }.issubset(names)

    openai_tools = ext._build_openai_tool_declarations(DummyOrchestrator())
    openai_names = {tool["function"]["name"] for tool in openai_tools}
    assert "network_client" in openai_names
    assert "github_search" in openai_names


class TaskStore:
    def __init__(self, graph=None, run=None):
        self.graph = graph
        self.run = run or SimpleNamespace(status="FAILED", last_error="bad")
        self.saved = []
        self.created_runs = []

    def get_latest_graph_for_session(self, session_id):
        return self.graph

    def save_graph(self, graph):
        self.saved.append(graph)
        self.graph = graph

    def create_run(self, run):
        run.id = "run-1"
        self.created_runs.append(run)
        self.run = run

    def get_run(self, run_id):
        return self.run


def test_handle_proceed_requests_permission_compiles_and_executes_with_summary(monkeypatch):
    graph = SimpleNamespace(id="graph-1")
    token = SimpleNamespace(id="token-1", operator_id="op-1")
    result = SimpleNamespace(
        status="SUCCEEDED",
        step_results=[
            SimpleNamespace(success=True, outputs={"answer": 4}, skill_name="calculator")
        ],
    )
    monkeypatch.setattr(
        ext,
        "TaskRun",
        lambda **kwargs: SimpleNamespace(status="RUNNING", last_error="pending", **kwargs),
        raising=False,
    )
    monkeypatch.setattr("feature_flags.FEATURE_AGENTIC_LOOP", False, raising=False)

    assert "Task execution not available" in ext._handle_proceed(
        DummyOrchestrator(task_store=None),
        "proceed",
    )

    no_plan = DummyOrchestrator(
        task_store=TaskStore(graph=None),
        _last_plan_artifact=None,
        plan_compiler=None,
    )
    assert "No plan to proceed" in ext._handle_proceed(no_plan, "proceed", "s1")

    compiled_graph = SimpleNamespace(id="compiled")
    compile_orch = DummyOrchestrator(
        task_store=TaskStore(graph=None),
        _last_plan_artifact={"plan": "x"},
        plan_compiler=SimpleNamespace(compile_plan_artifact=lambda artifact, session_id: compiled_graph),
        _request_permission=lambda graph: f"permission:{graph.id}",
    )
    assert ext._handle_proceed(compile_orch, "proceed", "s1") == "permission:compiled"
    assert compile_orch.task_store.saved == [compiled_graph]

    execute_orch = DummyOrchestrator(
        task_store=TaskStore(graph=graph),
        token_store=SimpleNamespace(get_active_for_session=lambda session_id: [token]),
        task_runner=SimpleNamespace(run=lambda run_id: result),
        assembler=SimpleNamespace(
            assemble=lambda **kwargs: SimpleNamespace(
                war_room_artifacts=[{"path": "artifact"}],
                chat_response="assembled status",
            )
        ),
        _current_channel="warroom",
        _current_quest_id="quest-1",
        _summarize_execution_results=lambda graph, result: "summary content",
        _execute_with_llm=lambda graph: "llm content",
        _deliver_war_room_artifacts=lambda artifacts: setattr(execute_orch, "delivered", artifacts),
    )

    response = ext._handle_proceed(execute_orch, "proceed", "s1")

    assert "summary content" in response
    assert "assembled status" in response
    assert execute_orch.task_store.created_runs[0].quest_id == "quest-1"
    assert execute_orch.delivered == [{"path": "artifact"}]
    assert execute_orch.task_store.run.status == "SUCCEEDED"


def test_handle_proceed_agentic_loop_and_llm_fallback(monkeypatch):
    graph = SimpleNamespace(id="graph-1")
    token = SimpleNamespace(id="token-1")
    monkeypatch.setattr(
        ext,
        "TaskRun",
        lambda **kwargs: SimpleNamespace(status="RUNNING", last_error="pending", **kwargs),
        raising=False,
    )
    monkeypatch.setattr("feature_flags.FEATURE_AGENTIC_LOOP", True, raising=False)
    orch = DummyOrchestrator(
        task_store=TaskStore(graph=graph),
        token_store=SimpleNamespace(get_active_for_session=lambda session_id: [token]),
        task_runner=SimpleNamespace(run=lambda run_id: SimpleNamespace(status="FAILED", step_results=[])),
        assembler=None,
        _execute_with_llm=lambda graph: "agentic content",
    )

    assert ext._handle_proceed(orch, "proceed", "s1") == "agentic content"

    monkeypatch.setattr("feature_flags.FEATURE_AGENTIC_LOOP", False, raising=False)
    orch._execute_with_llm = lambda graph: ""
    assert ext._handle_proceed(orch, "proceed", "s1") == "Task completed with status: FAILED"


def test_init_provider_uses_api_key_oauth_codex_and_gemini_adc(monkeypatch):
    created = []

    class CodexProvider:
        pass

    CodexProvider.__name__ = "OpenAICodexResponsesProviderClient"

    def create_provider(name, api_key, **kwargs):
        created.append((name, api_key, kwargs))
        return CodexProvider()

    monkeypatch.setitem(
        sys.modules,
        "providers.factory",
        SimpleNamespace(
            API_KEY_VARS={"openai": "OPENAI_API_KEY", "openai-codex": "", "gemini": "GEMINI_API_KEY"},
            create_provider=create_provider,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "provider_profile",
        SimpleNamespace(
            ProfileRegistry=lambda: SimpleNamespace(
                has_provider=lambda provider: True,
                get_profile=lambda provider: SimpleNamespace(
                    fast=SimpleNamespace(model="fast"),
                    deep=SimpleNamespace(model="deep", thinking={"budget_tokens": 123}),
                    cache=SimpleNamespace(model="cache"),
                ),
            )
        ),
    )
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    orch = DummyOrchestrator(model_name="default")
    ext._init_provider(orch)
    assert created[-1][0] == "openai"
    assert orch.model_name == "fast"

    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orch = DummyOrchestrator(
        model_name="default",
        _get_openai_codex_oauth_token=lambda: "",
        _has_openai_codex_cli_auth=lambda: True,
    )
    ext._init_provider(orch)
    assert created[-1][0] == "openai-codex"

    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.setenv("LANCELOT_AUTH_MODE", "OAUTH")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "google.auth",
        SimpleNamespace(default=lambda scopes: ("creds", "project")),
    )
    monkeypatch.setitem(
        sys.modules,
        "google",
        SimpleNamespace(auth=SimpleNamespace(default=lambda scopes: ("creds", "project"))),
    )
    orch = DummyOrchestrator(model_name="default")
    ext._init_provider(orch)
    assert created[-1][0] == "gemini"
    assert created[-1][2]["credentials"] == "creds"


def test_verify_intent_with_llm_guardrails_and_overrides():
    long_message = "please search for roadmap details and explain what you find " * 3
    unhealthy = DummyOrchestrator(local_model=SimpleNamespace(is_healthy=lambda: False))
    assert ext._verify_intent_with_llm(unhealthy, long_message, ext.IntentType.PLAN_REQUEST) == ext.IntentType.PLAN_REQUEST

    labels = iter(["action", "question", "question", "action", "plan"])
    healthy = DummyOrchestrator(
        local_model=SimpleNamespace(
            is_healthy=lambda: True,
            verify_routing_intent=lambda message: next(labels),
        )
    )

    assert ext._verify_intent_with_llm(healthy, long_message, ext.IntentType.PLAN_REQUEST) == ext.IntentType.KNOWLEDGE_REQUEST
    assert ext._verify_intent_with_llm(healthy, long_message, ext.IntentType.EXEC_REQUEST) == ext.IntentType.KNOWLEDGE_REQUEST
    assert ext._verify_intent_with_llm(healthy, long_message, ext.IntentType.MIXED_REQUEST) == ext.IntentType.KNOWLEDGE_REQUEST
    assert ext._verify_intent_with_llm(healthy, long_message, ext.IntentType.MIXED_REQUEST) == ext.IntentType.KNOWLEDGE_REQUEST
    assert ext._verify_intent_with_llm(healthy, long_message, ext.IntentType.PLAN_REQUEST) == ext.IntentType.PLAN_REQUEST


def test_deep_reasoning_pass_records_thinking_and_failure():
    calls = []
    orch = DummyOrchestrator(
        _provider_name="anthropic",
        _provider_mode="sdk",
        _deep_thinking_config={"budget_tokens": 456},
        _provider_generate=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(
            text="final reasoning\nCapability gap: needs calendar access",
            raw={"thinking": "private thinking"},
        ),
    )

    artifact = ext._deep_reasoning_pass(orch, "analyze task", past_experiences="last time worked")

    assert artifact.model_used == "deep-model"
    assert "private thinking" in artifact.reasoning_text
    assert calls[0]["config"] == {"thinking": {"type": "enabled", "budget_tokens": 456}}
    assert artifact.token_count_estimate > 0

    failing = DummyOrchestrator(_provider_generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model down")))
    artifact = ext._deep_reasoning_pass(failing, "analyze task")
    assert artifact.reasoning_text == "[Reasoning pass unavailable]"


def test_record_task_experience_inserts_episodic_memory_and_handles_failures(monkeypatch):
    class FakeTaskExperience:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        @staticmethod
        def from_tool_receipts(receipts):
            return {
                "tools_used": ["network_client"] if receipts else [],
                "tools_succeeded": ["network_client"] if receipts else [],
                "tools_failed": [],
                "retries": 0,
            }

        def to_memory_content(self):
            return f"{self.task_summary}|{self.outcome}"

    class FakeMemoryItem:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeProvenance:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "reasoning_artifact",
        SimpleNamespace(TaskExperience=FakeTaskExperience),
    )
    monkeypatch.setitem(
        sys.modules,
        "memory.schemas",
        SimpleNamespace(
            MemoryItem=FakeMemoryItem,
            MemoryTier=SimpleNamespace(episodic="episodic"),
            Provenance=FakeProvenance,
            ProvenanceType=SimpleNamespace(agent_inference="agent_inference"),
            generate_id=lambda: "memory-1",
        ),
    )
    inserted = []
    memory_manager = SimpleNamespace(episodic=SimpleNamespace(insert=lambda item: inserted.append(item)))
    receipt = SimpleNamespace(status="success", tool_name="network_client")
    reasoning = SimpleNamespace(reasoning_text="reasoned", capability_gaps=["calendar"])
    orch = DummyOrchestrator(_memory_store_manager=memory_manager)

    ext._record_task_experience(
        orch,
        "research competitor pricing",
        "Finished without Error",
        [receipt],
        reasoning_artifact=reasoning,
        duration_ms=12.5,
    )

    assert len(inserted) == 1
    item = inserted[0]
    assert item.namespace == "task_experience"
    assert item.metadata["duration_ms"] == 12.5
    assert "partial" in item.tags

    broken = DummyOrchestrator(_memory_store_manager=SimpleNamespace(episodic=SimpleNamespace(insert=lambda item: (_ for _ in ()).throw(RuntimeError("db down")))))
    ext._record_task_experience(broken, "task", "response", [], duration_ms=1)
