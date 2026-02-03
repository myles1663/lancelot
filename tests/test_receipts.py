"""
Lancelot vNext — Receipt Storage Tests
=======================================
Production-ready tests for the receipt system.
Uses real SQLite database, real file operations.
"""

import os
import uuid
import time
import shutil
import tempfile
import threading
import pytest
from datetime import datetime, timezone, timedelta

from receipts import (
    Receipt, ReceiptService, ReceiptStatus, ActionType, CognitionTier,
    create_receipt, get_receipt_service
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
        """Can create and retrieve a receipt."""
        receipt = create_receipt(
            ActionType.TOOL_CALL,
            "test_create",
            {"key": "value"}
        )
        
        service.create(receipt)
        retrieved = service.get(receipt.id)
        
        assert retrieved is not None
        assert retrieved.id == receipt.id
        assert retrieved.action_name == "test_create"
        assert retrieved.inputs["key"] == "value"

    def test_update_receipt(self, service):
        """Can update an existing receipt."""
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
        assert all(r.status == ReceiptStatus.PENDING.value for r in pending_only)

    def test_search_receipts(self, service):
        """Can search receipts by text query."""
        # Create receipts with searchable content
        service.create(create_receipt(
            ActionType.FILE_OP,
            "write_config_file",
            {"path": "/etc/lancelot/config.yaml"}
        ))
        service.create(create_receipt(
            ActionType.TOOL_CALL,
            "send_email",
            {"to": "user@example.com"}
        ))
        
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
            time.sleep(0.01)  # Ensure ordering
        
        # Create unrelated receipt
        service.create(create_receipt(ActionType.TOOL_CALL, "other", {}))
        
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
        
        # Create children
        for i in range(2):
            child = create_receipt(
                ActionType.TOOL_CALL,
                f"child_{i}",
                {},
                parent_id=parent.id
            )
            service.create(child)
        
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
            if i < 3:
                receipt = receipt.complete(
                    {},
                    duration_ms=100 * (i + 1),
                    token_count=50 * (i + 1)
                )
            service.create(receipt)
        
        stats = service.get_stats()
        
        assert stats["total_receipts"] == 5
        assert ActionType.LLM_CALL.value in stats["by_action_type"]
        assert ActionType.TOOL_CALL.value in stats["by_action_type"]
        assert stats["tokens"]["total"] > 0

    def test_delete_old_receipts(self, service):
        """Can delete receipts older than threshold."""
        # Create old receipt (manually set timestamp)
        old_receipt = Receipt(
            timestamp=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
            action_name="old_action",
            action_type=ActionType.SYSTEM.value
        )
        service.create(old_receipt)
        
        # Create recent receipt
        recent_receipt = create_receipt(ActionType.SYSTEM, "recent", {})
        service.create(recent_receipt)
        
        # Delete old (30 days threshold)
        deleted = service.delete_old(days=30)
        assert deleted == 1
        
        # Verify
        assert service.get(old_receipt.id) is None
        assert service.get(recent_receipt.id) is not None


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
            service.create(create_receipt(ActionType.SYSTEM, f"initial_{i}", {}))
        
        read_results = []
        write_results = []
        
        def reader():
            for _ in range(10):
                receipts = service.list(limit=10)
                read_results.append(len(receipts))
                time.sleep(0.001)
        
        def writer():
            for i in range(10):
                service.create(create_receipt(ActionType.TOOL_CALL, f"new_{i}", {}))
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
