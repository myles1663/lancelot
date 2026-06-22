"""Canonical Core receipt integration for UAB action outcomes."""

from __future__ import annotations

from src.core.execution_authority import create_uab_authority_grant
from src.core.uab_runtime_adapter import create_uab_provider
from src.shared.receipts import ActionType, ReceiptStatus
from src.shared.receipts_service import ReceiptService
from src.tools.fabric import ToolFabric, ToolFabricConfig
from src.tools.providers.uab_bridge import UABConfig, UABProvider
from src.tools.receipts_uab import (
    UAB_CANONICAL_RECEIPT_SOURCE,
    build_uab_receipt_metadata,
    emit_uab_canonical_receipt,
)


SECRET = "uab-canonical-receipt-test-secret"


def _grant(action: str, *, selector_scope: str = "edit1", run_id: str = "run-1"):
    return create_uab_authority_grant(
        secret=SECRET,
        risk_tier="T2_CONTROLLED",
        uab_risk="moderate",
        capability=f"uab_{action}",
        app_name="Notepad",
        app_pid=7,
        action=action,
        selector_scope=selector_scope,
        policy_version="policy-v1",
        soul_version="soul-v1",
        workflow_id="workflow-1",
        run_id=run_id,
        parent_receipt_id="parent-1",
        approval_id="approval-1",
        mutating=True,
    ).to_dict()


def _receipt_context(**overrides):
    values = {
        "appName": "Notepad",
        "appPid": 7,
        "selectorScope": "edit1",
        "riskTier": "T2_CONTROLLED",
        "uabRisk": "moderate",
        "parentReceiptId": "parent-denied-1",
        "workflowId": "workflow-denied-1",
        "runId": "run-denied-1",
        "mutating": True,
    }
    values.update(overrides)
    return values


def _provider(service: ReceiptService) -> UABProvider:
    provider = UABProvider(
        config=UABConfig(authority_grant_secret=SECRET),
        receipt_service=service,
    )
    provider._connected_apps[7] = {"name": "Notepad"}
    return provider


def test_emit_uab_canonical_receipt_persists_via_receipt_service(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        metadata = build_uab_receipt_metadata(
            outcome="success",
            grant=_grant("type"),
        )

        stored = emit_uab_canonical_receipt(
            metadata,
            receipt_service=service,
            duration_ms=12,
        )
        loaded = service.get(stored.id)

        assert loaded is not None
        assert loaded.action_type == ActionType.UAB_ACTION.value
        assert loaded.status == ReceiptStatus.SUCCESS.value
        assert loaded.parent_id == "parent-1"
        assert loaded.quest_id == "workflow-1"
        assert loaded.session_id == "run-1"
        assert loaded.metadata["canonical_receipt_source"] == UAB_CANONICAL_RECEIPT_SOURCE
        assert loaded.metadata["local_uab_audit_is_canonical"] is False
        assert loaded.metadata["uab_receipt_metadata"]["grant_id"] == metadata.grant_id
        assert "canonical_receipt_deferred_to" not in loaded.metadata["uab_receipt_metadata"]
        assert service.validate_integrity_chain(quest_id="workflow-1") == []
    finally:
        service.close()


def test_core_adapter_created_uab_provider_emits_canonical_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("UAB_AUTHORITY_GRANT_SECRET", SECRET)
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = create_uab_provider(receipt_service=service)
        provider._connected_apps[7] = {"name": "Notepad"}
        grant = _grant("type", run_id="run-adapter-1")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": True,
            "durationMs": 4,
            "result": {"adapter": True},
        }

        result = provider.act(7, "edit1", "type", {"text": "adapter", "uabAuthorityGrant": grant})
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is True
        assert result.result_data == {"adapter": True}
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.SUCCESS.value
        assert receipt.session_id == "run-adapter-1"
        assert receipt.metadata["canonical_receipt_source"] == UAB_CANONICAL_RECEIPT_SOURCE
        assert receipt.metadata["local_uab_audit_is_canonical"] is False
        assert receipt.metadata["uab_receipt_metadata"]["grant_id"] == grant["grant_id"]
    finally:
        service.close()


def test_tool_fabric_uab_provider_emits_canonical_receipts(tmp_path, monkeypatch):
    import src.shared.receipts_service as receipts_service_module
    import src.tools.fabric as fabric_module

    monkeypatch.setenv("UAB_AUTHORITY_GRANT_SECRET", SECRET)
    monkeypatch.setattr(fabric_module, "FEATURE_TOOLS_UAB", True)
    service = ReceiptService(str(tmp_path / "receipts"))
    monkeypatch.setattr(receipts_service_module, "get_receipt_service", lambda: service)
    try:
        fabric = ToolFabric(ToolFabricConfig(enabled=False))
        provider = fabric._health_monitor.get_provider("uab_bridge")

        assert provider is not None
        assert getattr(provider, "_receipt_service") is service

        provider._connected_apps[7] = {"name": "Notepad"}
        responses = iter(
            [
                {
                    "success": True,
                    "durationMs": 6,
                    "result": {"tool_fabric": "success"},
                },
                {
                    "success": False,
                    "durationMs": 7,
                    "error": "daemon rejected tool fabric action",
                    "result": {"tool_fabric": "failed"},
                },
            ]
        )
        provider._rpc_call = lambda method, params=None, timeout=None: next(responses)

        success_grant = _grant("type", run_id="run-tool-fabric-success")
        failure_grant = _grant("type", selector_scope="edit2", run_id="run-tool-fabric-failed")
        success = provider.act(
            7,
            "edit1",
            "type",
            {"text": "success", "uabAuthorityGrant": success_grant},
        )
        failure = provider.act(
            7,
            "edit2",
            "type",
            {"text": "failure", "uabAuthorityGrant": failure_grant},
        )
        denial = provider.act(
            7,
            "edit3",
            "type",
            {"uabReceiptContext": _receipt_context(selectorScope="edit3")},
        )

        workflow_receipts = service.get_quest_receipts("workflow-1")
        denied_receipts = service.get_quest_receipts("workflow-denied-1")

        assert success.success is True
        assert failure.success is False
        assert denial.success is False
        assert [receipt.status for receipt in workflow_receipts] == [
            ReceiptStatus.SUCCESS.value,
            ReceiptStatus.FAILURE.value,
        ]
        assert workflow_receipts[0].metadata["grant_id"] == success_grant["grant_id"]
        assert workflow_receipts[1].metadata["grant_id"] == failure_grant["grant_id"]
        assert workflow_receipts[1].error_message == "daemon rejected tool fabric action"
        assert len(denied_receipts) == 1
        assert denied_receipts[0].metadata["uab_receipt_metadata"]["outcome"] == "denied"
        assert denied_receipts[0].metadata["local_uab_audit_is_canonical"] is False
    finally:
        service.close()


def test_tool_fabric_omits_uab_provider_when_feature_disabled(monkeypatch):
    import src.tools.fabric as fabric_module

    monkeypatch.setattr(fabric_module, "FEATURE_TOOLS_UAB", False)

    fabric = ToolFabric(ToolFabricConfig(enabled=False))

    assert fabric._health_monitor.get_provider("uab_bridge") is None


def test_provider_successful_uab_action_emits_canonical_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("type")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": True,
            "durationMs": 3,
            "result": {"ok": True},
        }

        result = provider.act(7, "edit1", "type", {"text": "hello", "uabAuthorityGrant": grant})
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is True
        assert result.result_data == {"ok": True}
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.SUCCESS.value
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.outputs["uab_receipt_metadata"]["outcome"] == "success"
        assert receipt.outputs["uab_receipt_metadata"]["local_uab_audit_is_canonical"] is False
    finally:
        service.close()


def test_provider_failed_uab_action_emits_canonical_failure_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("type", run_id="run-failed-1")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 5,
            "error": "daemon rejected action",
            "result": {"ok": False},
        }

        result = provider.act(7, "edit1", "type", {"uabAuthorityGrant": grant})
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is False
        assert result.error_message == "daemon rejected action"
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.error_message == "daemon rejected action"
        assert receipt.session_id == "run-failed-1"
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
        assert receipt.metadata["uab_receipt_metadata"]["grant_id"] == grant["grant_id"]
    finally:
        service.close()


def test_receipt_query_distinguishes_denied_from_failed_uab_outcomes(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("type", run_id="run-failed-audit")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 5,
            "error": "daemon rejected action",
        }

        failed = provider.act(7, "edit1", "type", {"uabAuthorityGrant": grant})
        denied = provider.act(
            7,
            "edit2",
            "type",
            {
                "uabReceiptContext": _receipt_context(
                    selectorScope="edit2",
                    workflowId="workflow-denied-audit",
                    runId="run-denied-audit",
                )
            },
        )

        failed_receipt = service.get_quest_receipts("workflow-1")[0]
        denied_receipt = service.get_quest_receipts("workflow-denied-audit")[0]

        assert failed.success is False
        assert denied.success is False
        assert failed_receipt.status == ReceiptStatus.FAILURE.value
        assert denied_receipt.status == ReceiptStatus.FAILURE.value
        assert failed_receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
        assert failed_receipt.metadata["uab_receipt_metadata"]["error_reason"] == "daemon rejected action"
        assert denied_receipt.metadata["uab_receipt_metadata"]["outcome"] == "denied"
        assert denied_receipt.metadata["uab_receipt_metadata"]["denial_reason"] == denied.error_message
    finally:
        service.close()


def test_provider_act_false_without_error_emits_canonical_failure_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("type", run_id="run-act-false-no-error")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 6,
            "result": {"ok": False},
        }

        result = provider.act(7, "edit1", "type", {"uabAuthorityGrant": grant})
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is False
        assert result.error_message is None
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.error_message == "UAB action 'type' failed without daemon error detail"
        assert receipt.session_id == "run-act-false-no-error"
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
        assert receipt.metadata["uab_receipt_metadata"]["error_reason"] == receipt.error_message
    finally:
        service.close()


def test_provider_denied_uab_action_emits_canonical_denial_receipt_when_context_supplied(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        calls = []
        provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

        result = provider.act(
            7,
            "edit1",
            "type",
            {"uabReceiptContext": _receipt_context()},
        )
        receipts = service.get_quest_receipts("workflow-denied-1")

        assert result.success is False
        assert calls == []
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.parent_id == "parent-denied-1"
        assert receipt.session_id == "run-denied-1"
        assert receipt.error_message == "UAB authority grant required for provider action 'type'"
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "denied"
        assert receipt.metadata["uab_receipt_metadata"]["denial_reason"] == result.error_message
        assert receipt.metadata["local_uab_audit_is_canonical"] is False
    finally:
        service.close()


def test_non_act_keypress_success_emits_canonical_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("keypress", selector_scope="", run_id="run-keypress-success")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": True,
            "durationMs": 8,
            "result": {"key": "enter"},
        }

        result = provider.keypress(7, "enter", uab_authority_grant=grant)
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is True
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.action_type == ActionType.UAB_ACTION.value
        assert receipt.status == ReceiptStatus.SUCCESS.value
        assert receipt.session_id == "run-keypress-success"
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.metadata["uab_receipt_metadata"]["action"] == "keypress"
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "success"
        assert "canonical_receipt_deferred_to" not in receipt.metadata["uab_receipt_metadata"]
    finally:
        service.close()


def test_non_act_dict_failure_emits_canonical_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("atomicChain", selector_scope="", run_id="run-atomic-failed")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 9,
            "error": "atomic chain rejected",
            "result": {"steps": 0},
        }

        result = provider.atomic_chain(
            7,
            [{"action": "keypress", "key": "enter"}],
            uab_authority_grant=grant,
        )
        receipts = service.get_quest_receipts("workflow-1")

        assert result["success"] is False
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.error_message == "atomic chain rejected"
        assert receipt.session_id == "run-atomic-failed"
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.metadata["uab_receipt_metadata"]["action"] == "atomicChain"
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
    finally:
        service.close()


def test_non_act_keypress_false_without_error_emits_canonical_failure_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("keypress", selector_scope="", run_id="run-keypress-false-no-error")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 10,
            "result": {"ok": False},
        }

        result = provider.keypress(7, "enter", uab_authority_grant=grant)
        receipts = service.get_quest_receipts("workflow-1")

        assert result.success is False
        assert result.error_message is None
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.error_message == "UAB action 'keypress' failed without daemon error detail"
        assert receipt.session_id == "run-keypress-false-no-error"
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
        assert receipt.metadata["uab_receipt_metadata"]["error_reason"] == receipt.error_message
    finally:
        service.close()


def test_non_act_dict_false_without_error_emits_canonical_failure_receipt(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        grant = _grant("atomicChain", selector_scope="", run_id="run-atomic-false-no-error")
        provider._rpc_call = lambda method, params=None, timeout=None: {
            "success": False,
            "durationMs": 11,
            "result": {"ok": False},
        }

        result = provider.atomic_chain(
            7,
            [{"action": "keypress", "key": "enter"}],
            uab_authority_grant=grant,
        )
        receipts = service.get_quest_receipts("workflow-1")

        assert result["success"] is False
        assert "error" not in result
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.error_message == "UAB action 'atomicChain' failed without daemon error detail"
        assert receipt.session_id == "run-atomic-false-no-error"
        assert receipt.metadata["grant_id"] == grant["grant_id"]
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "failed"
        assert receipt.metadata["uab_receipt_metadata"]["error_reason"] == receipt.error_message
    finally:
        service.close()


def test_non_act_dict_denial_emits_canonical_receipt_when_context_supplied(tmp_path):
    service = ReceiptService(str(tmp_path / "receipts"))
    try:
        provider = _provider(service)
        calls = []
        provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

        result = provider.execute_chain(
            {
                "pid": 7,
                "steps": [{"action": "keypress", "key": "enter"}],
                "uabReceiptContext": _receipt_context(
                    selectorScope="chain-denied",
                    runId="run-chain-denied",
                    parentReceiptId="parent-chain-denied",
                    workflowId="workflow-chain-denied",
                ),
            }
        )
        receipts = service.get_quest_receipts("workflow-chain-denied")

        assert result["success"] is False
        assert calls == []
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.status == ReceiptStatus.FAILURE.value
        assert receipt.parent_id == "parent-chain-denied"
        assert receipt.session_id == "run-chain-denied"
        assert receipt.metadata["uab_receipt_metadata"]["action"] == "chain"
        assert receipt.metadata["uab_receipt_metadata"]["outcome"] == "denied"
        assert receipt.metadata["uab_receipt_metadata"]["denial_reason"] == result["error"]
        assert receipt.metadata["local_uab_audit_is_canonical"] is False
    finally:
        service.close()
