import sys
import types
from types import SimpleNamespace

import pytest

import orchestrator


def _runtime():
    runtime = orchestrator.LancelotOrchestrator.__new__(orchestrator.LancelotOrchestrator)
    runtime.provider = None
    runtime.model_name = "fast-default"
    runtime._cache = None
    runtime._cache_model = "cache-default"
    runtime._cache_ttl = 3600
    runtime._deep_model_validation_cache = {}
    runtime._current_quest_id = "quest-1"
    runtime._current_channel = "warroom"
    runtime._current_operator_id = "op-1"
    runtime._current_operator_name = "Myles"
    runtime._current_session_id = "session-1"
    return runtime


def test_provider_lane_cache_and_validation_accessors():
    runtime = _runtime()
    provider = SimpleNamespace(
        create_context_cache=lambda **kwargs: SimpleNamespace(name="cache-1", kwargs=kwargs)
    )

    runtime.set_provider_runtime(provider, provider_name="openai-codex", provider_mode="sdk")
    runtime.set_provider_lane_configuration(
        fast_model="gpt-fast",
        deep_model="gpt-deep",
        cache_model="gpt-cache",
        deep_thinking_config={"budget_tokens": 512},
    )
    runtime.record_deep_model_validation("gpt-deep", True)
    runtime._deep_model_valid_old = True

    assert runtime.active_provider_name == "openai-codex"
    assert runtime.model_name == "gpt-fast"
    assert runtime.deep_model_name() == "gpt-deep"
    assert runtime.context_cache_model() == "gpt-cache"
    assert runtime.context_cache_ttl_seconds() == 3600
    assert runtime.cached_deep_model_validation("gpt-deep") is True

    runtime.set_model_lane("deep", "gpt-deep-2")
    assert runtime.cached_deep_model_validation("gpt-deep") is None
    assert not hasattr(runtime, "_deep_model_valid_old")

    runtime.set_context_cache(SimpleNamespace(name="existing-cache"))
    assert runtime.context_cache_name() == "existing-cache"
    runtime.set_model_lane("cache", "gpt-cache-2")
    assert runtime.context_cache_name() is None
    assert runtime.context_cache_model() == "gpt-cache-2"

    created = runtime.create_context_cache(
        contents="large stable context",
        system_instruction="system",
        display_name="Release context",
    )
    assert created.name == "cache-1"
    assert created.kwargs["model"] == "gpt-cache-2"
    assert created.kwargs["ttl_s"] == 3600

    with pytest.raises(ValueError):
        runtime.set_model_lane("unknown", "model")

    no_provider = _runtime()
    with pytest.raises(RuntimeError):
        no_provider.create_context_cache(contents="x", system_instruction="s", display_name="d")


def test_memory_soul_governance_and_telegram_state_accessors():
    runtime = _runtime()
    risk_updates = []
    risk_classifier = SimpleNamespace(update_soul=lambda soul: risk_updates.append(soul))

    runtime.set_memory_enabled(True)
    assert runtime.is_memory_enabled() is True
    runtime.refresh_soul_policy("active-soul")
    assert runtime.soul == "active-soul"

    runtime.set_governance_runtime(
        risk_classifier=risk_classifier,
        async_queue="queue",
        rollback_manager="rollback",
        template_registry="templates",
    )
    runtime.refresh_soul_policy("new-soul")
    assert risk_updates == ["new-soul"]
    assert runtime._async_queue == "queue"
    assert runtime._rollback_manager == "rollback"
    assert runtime._template_registry == "templates"

    registry = object()
    runtime.attach_connector_registry(registry)
    assert runtime._connector_registry is registry

    assert runtime.was_telegram_delivery_handled() is False
    runtime.mark_telegram_delivery_handled()
    assert runtime.was_telegram_delivery_handled() is True
    runtime.clear_telegram_delivery_handled()
    assert runtime.was_telegram_delivery_handled() is False


def test_event_progress_and_model_usage_wrappers(monkeypatch):
    runtime = _runtime()
    published = []

    class Event:
        def __init__(self, type, payload):
            self.type = type
            self.payload = payload

    monkeypatch.setitem(
        sys.modules,
        "event_bus",
        SimpleNamespace(
            Event=Event,
            event_bus=SimpleNamespace(publish_sync=lambda event: published.append(event)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.core.model_usage_policy",
        SimpleNamespace(get_model_usage_status=lambda: {"local": "ready"}),
    )

    runtime.emit_chat_progress("planning", "Building plan", detail=None, wait_reason="provider")

    assert runtime.current_model_usage_status() == {"local": "ready"}
    assert published[0].type == "chat.progress"
    assert published[0].payload["quest_id"] == "quest-1"
    assert published[0].payload["wait_reason"] == "provider"
    assert "detail" not in published[0].payload

    monkeypatch.setitem(sys.modules, "event_bus", None)
    runtime.emit_chat_progress("ignored", "No event bus")


def test_verify_async_job_delegates_to_verifier():
    runtime = _runtime()
    calls = []
    runtime.verifier = SimpleNamespace(
        verify_step=lambda goal, output: calls.append((goal, output)) or SimpleNamespace(success=True)
    )

    assert runtime.verify_async_job(SimpleNamespace(goal="deploy", output={"ok": True})) is True
    assert calls == [("deploy", "{'ok': True}")]


def test_plan_enrichment_no_provider_agentic_and_text_paths(monkeypatch):
    artifact = SimpleNamespace(goal="Ship release", plan_steps=["template"], next_action="template")
    runtime = _runtime()

    assert runtime._enrich_plan_with_llm(artifact, "make it better") is artifact
    assert artifact.plan_steps == ["template"]

    monkeypatch.setattr("feature_flags.FEATURE_AGENTIC_LOOP", True, raising=False)
    runtime.provider = object()
    runtime._build_self_awareness = lambda: "self-aware"
    runtime._agentic_generate = lambda **kwargs: "1. Inspect repo\n2. Run tests\n3. Publish notes"
    enriched = runtime._enrich_plan_with_llm(artifact, "prepare release")
    assert enriched.plan_steps == ["Inspect repo", "Run tests", "Publish notes"]
    assert enriched.next_action == "Inspect repo"

    monkeypatch.setattr("feature_flags.FEATURE_AGENTIC_LOOP", False, raising=False)
    artifact = SimpleNamespace(goal="Review", plan_steps=["template"], next_action="template")
    runtime.context_env = SimpleNamespace(get_context_string=lambda: "context")
    runtime._build_frontier_user_message = lambda text: {"role": "user", "content": text}
    runtime._llm_call_with_retry = lambda fn: fn()
    runtime._provider_generate = lambda **kwargs: SimpleNamespace(
        text="1. Read docs\n2. Verify behavior\n3. Update summary\n"
    )
    enriched = runtime._enrich_plan_with_llm(artifact, "review feature")
    assert enriched.plan_steps == ["Read docs", "Verify behavior", "Update summary"]

    runtime._provider_generate = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down"))
    fallback = runtime._enrich_plan_with_llm(artifact, "review feature")
    assert fallback is artifact


def test_execution_result_summary_success_failure_and_fallback(monkeypatch):
    runtime = _runtime()
    graph = SimpleNamespace(
        goal="Run release checks",
        steps=[
            SimpleNamespace(step_id="s1", inputs={"description": "Run unit tests"}, type="command"),
            SimpleNamespace(step_id="s2", inputs={}, type="lint"),
        ],
    )
    run_result = SimpleNamespace(
        step_results=[
            SimpleNamespace(step_id="s1", success=True, outputs={"passed": 12}, error=""),
            SimpleNamespace(step_id="s2", success=False, outputs={}, error="lint failed"),
        ]
    )

    assert runtime._summarize_execution_results(graph, run_result) == ""

    runtime.provider = object()
    runtime.context_env = SimpleNamespace(get_context_string=lambda: "context")
    runtime._build_execution_instruction = lambda: "exec system"
    runtime._build_frontier_user_message = lambda text: {"role": "user", "content": text}
    runtime._route_model = lambda prompt: "deep-model"
    runtime._get_thinking_config = lambda: {"budget_tokens": 128}
    runtime._llm_call_with_retry = lambda fn: fn()
    runtime._provider_generate = lambda **kwargs: SimpleNamespace(text="Summary with scaffold")
    monkeypatch.setitem(
        sys.modules,
        "response.policies",
        SimpleNamespace(OutputPolicy=SimpleNamespace(strip_tool_scaffolding=lambda text: text.replace(" scaffold", ""))),
    )

    assert runtime._summarize_execution_results(graph, run_result) == "Summary with"

    runtime._provider_generate = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model failed"))
    fallback = runtime._summarize_execution_results(graph, run_result)
    assert fallback.startswith("**Execution Complete**")
    assert "Run unit tests: SUCCESS" in fallback
    assert "lint: FAILED - lint failed" in fallback


def test_task_experience_retrieval_existing_store_lazy_store_and_failure(monkeypatch):
    runtime = _runtime()
    item = SimpleNamespace(content="Prior release cleanup succeeded")
    runtime._memory_store_manager = SimpleNamespace(
        episodic=SimpleNamespace(search=lambda **kwargs: [item])
    )

    rendered = runtime._retrieve_task_experiences("release cleanup", limit=2)
    assert rendered == "Past similar tasks:\n- Prior release cleanup succeeded"

    inserted_manager = SimpleNamespace(episodic=SimpleNamespace(search=lambda **kwargs: []))
    monkeypatch.setitem(
        sys.modules,
        "memory.sqlite_store",
        SimpleNamespace(MemoryStoreManager=lambda data_dir: inserted_manager),
    )
    runtime = _runtime()
    runtime.data_dir = "data-dir"
    assert runtime._retrieve_task_experiences("unknown") == ""
    assert runtime._memory_store_manager is inserted_manager

    runtime._memory_store_manager = SimpleNamespace(
        episodic=SimpleNamespace(search=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    )
    assert runtime._retrieve_task_experiences("release cleanup") == ""


def test_public_wrapper_methods_delegate_to_runtime_implementations(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(orchestrator, "_suggest_alternatives_impl", lambda skill, inputs: ["alt"])
    monkeypatch.setattr(orchestrator, "_get_trust_summary_impl", lambda self, skill, inputs: "trusted")
    monkeypatch.setattr(orchestrator, "_set_lane_model_impl", lambda self, lane, model: setattr(self, "lane_call", (lane, model)))
    monkeypatch.setattr(orchestrator, "_get_thinking_config_impl", lambda: {"thinking": True})

    assert runtime.suggest_alternatives("command_runner", {}) == ["alt"]
    assert runtime.get_trust_summary("command_runner", {}) == "trusted"
    runtime.set_lane_model("fast", "model-fast")
    assert runtime.lane_call == ("fast", "model-fast")
    assert runtime.get_thinking_config() == {"thinking": True}
