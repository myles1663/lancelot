"""Baseline contracts for core-spine refactors.

These tests freeze runtime, receipt, memory, tool-loop, and UAB behavior before
the LDD-002 file splits begin.
"""

from __future__ import annotations

import types

from fastapi.testclient import TestClient


def test_gateway_app_binds_health_and_readiness_routes():
    import gateway

    client = TestClient(gateway.app)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code in {200, 503}
    assert isinstance(health.json(), dict)
    assert "ready" in ready.json()


def test_orchestrator_basic_chat_uses_existing_text_generation_path(monkeypatch):
    import chat_flow
    import feature_flags
    from tests.test_chat_flow_runtime_paths import _runtime

    monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", False, raising=False)
    monkeypatch.setattr(chat_flow, "classify_intent", lambda _message: chat_flow.IntentType.KNOWLEDGE_REQUEST)

    runtime = _runtime()
    runtime._text_only_generate.return_value = "baseline text response"

    assert chat_flow.chat(runtime, "summarize current state") == "baseline text response"
    runtime._text_only_generate.assert_called_once()


def test_governance_event_records_trust_and_decision_log():
    from governance.models import RiskTier
    from orchestrator_governance import record_governance_event

    ledger_calls: list[tuple] = []
    decisions: list[tuple] = []

    class Ledger:
        def get_or_create_record(self, capability, scope, default_tier):
            ledger_calls.append(("ensure", capability, scope, default_tier))

        def record_success(self, capability, scope):
            ledger_calls.append(("success", capability, scope))

        def record_failure(self, capability, scope):
            ledger_calls.append(("failure", capability, scope))

    class DecisionLog:
        def record(self, context, decision, reason):
            decisions.append((context, decision, reason))

    runtime = types.SimpleNamespace(trust_ledger=Ledger(), decision_log=DecisionLog())

    record_governance_event(runtime, "receipt.finalize", "core-spine", RiskTier.T2_CONTROLLED, True)

    assert ledger_calls == [
        ("ensure", "receipt.finalize", "core-spine", RiskTier.T2_CONTROLLED),
        ("success", "receipt.finalize", "core-spine"),
    ]
    context, decision, reason = decisions[0]
    assert context.capability == "receipt.finalize"
    assert context.target == "core-spine"
    assert decision == "approved"
    assert reason == "auto-execution"


def test_receipt_staging_finalization_loading_and_integrity_contract(tmp_path):
    from src.shared.receipts import ActionType, ReceiptService, ReceiptStatus, create_receipt

    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        pending = create_receipt(
            ActionType.SYSTEM,
            "core_spine_baseline",
            {"input": "freeze"},
            quest_id="core-b1",
        )
        staged = service.create(pending)
        finalized = service.update(staged.complete({"result": "ok"}, duration_ms=12))
        loaded = service.get(finalized.id)

        assert staged.status == ReceiptStatus.PENDING.value
        assert finalized.status == ReceiptStatus.SUCCESS.value
        assert loaded is not None
        assert loaded.id == finalized.id
        assert loaded.outputs == {"result": "ok"}
        assert loaded.integrity_hash
        assert loaded.integrity_prev_hash
        assert loaded.integrity_signature
        assert service.validate_integrity_chain(quest_id="core-b1") == []
    finally:
        service.close()


def test_memory_store_create_retrieve_and_reopen_contract(tmp_path):
    from memory.schemas import MemoryItem, MemoryTier, Provenance, ProvenanceType
    from memory.sqlite_store import MemoryStoreManager

    item = MemoryItem(
        id="core-b1-memory",
        tier=MemoryTier.working,
        namespace="core-b1",
        title="Core spine baseline",
        content="Receipt split must preserve memory persistence.",
        tags=["core-b1", "baseline"],
        provenance=[Provenance(type=ProvenanceType.system, ref="CORE-B1")],
    )

    manager = MemoryStoreManager(tmp_path / "memory")
    manager.working.insert(item)
    manager.close_all()

    reopened = MemoryStoreManager(tmp_path / "memory")
    try:
        loaded = reopened.working.get("core-b1-memory")
        results = reopened.search_all("receipt split", tiers=[MemoryTier.working], namespace="core-b1")

        assert loaded is not None
        assert loaded.title == "Core spine baseline"
        assert loaded.provenance[0].ref == "CORE-B1"
        assert [result.id for result in results] == ["core-b1-memory"]
    finally:
        reopened.close_all()


def test_tool_loop_escalated_write_blocks_without_approval(monkeypatch):
    import tool_loop
    from providers.base import ToolCall
    from tests.test_composition_coverage import _FakeToolLoopSelf

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[
            ToolCall(
                name="repo_writer",
                args={"action": "edit", "path": "README.md"},
                id="call-core-b1",
            )
        ],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    runtime._classify_tool_call_safety = lambda *_: "escalate"
    runtime._provider_generate_with_tools = lambda **_: first
    runtime.actioncard_factory = types.SimpleNamespace(from_sentry_request=lambda **_kwargs: None)

    response = tool_loop._agentic_generate(runtime, "edit README", allow_writes=False)

    assert response.startswith("Paused for Commander approval")
    assert runtime._last_tool_receipts[0]["result"].startswith("ESCALATED")


def test_uab_classified_read_only_provider_path_still_reaches_rpc():
    from src.tools.providers.uab_bridge import UABConfig, UABProvider

    provider = UABProvider(config=UABConfig(authority_grant_secret="core-b1-secret"))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls: list[tuple] = []

    def fake_rpc(method, params=None, timeout=None):
        calls.append((method, params))
        return {"success": True, "durationMs": 1, "result": {"tabs": []}}

    provider._rpc_call = fake_rpc

    result = provider.get_tabs(7)

    assert result.success is True
    assert result.result_data == {"tabs": []}
    assert calls == [("act", {"pid": 7, "elementId": "", "action": "getTabs", "params": {}})]
    assert provider.get_denial_events() == []
