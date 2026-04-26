from types import SimpleNamespace

from tool_loop_receipts import persist_tool_call_receipt, receipt_safe_payload


class CapturingReceiptService:
    def __init__(self):
        self.created = []

    def create(self, receipt):
        self.created.append(receipt)


def test_receipt_safe_payload_preserves_json_safe_values():
    payload = {"b": 2, "a": [1, "two"]}

    assert receipt_safe_payload(payload) == payload


def test_receipt_safe_payload_truncates_large_rendered_values():
    safe = receipt_safe_payload({"content": "x" * 50}, limit=20)

    assert safe["truncated"] is True
    assert len(safe["preview"]) == 20


def test_persist_tool_call_receipt_writes_success_receipt_with_runtime_context():
    service = CapturingReceiptService()
    runtime = SimpleNamespace(
        receipt_service=service,
        _current_channel="warroom",
        _current_quest_id="quest-1",
        _current_operator_id="op-1",
        _current_session_id="sess-1",
    )

    persist_tool_call_receipt(
        runtime,
        "repo_writer",
        {"action": "edit", "path": "README.md"},
        "SUCCESS",
        outputs={"path": "README.md"},
        duration_ms=123,
        iteration=2,
    )

    assert len(service.created) == 1
    receipt = service.created[0]
    assert receipt.action_type == "tool_call"
    assert receipt.action_name == "repo_writer"
    assert receipt.status == "success"
    assert receipt.inputs == {
        "tool": "repo_writer",
        "inputs": {"action": "edit", "path": "README.md"},
    }
    assert receipt.outputs == {"path": "README.md"}
    assert receipt.quest_id == "quest-1"
    assert receipt.operator_id == "op-1"
    assert receipt.session_id == "sess-1"
    assert receipt.duration_ms == 123
    assert receipt.metadata == {
        "tool_name": "repo_writer",
        "result": "SUCCESS",
        "channel": "warroom",
        "iteration": 2,
    }


def test_persist_tool_call_receipt_maps_escalated_and_rejected_statuses():
    service = CapturingReceiptService()
    runtime = SimpleNamespace(receipt_service=service)

    persist_tool_call_receipt(
        runtime,
        "repo_writer",
        {"action": "edit"},
        "ESCALATED - needs Commander approval",
        approval_id="approval-1",
    )
    persist_tool_call_receipt(
        runtime,
        "repo_writer",
        {"action": "edit"},
        "REJECTED - repo_writer missing required input(s): path.",
        error="repo_writer missing required input(s): path.",
    )

    assert [receipt.status for receipt in service.created] == ["pending", "failure"]
    assert service.created[0].metadata["approval_id"] == "approval-1"
    assert service.created[1].error_message == "repo_writer missing required input(s): path."


def test_persist_tool_call_receipt_is_noop_without_receipt_service():
    persist_tool_call_receipt(
        SimpleNamespace(receipt_service=None),
        "repo_writer",
        {"action": "edit"},
        "SUCCESS",
    )
