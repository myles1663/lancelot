from __future__ import annotations

import json

from src.tools.contracts import RiskLevel
from src.tools.receipts_uab import (
    AppControlReceipt,
    AppControlReceiptStore,
    AppSessionEntry,
    create_app_control_receipt,
    get_uab_receipt_store,
    reset_uab_receipt_store,
)


def test_app_control_receipt_round_trip_and_failure_state():
    receipt = AppControlReceipt(
        app_name="Notepad",
        app_pid=7,
        action_type="act",
        action_performed="type",
        element_id="edit1",
        chain_id="chain-1",
    )

    receipt.fail("daemon offline")
    restored = AppControlReceipt.from_dict(
        {
            **receipt.to_dict(),
            "ignored_future_field": "ignored",
        }
    )

    assert restored.success is False
    assert restored.error_message == "daemon offline"
    assert restored.action_performed == "type"
    assert not hasattr(restored, "ignored_future_field")


def test_session_entry_rolls_up_actions_and_risk():
    session = AppSessionEntry(app_name="Notepad", app_pid=7)
    read_receipt = AppControlReceipt(
        app_name="Notepad",
        app_pid=7,
        action_type="query",
        action_performed="getText",
        element_id="field-1",
        risk_level=RiskLevel.LOW.value,
    )
    write_receipt = AppControlReceipt(
        app_name="Notepad",
        app_pid=7,
        action_type="act",
        action_performed="type",
        mutating=True,
        element_id="field-1",
        risk_level=RiskLevel.HIGH.value,
    )

    session.record_action(read_receipt)
    session.record_action(write_receipt)
    session.close()
    restored = AppSessionEntry.from_dict(session.to_dict())

    assert restored.total_actions == 2
    assert restored.read_only_actions == 1
    assert restored.mutating_actions == 1
    assert restored.action_summary == {"getText": 1, "type": 1}
    assert restored.elements_touched == ["field-1"]
    assert restored.max_risk_level == RiskLevel.HIGH.value
    assert restored.disconnected_at is not None


def test_receipt_store_persists_filters_and_summarizes_sessions(tmp_path):
    store = AppControlReceiptStore(data_dir=str(tmp_path))
    session = store.start_session(
        7,
        "Notepad",
        framework="uia",
        connection_method="mcp",
    )
    read_receipt = AppControlReceipt(
        app_name="Notepad",
        app_pid=7,
        action_type="query",
        action_performed="getText",
        chain_id="chain-1",
    )
    write_receipt = AppControlReceipt(
        app_name="Notepad",
        app_pid=7,
        action_type="act",
        action_performed="type",
        mutating=True,
        chain_id="chain-1",
        element_id="edit1",
    )
    other_app_receipt = AppControlReceipt(
        app_name="Calculator",
        app_pid=8,
        action_type="query",
        action_performed="getText",
    )

    store.store_receipt(read_receipt)
    store.store_receipt(write_receipt)
    store.store_receipt(other_app_receipt)

    receipt_path = tmp_path / "receipts" / "uab" / f"{write_receipt.receipt_id}.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["receipt_id"] == write_receipt.receipt_id
    assert store.get_active_sessions()[7].session_id == session.session_id
    assert store.get_recent_receipts(app_name="notepad") == [write_receipt, read_receipt]
    assert store.get_recent_receipts(mutating_only=True) == [write_receipt]
    assert store.get_recent_receipts(action_type="getText") == [other_app_receipt, read_receipt]
    assert store.get_receipts_for_chain("chain-1") == [read_receipt, write_receipt]

    active_summaries = store.get_session_summaries()
    assert active_summaries[0]["active"] is True
    assert active_summaries[0]["total_actions"] == 2

    ended = store.end_session(7)
    assert ended is not None
    assert ended.total_actions == 2
    assert store.end_session(999) is None
    persisted_summaries = store.get_session_summaries()
    assert persisted_summaries[0]["active"] is False
    assert persisted_summaries[0]["session_id"] == session.session_id


def test_receipt_store_limits_recent_cache(tmp_path):
    store = AppControlReceiptStore(data_dir=str(tmp_path))
    store._max_recent = 2

    receipts = [
        AppControlReceipt(app_name="Notepad", app_pid=7, action_type="query")
        for _ in range(3)
    ]
    for receipt in receipts:
        store.store_receipt(receipt)

    assert store.get_recent_receipts(limit=10) == [receipts[2], receipts[1]]


def test_create_app_control_receipt_computes_and_allows_overrides():
    auto = create_app_control_receipt(
        "act",
        app_name="Notepad",
        app_pid=7,
        action_performed="type",
    )
    overridden = create_app_control_receipt(
        "query",
        app_name="Notepad",
        app_pid=7,
        action_performed="getText",
        mutating=True,
        risk_level=RiskLevel.HIGH.value,
    )

    assert auto.mutating is True
    assert auto.risk_level in {RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value}
    assert overridden.mutating is True
    assert overridden.risk_level == RiskLevel.HIGH.value


def test_uab_receipt_store_singleton_resets(tmp_path):
    reset_uab_receipt_store()
    first = get_uab_receipt_store(data_dir=str(tmp_path / "first"))
    second = get_uab_receipt_store(data_dir=str(tmp_path / "second"))
    reset_uab_receipt_store()
    third = get_uab_receipt_store(data_dir=str(tmp_path / "third"))

    assert first is second
    assert third is not first

    reset_uab_receipt_store()
