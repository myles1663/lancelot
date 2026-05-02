from __future__ import annotations

import json
import sqlite3

import pytest

from src.shared.receipt_queries import ReceiptQueryMixin


class _ReceiptStore(ReceiptQueryMixin):
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE receipts (
                id TEXT,
                action_type TEXT,
                status TEXT,
                quest_id TEXT,
                operator_id TEXT,
                timestamp TEXT,
                tier INTEGER,
                outputs TEXT
            )
            """
        )

    def _get_connection(self):
        return self.connection

    def _row_to_receipt(self, row):
        return dict(row)

    def insert(self, **values) -> None:
        defaults = {
            "id": "r-1",
            "action_type": "tool_run",
            "status": "success",
            "quest_id": "q-1",
            "operator_id": "op-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "tier": 1,
            "outputs": "{}",
        }
        defaults.update(values)
        self.connection.execute(
            """
            INSERT INTO receipts
            (id, action_type, status, quest_id, operator_id, timestamp, tier, outputs)
            VALUES (:id, :action_type, :status, :quest_id, :operator_id, :timestamp, :tier, :outputs)
            """,
            defaults,
        )
        self.connection.commit()


def test_receipt_list_and_count_apply_all_filters() -> None:
    store = _ReceiptStore()
    store.insert(id="r-1", action_type="tool_run", timestamp="2026-01-01T00:00:00Z", tier=2)
    store.insert(id="r-2", action_type="chat", timestamp="2026-01-02T00:00:00Z", tier=1)

    rows = store.list(
        action_type="tool_run",
        status="success",
        quest_id="q-1",
        operator_id="op-1",
        risk_tier=2,
        since="2026-01-01T00:00:00Z",
        until="2026-01-01T23:59:59Z",
    )

    assert [row["id"] for row in rows] == ["r-1"]
    assert store.count(action_type="tool_run", risk_tier=2) == 1


def test_receipt_list_supports_pagination_and_chronological_order() -> None:
    store = _ReceiptStore()
    store.insert(id="old", timestamp="2026-01-01T00:00:00Z")
    store.insert(id="new", timestamp="2026-01-02T00:00:00Z")

    assert [row["id"] for row in store.list(limit=1)] == ["new"]
    assert [row["id"] for row in store.list_chronological(limit=1, offset=1)] == ["new"]
    assert [row["id"] for row in store.list_chronological(offset=1)] == ["new"]


def test_receipt_aggregate_counts_validates_group_by() -> None:
    store = _ReceiptStore()
    store.insert(id="r-1", action_type="tool_run", operator_id="op-1")
    store.insert(id="r-2", action_type="tool_run", operator_id="op-1")
    store.insert(id="r-3", action_type="chat", operator_id="op-2")

    assert store.aggregate_counts(group_by="operator_id")[0] == {"key": "op-1", "count": 2}
    with pytest.raises(ValueError, match="Unsupported receipt aggregation column"):
        store.aggregate_counts(group_by="outputs")


def test_receipt_action_outputs_parse_json_and_skip_invalid_payloads() -> None:
    store = _ReceiptStore()
    store.insert(id="r-1", action_type="tool_run", outputs=json.dumps({"ok": True}))
    store.insert(id="r-2", action_type="tool_run", outputs="not-json")
    store.insert(id="r-3", action_type="chat", outputs=json.dumps({"ignored": True}))

    assert store.list_action_outputs(action_type="tool_run") == [{"ok": True}]
