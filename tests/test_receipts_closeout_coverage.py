from __future__ import annotations

import sqlite3

import pytest

from receipts import (
    ActionType,
    CognitionTier,
    ImmutableReceiptError,
    ReceiptService,
    ReceiptStatus,
    create_finalized_receipt,
    create_receipt,
    get_receipt_service,
)
from receipts_migrations import ensure_receipt_schema


def _finalize(service: ReceiptService, receipt):
    service.create(receipt)
    return service.update(receipt.complete({"result": receipt.action_name}, duration_ms=3))


def test_old_receipt_schema_migration_adds_receipt_and_staging_columns(tmp_path):
    db_path = tmp_path / "receipts.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE receipts (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_name TEXT NOT NULL,
                inputs TEXT NOT NULL,
                outputs TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER,
                token_count INTEGER,
                tier INTEGER NOT NULL DEFAULT 0,
                parent_id TEXT,
                quest_id TEXT,
                error_message TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE receipt_staging (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_name TEXT NOT NULL,
                inputs TEXT NOT NULL,
                outputs TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER,
                token_count INTEGER,
                tier INTEGER NOT NULL DEFAULT 0,
                parent_id TEXT,
                quest_id TEXT,
                error_message TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        ensure_receipt_schema(conn)
        ensure_receipt_schema(conn)

        receipt_columns = {row[1] for row in conn.execute("PRAGMA table_info(receipts)")}
        staging_columns = {row[1] for row in conn.execute("PRAGMA table_info(receipt_staging)")}

        for column in {
            "operator_id",
            "session_id",
            "integrity_prev_hash",
            "integrity_hash",
            "integrity_key_id",
            "integrity_signature",
        }:
            assert column in receipt_columns
            assert column in staging_columns
    finally:
        conn.close()


def test_receipt_service_search_count_parent_summary_and_stats_filters(tmp_path):
    service = ReceiptService(data_dir=str(tmp_path))
    try:
        parent = _finalize(
            service,
            create_receipt(
                ActionType.PLAN_STEP,
                "parent-closeout",
                {"body": "alpha"},
                quest_id="quest-closeout",
            ),
        )
        child = _finalize(
            service,
            create_receipt(
                ActionType.TOOL_CALL,
                "child-closeout",
                {"body": "alpha child"},
                tier=CognitionTier.SYNTHESIS,
                parent_id=parent.id,
                quest_id="quest-closeout",
            ),
        )
        orphan = _finalize(
            service,
            create_receipt(
                ActionType.TOOL_CALL,
                "orphan-closeout",
                {"body": "alpha orphan"},
                tier=CognitionTier.SYNTHESIS,
                parent_id="missing-parent",
                quest_id="quest-closeout",
            ),
        )

        results = service.search(
            "alpha",
            action_types=[ActionType.TOOL_CALL.value],
            status=ReceiptStatus.SUCCESS.value,
            quest_id="quest-closeout",
            risk_tier=CognitionTier.SYNTHESIS.value,
            since="2000-01-01T00:00:00+00:00",
            until="2999-01-01T00:00:00+00:00",
            time_range_hours=1,
        )
        count = service.count_search(
            "alpha",
            action_types=[ActionType.TOOL_CALL.value],
            status=ReceiptStatus.SUCCESS.value,
            quest_id="quest-closeout",
            risk_tier=CognitionTier.SYNTHESIS.value,
            since="2000-01-01T00:00:00+00:00",
            until="2999-01-01T00:00:00+00:00",
            time_range_hours=1,
        )
        gaps = service.validate_parent_chain(quest_id="quest-closeout")
        summary = service.summarize_parent_chain(
            since="2000-01-01T00:00:00+00:00",
            until="2999-01-01T00:00:00+00:00",
            quest_id="quest-closeout",
        )
        stats = service.get_stats(
            since="2000-01-01T00:00:00+00:00",
            quest_id="quest-closeout",
        )

        assert {receipt.id for receipt in results} == {child.id, orphan.id}
        assert count == 2
        assert gaps == [{"receipt_id": orphan.id, "orphaned_parent_id": "missing-parent"}]
        assert summary["total_receipts"] == 3
        assert summary["receipts_with_parents"] == 2
        assert summary["missing_parent_gaps"][0]["receipt_id"] == orphan.id
        assert stats["total_receipts"] == 3
    finally:
        service.close()


def test_receipt_service_transaction_rollback_and_clear_are_fail_closed(tmp_path):
    service = ReceiptService(data_dir=str(tmp_path))
    try:
        conn = service._get_connection()
        conn.execute("CREATE TABLE rollback_marker (id TEXT)")
        conn.commit()

        with pytest.raises(RuntimeError):
            with service._transaction() as conn:
                conn.execute("INSERT INTO rollback_marker (id) VALUES ('should-rollback')")
                raise RuntimeError("force rollback")

        marker = conn.execute(
            "SELECT id FROM rollback_marker WHERE id = 'should-rollback'"
        ).fetchone()

        assert marker is None
        with pytest.raises(ImmutableReceiptError, match="append-only"):
            service.clear()
    finally:
        service.close()


def test_validate_integrity_chain_reports_missing_fields_and_key_mismatch(tmp_path):
    service = ReceiptService(data_dir=str(tmp_path))
    try:
        missing = _finalize(
            service,
            create_receipt(ActionType.SYSTEM, "missing-integrity", {}),
        )
        key_mismatch = _finalize(
            service,
            create_receipt(ActionType.SYSTEM, "key-mismatch", {}),
        )

        conn = service._get_connection()
        conn.execute(
            "UPDATE receipts SET integrity_hash = NULL WHERE id = ?",
            (missing.id,),
        )
        conn.execute(
            "UPDATE receipts SET integrity_key_id = ? WHERE id = ?",
            ("wrong-key", key_mismatch.id),
        )
        conn.commit()

        issues = service.validate_integrity_chain()

        assert any(
            issue["receipt_id"] == missing.id
            and issue["issue"] == "missing_integrity_fields"
            for issue in issues
        )
        assert any(
            issue["receipt_id"] == key_mismatch.id
            and issue["issue"] == "integrity_key_id_mismatch"
            for issue in issues
        )
    finally:
        service.close()


def test_create_finalized_receipt_and_singleton_service(tmp_path):
    receipt = create_finalized_receipt(
        ActionType.SYSTEM,
        "closeout-finalized",
        {"input": "value"},
        outputs={"ok": True},
        status=ReceiptStatus.SUCCESS,
        tier=CognitionTier.DETERMINISTIC,
        parent_id="parent-1",
        quest_id="quest-1",
        metadata={"source": "closeout"},
        operator_id="operator-1",
        session_id="session-1",
        duration_ms=4,
        token_count=5,
    )

    import receipts_service as receipts_service_module

    receipts_service_module._service_instance = None
    first = get_receipt_service(data_dir=str(tmp_path / "first"))
    second = get_receipt_service(data_dir=str(tmp_path / "second"))
    try:
        assert receipt.status == ReceiptStatus.SUCCESS.value
        assert receipt.outputs == {"ok": True}
        assert receipt.parent_id == "parent-1"
        assert receipt.quest_id == "quest-1"
        assert receipt.operator_id == "operator-1"
        assert receipt.session_id == "session-1"
        assert first is second
    finally:
        first.close()
        receipts_service_module._service_instance = None
