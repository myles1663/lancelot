"""Receipt storage tests using real SQLite databases and file operations."""

import os
import uuid
import time
import json
import shutil
import tempfile
import threading
import pytest
from datetime import datetime, timezone, timedelta

from receipts import (
    Receipt, ReceiptService, ReceiptStatus, ActionType, CognitionTier,
    ImmutableReceiptError, create_receipt, get_receipt_service
)


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for tests."""
    temp_dir = tempfile.mkdtemp(prefix="lancelot_test_")
    yield temp_dir
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def service(temp_data_dir):
    """Create a ReceiptService with temporary storage."""
    svc = ReceiptService(data_dir=temp_data_dir)
    yield svc
    svc.close()


class TestReceiptModel:
    """Tests for the Receipt dataclass."""

    def test_create_receipt_with_defaults(self):
        """Receipt creates with sensible defaults."""
        receipt = Receipt()
        
        assert receipt.id is not None
        assert len(receipt.id) == 36  # UUID format
        assert receipt.timestamp is not None
        assert receipt.status == ReceiptStatus.PENDING.value
        assert receipt.tier == CognitionTier.DETERMINISTIC.value
        assert receipt.inputs == {}
        assert receipt.outputs == {}

    def test_create_receipt_with_values(self):
        """Receipt accepts all parameters."""
        quest_id = str(uuid.uuid4())
        receipt = Receipt(
            action_type=ActionType.TOOL_CALL.value,
            action_name="file_write",
            inputs={"path": "/test.txt", "content": "hello"},
            tier=CognitionTier.PLANNING.value,
            quest_id=quest_id
        )
        
        assert receipt.action_type == "tool_call"
        assert receipt.action_name == "file_write"
        assert receipt.inputs["path"] == "/test.txt"
        assert receipt.tier == 2
        assert receipt.quest_id == quest_id

    def test_receipt_to_dict(self):
        """Receipt serializes to dictionary."""
        receipt = Receipt(
            action_name="test_action",
            inputs={"key": "value"}
        )
        data = receipt.to_dict()
        
        assert isinstance(data, dict)
        assert data["action_name"] == "test_action"
        assert data["inputs"]["key"] == "value"

    def test_receipt_from_dict(self):
        """Receipt deserializes from dictionary."""
        data = {
            "id": "test-id",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "action_type": "llm_call",
            "action_name": "generate",
            "inputs": {"prompt": "hello"},
            "outputs": {"response": "world"},
            "status": "success",
            "duration_ms": 1500,
            "token_count": 100,
            "tier": 3,
            "parent_id": None,
            "quest_id": None,
            "error_message": None,
            "metadata": {}
        }
        receipt = Receipt.from_dict(data)
        
        assert receipt.id == "test-id"
        assert receipt.action_type == "llm_call"
        assert receipt.token_count == 100

    def test_receipt_complete(self):
        """Receipt can be marked as complete."""
        receipt = create_receipt(
            ActionType.TOOL_CALL,
            "test_tool",
            {"input": "value"}
        )
        
        completed = receipt.complete(
            outputs={"result": "success"},
            duration_ms=500,
            token_count=50
        )
        
        assert completed.status == ReceiptStatus.SUCCESS.value
        assert completed.outputs["result"] == "success"
        assert completed.duration_ms == 500
        assert completed.token_count == 50
        # Original fields preserved
        assert completed.id == receipt.id
        assert completed.action_name == "test_tool"

    def test_receipt_fail(self):
        """Receipt can be marked as failed."""
        receipt = create_receipt(
            ActionType.FILE_OP,
            "file_read",
            {"path": "/nonexistent"}
        )
        
        failed = receipt.fail(
            error_message="File not found",
            duration_ms=10
        )
        
        assert failed.status == ReceiptStatus.FAILURE.value
        assert failed.error_message == "File not found"
        assert failed.outputs == {}


class TestReceiptService:
    """Tests for the ReceiptService SQLite backend."""

    def test_database_created(self, temp_data_dir):
        """Service creates SQLite database on init."""
        service = ReceiptService(data_dir=temp_data_dir)
        
        db_path = os.path.join(temp_data_dir, "receipts.db")
        assert os.path.exists(db_path)
        service.close()

    def test_create_and_get(self, service):
        """Pending receipts stay staged until finalized into the immutable log."""
        receipt = create_receipt(
            ActionType.TOOL_CALL,
            "test_create",
            {"key": "value"}
        )
        
        service.create(receipt)
        assert service.get(receipt.id) is None

        completed = receipt.complete({"result": "ok"}, duration_ms=25)
        service.update(completed)
        retrieved = service.get(receipt.id)

        assert retrieved is not None
        assert retrieved.id == receipt.id
        assert retrieved.action_name == "test_create"
        assert retrieved.inputs["key"] == "value"
        assert retrieved.status == ReceiptStatus.SUCCESS.value

    def test_update_receipt(self, service):
        """Finalizing a staged receipt writes a single immutable record."""
        receipt = create_receipt(
            ActionType.LLM_CALL,
            "generate",
            {"prompt": "hello"}
        )
        service.create(receipt)
        
        # Complete the receipt
        completed = receipt.complete(
            outputs={"response": "world"},
            duration_ms=1000,
            token_count=50
        )
        service.update(completed)
        
        # Verify update persisted
        retrieved = service.get(receipt.id)
        assert retrieved.status == ReceiptStatus.SUCCESS.value
        assert retrieved.duration_ms == 1000
        assert retrieved.token_count == 50
        assert retrieved.outputs["response"] == "world"

    def test_finalized_receipt_cannot_be_mutated(self, service):
        receipt = create_receipt(
            ActionType.SYSTEM,
            "finalize_once",
            {"value": 1},
        )
        service.create(receipt)
        service.update(receipt.complete({"result": "first"}, duration_ms=1))

        with pytest.raises(ImmutableReceiptError):
            service.update(receipt.complete({"result": "second"}, duration_ms=2))

    def test_finalized_receipt_requires_staged_predecessor(self, service):
        receipt = create_receipt(
            ActionType.SYSTEM,
            "direct_finalize_attempt",
            {"value": 1},
        )

        with pytest.raises(ImmutableReceiptError):
            service.update(receipt.complete({"result": "first"}, duration_ms=1))

        assert service.get(receipt.id) is None

    def test_finalized_receipt_gets_integrity_chain_fields(self, service):
        receipt = create_receipt(
            ActionType.SYSTEM,
            "integrity_root",
            {"value": 1},
        )
        service.create(receipt)
        service.update(receipt.complete({"result": "ok"}, duration_ms=1))

        stored = service.get(receipt.id)

        assert stored is not None
        assert stored.integrity_prev_hash == "0" * 64
        assert stored.integrity_hash is not None
        assert len(stored.integrity_hash) == 64
        assert stored.integrity_key_id is not None
        assert stored.integrity_signature is not None
        assert len(stored.integrity_signature) == 64
        assert service.validate_integrity_chain() == []

    def test_integrity_chain_links_sequential_receipts(self, service):
        first = create_receipt(ActionType.SYSTEM, "first", {"step": 1})
        second = create_receipt(ActionType.SYSTEM, "second", {"step": 2})

        service.create(first)
        stored_first = service.update(first.complete({"result": "ok-1"}, duration_ms=1))
        service.create(second)
        stored_second = service.update(second.complete({"result": "ok-2"}, duration_ms=1))

        assert stored_first.integrity_hash is not None
        assert stored_second.integrity_prev_hash == stored_first.integrity_hash
        assert service.validate_integrity_chain() == []

    def test_validate_integrity_chain_detects_tampering(self, service):
        first = create_receipt(ActionType.SYSTEM, "first", {"step": 1})
        second = create_receipt(ActionType.SYSTEM, "second", {"step": 2})

        service.create(first)
        service.update(first.complete({"result": "ok-1"}, duration_ms=1))
        service.create(second)
        service.update(second.complete({"result": "ok-2"}, duration_ms=1))

        conn = service._get_connection()
        conn.execute(
            "UPDATE receipts SET outputs = ? WHERE id = ?",
            (json.dumps({"result": "tampered"}), second.id),
        )
        conn.commit()

        issues = service.validate_integrity_chain()

        assert any(
            issue["receipt_id"] == second.id
            and issue["issue"] == "integrity_hash_mismatch"
            for issue in issues
        )

    def test_signed_receipt_gets_signature_fields(self, temp_data_dir, monkeypatch):
        monkeypatch.setenv("LANCELOT_RECEIPT_HMAC_KEY", "test-receipt-secret-32-bytes-min!!")
        monkeypatch.setenv("LANCELOT_RECEIPT_HMAC_KEY_ID", "test-key-1")
        service = ReceiptService(data_dir=temp_data_dir)
        try:
            receipt = create_receipt(ActionType.SYSTEM, "signed", {"step": 1})
            service.create(receipt)
            stored = service.update(receipt.complete({"result": "ok"}, duration_ms=1))

            assert stored.integrity_hash is not None
            assert stored.integrity_key_id == "test-key-1"
            assert stored.integrity_signature is not None
            assert len(stored.integrity_signature) == 64
            assert service.validate_integrity_chain() == []
        finally:
            service.close()

    def test_local_signing_key_persists_across_service_restart(self, temp_data_dir):
        first_service = ReceiptService(data_dir=temp_data_dir)
        try:
            receipt = create_receipt(ActionType.SYSTEM, "restart-safe", {"step": 1})
            first_service.create(receipt)
            first_stored = first_service.update(
                receipt.complete({"result": "ok"}, duration_ms=1)
            )
            first_key_id = first_stored.integrity_key_id
        finally:
            first_service.close()

        second_service = ReceiptService(data_dir=temp_data_dir)
        try:
            next_receipt = create_receipt(ActionType.SYSTEM, "restart-safe-2", {"step": 2})
            second_service.create(next_receipt)
            second_stored = second_service.update(
                next_receipt.complete({"result": "ok-2"}, duration_ms=1)
            )

            assert first_key_id is not None
            assert second_stored.integrity_key_id == first_key_id
            assert second_stored.integrity_prev_hash == first_stored.integrity_hash
            assert second_service.validate_integrity_chain() == []
        finally:
            second_service.close()

    def test_validate_integrity_chain_detects_signature_tampering(self, temp_data_dir, monkeypatch):
        monkeypatch.setenv("LANCELOT_RECEIPT_HMAC_KEY", "test-receipt-secret-32-bytes-min!!")
        monkeypatch.setenv("LANCELOT_RECEIPT_HMAC_KEY_ID", "test-key-1")
        service = ReceiptService(data_dir=temp_data_dir)
        try:
            receipt = create_receipt(ActionType.SYSTEM, "signed", {"step": 1})
            service.create(receipt)
            stored = service.update(receipt.complete({"result": "ok"}, duration_ms=1))

            conn = service._get_connection()
            conn.execute(
                "UPDATE receipts SET integrity_signature = ? WHERE id = ?",
                ("0" * 64, stored.id),
            )
            conn.commit()

            issues = service.validate_integrity_chain()
            assert any(
                issue["receipt_id"] == stored.id
                and issue["issue"] == "integrity_signature_mismatch"
                for issue in issues
            )
        finally:
            service.close()

    def test_receipt_service_rejects_undersized_external_signing_keys(self, temp_data_dir, monkeypatch):
        monkeypatch.setenv("LANCELOT_RECEIPT_HMAC_KEY", "too-short")
        with pytest.raises(RuntimeError, match="at least 32 bytes"):
            ReceiptService(data_dir=temp_data_dir)

    def test_validate_integrity_chain_quest_scope_uses_global_chain_order(self, service):
        quest_a = str(uuid.uuid4())
        quest_b = str(uuid.uuid4())

        first = create_receipt(ActionType.SYSTEM, "quest-a-first", {"step": 1}, quest_id=quest_a)
        middle = create_receipt(ActionType.SYSTEM, "quest-b-middle", {"step": 2}, quest_id=quest_b)
        last = create_receipt(ActionType.SYSTEM, "quest-a-last", {"step": 3}, quest_id=quest_a)

        for receipt in (first, middle, last):
            service.create(receipt)
            service.update(receipt.complete({"result": receipt.action_name}, duration_ms=1))

        assert service.validate_integrity_chain(quest_id=quest_a) == []
        assert service.validate_integrity_chain(quest_id=quest_b) == []

    def test_list_receipts(self, service):
        """Can list receipts with pagination."""
        # Create multiple receipts
        for i in range(10):
            receipt = create_receipt(
                ActionType.TOOL_CALL,
                f"action_{i}",
                {"index": i}
            )
            service.create(receipt)
            service.update(receipt.complete({"index": i}, duration_ms=10))
        
        # List first 5
        first_page = service.list(limit=5, offset=0)
        assert len(first_page) == 5
        
        # List next 5
        second_page = service.list(limit=5, offset=5)
        assert len(second_page) == 5
        
        # All different
        first_ids = {r.id for r in first_page}
        second_ids = {r.id for r in second_page}
        assert first_ids.isdisjoint(second_ids)

    def test_list_with_filters(self, service):
        """Can filter receipts by action_type and status."""
        # Create mixed receipts
        tool_receipt = create_receipt(ActionType.TOOL_CALL, "tool", {})
        llm_receipt = create_receipt(ActionType.LLM_CALL, "llm", {})
        
        service.create(tool_receipt)
        service.create(llm_receipt)
        
        # Update one to success
        completed = tool_receipt.complete({}, 100)
        service.update(completed)
        
        # Filter by action_type
        tool_only = service.list(action_type=ActionType.TOOL_CALL.value)
        assert len(tool_only) == 1
        assert tool_only[0].action_type == ActionType.TOOL_CALL.value
        
        # Filter by status
        pending_only = service.list(status=ReceiptStatus.PENDING.value)
        assert pending_only == []

    def test_search_receipts(self, service):
        """Can search receipts by text query."""
        # Create receipts with searchable content
        service.create(create_receipt(
            ActionType.FILE_OP,
            "write_config_file",
            {"path": "/etc/lancelot/config.yaml"}
        ).complete({}, duration_ms=1))
        service.create(create_receipt(
            ActionType.TOOL_CALL,
            "send_email",
            {"to": "user@example.com"}
        ).complete({}, duration_ms=1))
        
        # Search by action name
        results = service.search("config")
        assert len(results) == 1
        assert "config" in results[0].action_name
        
        # Search by input content
        results = service.search("example.com")
        assert len(results) == 1
        assert "example.com" in results[0].inputs["to"]

    def test_quest_receipts(self, service):
        """Can group and retrieve receipts by quest_id."""
        quest_id = str(uuid.uuid4())
        
        # Create quest receipts
        for i in range(3):
            receipt = create_receipt(
                ActionType.PLAN_STEP,
                f"step_{i}",
                {"order": i},
                quest_id=quest_id
            )
            service.create(receipt)
            service.update(receipt.complete({"order": i}, duration_ms=1))
            time.sleep(0.01)  # Ensure ordering
        
        # Create unrelated receipt
        other = create_receipt(ActionType.TOOL_CALL, "other", {})
        service.create(other)
        service.update(other.complete({}, duration_ms=1))
        
        # Get quest receipts
        quest_receipts = service.get_quest_receipts(quest_id)
        assert len(quest_receipts) == 3
        assert all(r.quest_id == quest_id for r in quest_receipts)
        # Ordered by timestamp
        assert quest_receipts[0].action_name == "step_0"
        assert quest_receipts[2].action_name == "step_2"

    def test_parent_child_receipts(self, service):
        """Can link parent and child receipts."""
        parent = create_receipt(
            ActionType.PLAN_STEP,
            "parent_action",
            {}
        )
        service.create(parent)
        service.update(parent.complete({}, duration_ms=1))
        
        # Create children
        for i in range(2):
            child = create_receipt(
                ActionType.TOOL_CALL,
                f"child_{i}",
                {},
                parent_id=parent.id
            )
            service.create(child)
            service.update(child.complete({}, duration_ms=1))
        
        # Get children
        children = service.get_children(parent.id)
        assert len(children) == 2
        assert all(c.parent_id == parent.id for c in children)

    def test_get_stats(self, service):
        """Can get aggregate statistics."""
        # Create varied receipts
        for i in range(5):
            receipt = create_receipt(
                ActionType.LLM_CALL if i % 2 == 0 else ActionType.TOOL_CALL,
                f"action_{i}",
                {}
            )
            service.create(receipt)
            if i < 3:
                service.update(receipt.complete(
                    {},
                    duration_ms=100 * (i + 1),
                    token_count=50 * (i + 1)
                ))
            else:
                service.update(receipt.fail("failed", duration_ms=100 * (i + 1)))

        stats = service.get_stats()
        
        assert stats["total_receipts"] == 5
        assert ActionType.LLM_CALL.value in stats["by_action_type"]
        assert ActionType.TOOL_CALL.value in stats["by_action_type"]
        assert stats["tokens"]["total"] > 0

    def test_delete_old_receipts_blocked(self, service):
        """Immutable receipt log cannot be pruned in place."""
        with pytest.raises(ImmutableReceiptError):
            service.delete_old(days=30)


class TestThreadSafety:
    """Tests for thread-safe operation."""

    def test_concurrent_writes(self, service):
        """Multiple threads can write concurrently."""
        results = []
        errors = []
        
        def writer(thread_id):
            try:
                for i in range(10):
                    receipt = create_receipt(
                        ActionType.TOOL_CALL,
                        f"thread_{thread_id}_action_{i}",
                        {"thread": thread_id, "index": i}
                    )
                    service.create(receipt)
                    service.update(receipt.complete({"thread": thread_id, "index": i}, duration_ms=1))
                results.append(thread_id)
            except Exception as e:
                errors.append((thread_id, e))
        
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 5
        
        # Verify all receipts written
        all_receipts = service.list(limit=100)
        assert len(all_receipts) == 50

    def test_concurrent_read_write(self, service):
        """Can read and write concurrently."""
        # Pre-populate
        for i in range(20):
            receipt = create_receipt(ActionType.SYSTEM, f"initial_{i}", {})
            service.create(receipt)
            service.update(receipt.complete({}, duration_ms=1))
        
        read_results = []
        write_results = []
        
        def reader():
            for _ in range(10):
                receipts = service.list(limit=10)
                read_results.append(len(receipts))
                time.sleep(0.001)
        
        def writer():
            for i in range(10):
                receipt = create_receipt(ActionType.TOOL_CALL, f"new_{i}", {})
                service.create(receipt)
                service.update(receipt.complete({}, duration_ms=1))
                write_results.append(i)
                time.sleep(0.001)
        
        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        
        reader_thread.start()
        writer_thread.start()
        reader_thread.join()
        writer_thread.join()
        
        assert len(read_results) == 10
        assert len(write_results) == 10


class TestReceiptFactoryFunction:
    """Tests for the create_receipt helper function."""

    def test_create_receipt_factory(self):
        """Factory function creates receipt with correct values."""
        quest_id = str(uuid.uuid4())
        receipt = create_receipt(
            action_type=ActionType.VERIFICATION,
            action_name="verify_plan",
            inputs={"plan_id": "123"},
            tier=CognitionTier.PLANNING,
            quest_id=quest_id,
            metadata={"source": "planner"}
        )
        
        assert receipt.action_type == ActionType.VERIFICATION.value
        assert receipt.action_name == "verify_plan"
        assert receipt.tier == CognitionTier.PLANNING.value
        assert receipt.quest_id == quest_id
        assert receipt.metadata["source"] == "planner"
        assert receipt.status == ReceiptStatus.PENDING.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
