"""Tests for HIVE receipt helpers."""

from src.hive.receipts import emit_hive_receipt
from src.shared.receipts import get_receipt_service


def test_emit_hive_receipt_persists_finalized_event(tmp_path):
    receipt = emit_hive_receipt(
        event_type="task",
        action_name="task_spawned",
        inputs={"task_id": "task-1"},
        data_dir=str(tmp_path),
    )

    persisted = get_receipt_service(str(tmp_path)).get(receipt.id)
    assert persisted is not None
    assert persisted.action_type == "hive_task_event"
    assert persisted.status == "success"
