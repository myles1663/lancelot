from types import SimpleNamespace

from chat_flow import _should_record_task_experience
from orchestrator_ext import _record_task_experience


class _EpisodicSink:
    def __init__(self):
        self.items = []

    def insert(self, item):
        self.items.append(item)


def test_should_record_task_experience_skips_short_acknowledgements():
    assert (
        _should_record_task_experience(
            "ok",
            "Understood.",
            tool_receipts=[],
            wants_action=False,
        )
        is False
    )


def test_should_record_task_experience_keeps_meaningful_repeat_tasks():
    assert (
        _should_record_task_experience(
            "Review the memory retrieval ranking and update the proof tests",
            "I updated the ranking tests.",
            tool_receipts=[],
            wants_action=True,
        )
        is True
    )


def test_record_task_experience_uses_context_compiler_memory_and_provenance():
    sink = _EpisodicSink()
    runtime = SimpleNamespace(
        context_compiler=SimpleNamespace(
            memory_manager=SimpleNamespace(episodic=sink),
        ),
        _current_quest_id="quest-1",
        _current_session_id="sess-1",
        _current_operator_id="op-1",
        _current_operator_name="Operator One",
        _current_channel="warroom",
    )

    _record_task_experience(
        runtime,
        user_message="Update repeat task runbooks and add proof tests",
        response_text="Updated the runbooks and proof tests.",
        tool_receipts=[
            {
                "id": "receipt-1",
                "skill": "repo_writer",
                "result": "SUCCESS",
                "metadata": {
                    "target_path": "src/core/memory/sqlite_store.py",
                    "approval_request_id": "approval-1",
                    "workflow_id": "workflow-memory-proof",
                    "retry_count": 2,
                },
            }
        ],
        duration_ms=1234,
    )

    assert len(sink.items) == 1
    item = sink.items[0]
    assert item.namespace == "task_experience"
    assert item.metadata["outcome"] == "success"
    assert item.metadata["quest_id"] == "quest-1"
    assert item.metadata["session_id"] == "sess-1"
    assert item.metadata["operator_id"] == "op-1"
    assert item.metadata["operator_name"] == "Operator One"
    assert item.metadata["channel"] == "warroom"
    assert item.metadata["receipt_ids"] == ["receipt-1"]
    assert item.metadata["tools_succeeded"] == ["repo_writer"]
    assert item.metadata["files_touched"] == ["src/core/memory/sqlite_store.py"]
    assert item.metadata["approvals"] == ["approval-1"]
    assert item.metadata["elapsed_ms"] == 1234
    assert item.metadata["retries"] == 2
    assert item.metadata["workflow_id"] == "workflow-memory-proof"
    assert "channel:warroom" in item.tags
    assert "operator:op-1" in item.tags
    assert "quest:quest-1" in item.tags
    assert "workflow:workflow-memory-proof" in item.tags
    assert any(provenance.ref == "receipt-1" for provenance in item.provenance)
