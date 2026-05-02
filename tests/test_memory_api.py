"""
Tests for structured memory REST API.

These tests validate:
- Core block endpoints
- Memory search endpoint
- Commit workflow endpoints
- Quarantine endpoints
- Context compiler endpoint
- Stats endpoint
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.core import api_auth
from src.core.memory.api import router, get_memory_service, _memory_service, _get_memory_data_dir
from src.core.memory.schemas import (
    CoreBlockType,
    MemoryTier,
    MemoryStatus,
    ProvenanceType,
    MemoryItem,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def app(temp_data_dir, monkeypatch):
    """Create a FastAPI app with memory router."""
    import src.core.memory.api as api_module

    # Reset the singleton so previous test runs don't leak state
    original_service = api_module._memory_service
    api_module._memory_service = None

    # Build a test-local service dict bound to the temp directory
    _test_service = None

    def patched_get_memory_service():
        nonlocal _test_service
        if _test_service is None:
            from src.core.memory.store import CoreBlockStore
            from src.core.memory.sqlite_store import MemoryStoreManager
            from src.core.memory.commits import CommitManager
            from src.core.memory.gates import WriteGateValidator, QuarantineManager
            from src.core.memory.index import MemoryIndex
            from src.core.memory.compiler import ContextCompilerService

            core_store = CoreBlockStore(data_dir=temp_data_dir)
            core_store.initialize()

            store_manager = MemoryStoreManager(data_dir=temp_data_dir)

            _test_service = {
                "core_store": core_store,
                "store_manager": store_manager,
                "commit_manager": CommitManager(core_store, store_manager, temp_data_dir),
                "gate_validator": WriteGateValidator(),
                "quarantine_manager": QuarantineManager(core_store, store_manager),
                "memory_index": MemoryIndex(store_manager),
                "compiler_service": ContextCompilerService(temp_data_dir),
            }

        return _test_service

    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(router)

    # Use FastAPI's dependency_overrides so that Depends(get_memory_service)
    # calls our patched factory regardless of the captured function reference.
    # This bypasses the FEATURE_MEMORY_VNEXT check in the real function and
    # ensures tests work even when other test modules have already imported
    # and cached the feature flag as False.
    app.dependency_overrides[api_module.get_memory_service] = patched_get_memory_service

    yield app

    # Cleanup
    app.dependency_overrides.clear()
    api_module._memory_service = original_service
    api_auth.init_api_auth(None)


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Core Block Endpoint Tests
# ---------------------------------------------------------------------------
class TestCoreBlockEndpoints:
    """Tests for core block endpoints."""

    def test_get_all_core_blocks(self, client):
        """Test getting all core blocks."""
        response = client.get("/memory/core")

        assert response.status_code == 200
        data = response.json()

        assert "blocks" in data
        assert "total_tokens" in data
        assert isinstance(data["total_tokens"], int)

    def test_get_specific_core_block(self, client):
        """Test getting a specific core block."""
        response = client.get("/memory/core/persona")

        assert response.status_code == 200
        data = response.json()

        assert data["block_type"] == "persona"
        assert "content" in data
        assert "token_count" in data
        assert "version" in data

    def test_get_invalid_core_block(self, client):
        """Test getting an invalid core block type."""
        response = client.get("/memory/core/invalid_type")

        assert response.status_code == 400
        assert "Invalid block type" in response.json()["detail"]

    def test_core_block_response_fields(self, client):
        """Test that core block response has all expected fields."""
        response = client.get("/memory/core/mission")

        assert response.status_code == 200
        data = response.json()

        expected_fields = [
            "block_type", "content", "token_count", "token_budget",
            "status", "updated_at", "updated_by", "version", "confidence"
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Search Endpoint Tests
# ---------------------------------------------------------------------------
class TestSearchEndpoint:
    """Tests for search endpoint."""

    def test_search_basic(self, client):
        """Test basic search functionality."""
        response = client.post(
            "/memory/search",
            json={
                "query": "test search",
                "tiers": ["working"],
                "limit": 10,
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "results" in data
        assert "total_count" in data
        assert "query" in data
        assert data["query"] == "test search"

    def test_search_with_all_parameters(self, client):
        """Test search with all parameters."""
        response = client.post(
            "/memory/search",
            json={
                "query": "important task",
                "tiers": ["working", "episodic", "archival"],
                "namespace": "project:alpha",
                "tags": ["urgent", "review"],
                "min_confidence": 0.5,
                "limit": 5,
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data["results"], list)

    def test_search_empty_results(self, client):
        """Test search with no results."""
        response = client.post(
            "/memory/search",
            json={
                "query": "nonexistent_xyz_123",
                "tiers": ["working"],
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert data["results"] == []
        assert data["total_count"] == 0


class TestRecentEndpoint:
    """Tests for recent memory listing."""

    def test_recent_returns_latest_items(self, client, app):
        service = next(iter(app.dependency_overrides.values()))()
        item = MemoryItem(
            tier=MemoryTier.episodic,
            namespace="conversation",
            title="Recent conversation",
            content="Latest memory content",
            status=MemoryStatus.active,
            token_count=12,
            confidence=0.8,
        )
        service["store_manager"].episodic.insert(item)

        response = client.get("/memory/recent?limit=5")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["items"][0]["title"] == "Recent conversation"
        assert data["items"][0]["tier"] == "episodic"
        assert data["items"][0]["namespace"] == "conversation"
        assert data["items"][0]["token_count"] == 12


# ---------------------------------------------------------------------------
# Commit Workflow Endpoint Tests
# ---------------------------------------------------------------------------
class TestCommitEndpoints:
    """Tests for commit workflow endpoints."""

    def test_begin_commit(self, client):
        """Test beginning a new commit."""
        response = client.post(
            "/memory/commit/begin",
            json={
                "created_by": "test_agent",
                "message": "Test commit",
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "commit_id" in data
        assert data["status"] == "staged"

    def test_add_edit_to_commit(self, client):
        """Test adding an edit to a commit."""
        # First begin a commit
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Test"}
        )
        commit_id = begin_response.json()["commit_id"]

        # Add an edit
        response = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Updated mission content",
                "reason": "Test update",
                "confidence": 0.9,
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "edit_id" in data
        assert data["commit_id"] == commit_id

    def test_add_edit_with_provenance(self, client):
        """Test adding an edit with provenance."""
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Test"}
        )
        commit_id = begin_response.json()["commit_id"]

        response = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Mission with provenance",
                "reason": "User requested update",
                "confidence": 0.95,
                "provenance_type": "user_message",
                "provenance_ref": "msg_123",
            }
        )

        assert response.status_code == 200

    def test_finish_commit(self, client):
        """Test finishing a commit."""
        # Begin commit
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Test"}
        )
        commit_id = begin_response.json()["commit_id"]

        # Add edit
        client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Final mission content",
                "reason": "Test",
                "confidence": 0.9,
            }
        )

        # Finish commit
        response = client.post(
            f"/memory/commit/{commit_id}/finish",
            json={"receipt_id": "receipt_001"}
        )

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "committed"
        assert "edit_count" in data

    def test_invalid_operation(self, client):
        """Test adding an edit with invalid operation."""
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Test"}
        )
        commit_id = begin_response.json()["commit_id"]

        response = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "invalid_op",
                "target": "core:mission",
                "after": "Content",
                "reason": "Test",
            }
        )

        assert response.status_code == 400
        assert "Invalid operation" in response.json()["detail"]

    def test_list_recent_commits(self, client):
        """Test listing recent governed memory commits."""
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Listed commit"}
        )
        commit_id = begin_response.json()["commit_id"]

        client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Committed content",
                "reason": "List me",
                "confidence": 0.9,
            }
        )
        client.post(f"/memory/commit/{commit_id}/finish", json={})

        response = client.get("/memory/commits?limit=5")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] >= 1
        assert data["commits"][0]["message"] == "Listed commit"

    def test_owner_edit_to_persona_allowed_with_system_provenance(self, client):
        """Governed owner edits should be able to change owner-only blocks."""
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "owner_user", "message": "Owner persona update"}
        )
        commit_id = begin_response.json()["commit_id"]

        response = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:persona",
                "after": "Owner curated persona",
                "reason": "Governed update",
                "confidence": 1.0,
                "editor": "owner",
                "provenance_type": "system",
                "provenance_ref": "warroom-memory-manager",
            }
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Rollback Endpoint Tests
# ---------------------------------------------------------------------------
class TestRollbackEndpoint:
    """Tests for rollback endpoint."""

    def test_rollback_commit(self, client):
        """Test rolling back a commit."""
        # Create and finish a commit
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test_agent", "message": "Test"}
        )
        commit_id = begin_response.json()["commit_id"]

        client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Content to rollback",
                "reason": "Test",
                "confidence": 0.9,
            }
        )

        finish_response = client.post(
            f"/memory/commit/{commit_id}/finish",
            json={}
        )
        finished_commit_id = finish_response.json()["commit_id"]

        # Rollback
        response = client.post(
            f"/memory/rollback/{finished_commit_id}",
            json={
                "reason": "Test rollback",
                "created_by": "test_admin",
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "rollback_commit_id" in data
        assert data["rolled_back_commit_id"] == finished_commit_id


# ---------------------------------------------------------------------------
# Quarantine Endpoint Tests
# ---------------------------------------------------------------------------
class TestQuarantineEndpoints:
    """Tests for quarantine endpoints."""

    def test_get_quarantine(self, client):
        """Test getting quarantined items."""
        response = client.get("/memory/quarantine")

        assert response.status_code == 200
        data = response.json()

        assert "core_blocks" in data
        assert "items" in data
        assert isinstance(data["core_blocks"], list)
        assert isinstance(data["items"], list)

    def test_get_quarantine_includes_review_metadata(self, client, app):
        """Quarantine listing returns review details for operator triage."""
        service = next(iter(app.dependency_overrides.values()))()
        item = MemoryItem(
            id="review_metadata_item",
            tier=MemoryTier.episodic,
            namespace="global",
            title="Needs policy review",
            content="Quarantined content",
            status=MemoryStatus.quarantined,
            metadata={
                "flagged_reason": "memory_ethics",
                "ethics_rule": "pii_requires_consent",
                "ethics_reason": "Long-term memory containing PII requires explicit consent",
                "injection_detection": {"reason": "instruction override", "matched": "ignore previous"},
            },
        )
        service["store_manager"].episodic.insert(item)

        response = client.get("/memory/quarantine")

        assert response.status_code == 200
        data = response.json()
        listed = next(entry for entry in data["items"] if entry["id"] == "review_metadata_item")
        assert listed["flagged_reason"] == "memory_ethics"
        assert listed["detection_metadata"]["ethics_rule"] == "pii_requires_consent"
        assert listed["detection_metadata"]["injection_detection"]["matched"] == "ignore previous"

    def test_promote_nonexistent_item(self, client):
        """Test promoting a nonexistent item."""
        response = client.post(
            "/memory/promote/nonexistent_id",
            json={"approver": "admin"},
            params={"tier": "working"}
        )

        assert response.status_code == 404

    def test_promote_invalid_core_block(self, client):
        """Test promoting with invalid core block type."""
        response = client.post(
            "/memory/promote/core:invalid_type",
            json={"approver": "admin"}
        )

        assert response.status_code == 400

    def test_approve_and_reject_quarantined_items(self, client, app):
        """Approve and reject endpoints should govern quarantined tiered items."""
        service = next(iter(app.dependency_overrides.values()))()
        item = MemoryItem(
            tier=MemoryTier.working,
            namespace="global",
            title="Needs review",
            content="Quarantined content",
            status=MemoryStatus.quarantined,
        )
        service["store_manager"].working.insert(item)

        approve_response = client.post(
            f"/memory/quarantine/working/{item.id}/approve",
            json={"operator": "admin", "reason": "Looks good"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        second_item = MemoryItem(
            tier=MemoryTier.working,
            namespace="global",
            title="Reject me",
            content="Bad quarantined content",
            status=MemoryStatus.quarantined,
        )
        service["store_manager"].working.insert(second_item)

        reject_response = client.post(
            f"/memory/quarantine/working/{second_item.id}/reject",
            json={"operator": "admin", "reason": "Bad memory"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "rejected"

    def test_approve_and_reject_quarantined_core_blocks(self, client, app):
        """Governed endpoints should handle quarantined core blocks."""
        service = next(iter(app.dependency_overrides.values()))()
        service["core_store"].set_block(
            block_type=CoreBlockType.mission,
            content="Quarantined mission",
            updated_by="agent",
            provenance=[],
            confidence=0.9,
            status=MemoryStatus.quarantined,
        )

        approve_response = client.post(
            "/memory/quarantine/core/mission/approve",
            json={"operator": "admin", "reason": "Approved"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        service["core_store"].set_block(
            block_type=CoreBlockType.mission,
            content="Reject this mission",
            updated_by="agent",
            provenance=[],
            confidence=0.9,
            status=MemoryStatus.quarantined,
        )
        reject_response = client.post(
            "/memory/quarantine/core/mission/reject",
            json={"operator": "admin", "reason": "Rejected"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "rejected"

    def test_archive_and_delete_tiered_items(self, client, app):
        """Tiered items should support lifecycle updates and governed deletion."""
        service = next(iter(app.dependency_overrides.values()))()
        item = MemoryItem(
            tier=MemoryTier.episodic,
            namespace="conversation",
            title="Archive me",
            content="Archive candidate",
            status=MemoryStatus.active,
        )
        service["store_manager"].episodic.insert(item)

        archive_response = client.post(
            f"/memory/item/episodic/{item.id}/status?status=deprecated",
            json={"operator": "admin", "reason": "Archive"},
        )
        assert archive_response.status_code == 200
        assert archive_response.json()["status"] == "deprecated"

        delete_response = client.post(
            f"/memory/item/episodic/{item.id}/delete",
            json={"operator": "admin", "reason": "Delete"},
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "deleted"

    def test_commit_edit_rejects_invalid_provenance_editor_and_gate_blocks(self, client, app, monkeypatch):
        """Commit edits should fail closed before staged memory is mutated."""
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "owner_user", "message": "Guardrail checks"},
        )
        commit_id = begin_response.json()["commit_id"]

        invalid_provenance = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "new mission",
                "reason": "bad provenance",
                "provenance_type": "not_real",
                "provenance_ref": "ref-1",
            },
        )
        assert invalid_provenance.status_code == 400
        assert "Invalid provenance type" in invalid_provenance.json()["detail"]

        invalid_editor = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "new mission",
                "reason": "bad editor",
                "editor": "stranger",
            },
        )
        assert invalid_editor.status_code == 400
        assert "Invalid editor" in invalid_editor.json()["detail"]

        service = next(iter(app.dependency_overrides.values()))()
        service["gate_validator"].validate_edit = lambda edit, editor: type(
            "GateResult",
            (),
            {"allowed": False, "reason": "owner-only block", "scrubbed_content": None},
        )()
        blocked = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:persona",
                "after": "blocked persona",
                "reason": "gate block",
            },
        )
        assert blocked.status_code == 403
        assert "owner-only block" in blocked.json()["detail"]

        service["gate_validator"].validate_edit = lambda edit, editor: type(
            "GateResult",
            (),
            {"allowed": True, "reason": "", "scrubbed_content": "scrubbed content"},
        )()
        captured = {}
        original_add_edit = service["commit_manager"].add_edit

        def add_edit_with_capture(**kwargs):
            captured.update(kwargs)
            return original_add_edit(**kwargs)

        service["commit_manager"].add_edit = add_edit_with_capture
        scrubbed = client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "raw secret content",
                "reason": "scrub before commit",
            },
        )
        assert scrubbed.status_code == 200
        assert captured["after"] == "scrubbed content"

    def test_commit_finish_and_rollback_report_manager_failures(self, client, app):
        service = next(iter(app.dependency_overrides.values()))()
        service["commit_manager"].finish_edits = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("commit store unavailable")
        )
        finish = client.post("/memory/commit/missing/finish", json={})
        assert finish.status_code == 400
        assert finish.json()["detail"] == "Failed to finish commit"

        service["commit_manager"].rollback = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("rollback unavailable")
        )
        rollback = client.post("/memory/rollback/commit-1", json={"reason": "undo"})
        assert rollback.status_code == 400
        assert rollback.json()["detail"] == "Failed to rollback commit"

    def test_quarantine_and_item_mutations_reject_invalid_or_missing_targets(self, client):
        assert client.post(
            "/memory/quarantine/core/not-a-block/approve",
            json={"reason": "approve"},
        ).status_code == 400
        assert client.post(
            "/memory/quarantine/core/mission/approve",
            json={"reason": "approve"},
        ).status_code == 404
        assert client.post(
            "/memory/quarantine/core/not-a-block/reject",
            json={"reason": "reject"},
        ).status_code == 400
        assert client.post(
            "/memory/quarantine/core/mission/reject",
            json={"reason": "reject"},
        ).status_code == 404
        assert client.post(
            "/memory/quarantine/not-a-tier/item-1/approve",
            json={"reason": "approve"},
        ).status_code == 400
        assert client.post(
            "/memory/quarantine/working/item-1/approve",
            json={"reason": "approve"},
        ).status_code == 404
        assert client.post(
            "/memory/quarantine/not-a-tier/item-1/reject",
            json={"reason": "reject"},
        ).status_code == 400
        assert client.post(
            "/memory/quarantine/working/item-1/reject",
            json={"reason": "reject"},
        ).status_code == 404
        assert client.post(
            "/memory/item/not-a-tier/item-1/status?status=active",
            json={"reason": "status"},
        ).status_code == 400
        assert client.post(
            "/memory/item/core/item-1/status?status=active",
            json={"reason": "status"},
        ).status_code == 400
        assert client.post(
            "/memory/item/working/item-1/status?status=not-a-status",
            json={"reason": "status"},
        ).status_code == 400
        assert client.post(
            "/memory/item/working/item-1/status?status=active",
            json={"reason": "status"},
        ).status_code == 404
        assert client.post(
            "/memory/item/not-a-tier/item-1/delete",
            json={"reason": "delete"},
        ).status_code == 400
        assert client.post(
            "/memory/item/core/item-1/delete",
            json={"reason": "delete"},
        ).status_code == 400
        assert client.post(
            "/memory/item/working/item-1/delete",
            json={"reason": "delete"},
        ).status_code == 404


# ---------------------------------------------------------------------------
# Context Compiler Endpoint Tests
# ---------------------------------------------------------------------------
class TestCompileEndpoint:
    """Tests for context compile endpoint."""

    def test_compile_context_basic(self, client):
        """Test basic context compilation."""
        response = client.post(
            "/memory/compile",
            json={
                "objective": "Complete the user's request",
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "context_id" in data
        assert "token_estimate" in data
        assert "token_breakdown" in data
        assert "included_blocks" in data

    def test_compile_context_with_quest(self, client):
        """Test context compilation with quest ID."""
        response = client.post(
            "/memory/compile",
            json={
                "objective": "Work on project task",
                "quest_id": "quest_123",
                "mode": "normal",
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data["included_blocks"], list)

    def test_compile_context_with_search(self, client):
        """Test context compilation with search query."""
        response = client.post(
            "/memory/compile",
            json={
                "objective": "Find relevant information",
                "search_query": "important task",
                "mode": "debug",
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "included_memory_count" in data
        assert "excluded_count" in data


# ---------------------------------------------------------------------------
# Stats Endpoint Tests
# ---------------------------------------------------------------------------
class TestStatsEndpoint:
    """Tests for stats endpoint."""

    def test_get_stats(self, client):
        """Test getting memory stats."""
        response = client.get("/memory/stats")

        assert response.status_code == 200
        data = response.json()

        assert "index" in data
        assert "core_blocks" in data
        assert "gates" in data

    def test_stats_core_blocks_info(self, client):
        """Test that stats include core block information."""
        response = client.get("/memory/stats")

        assert response.status_code == 200
        data = response.json()

        assert "total_tokens" in data["core_blocks"]
        assert "budget_issues" in data["core_blocks"]

    def test_stats_gates_info(self, client):
        """Test that stats include gates information."""
        response = client.get("/memory/stats")

        assert response.status_code == 200
        data = response.json()

        gates = data["gates"]
        assert "agent_writable" in gates
        assert "owner_only" in gates


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------
class TestErrorHandling:
    """Tests for API error handling."""

    def test_malformed_search_request(self, client):
        """Test handling of malformed search request."""
        response = client.post(
            "/memory/search",
            json={"invalid_field": "value"}
        )

        # FastAPI validation should catch this
        assert response.status_code == 422

    def test_search_rejects_unexpected_fields_with_valid_payload(self, client):
        response = client.post(
            "/memory/search",
            json={
                "query": "test search",
                "tiers": ["working"],
                "unexpected": "deny-me",
            }
        )

        assert response.status_code == 422

    def test_malformed_commit_request(self, client):
        """Test handling of malformed commit request."""
        response = client.post(
            "/memory/commit/begin",
            json={"invalid": "data"}
        )

        assert response.status_code == 422

    def test_nonexistent_commit_id(self, client):
        """Test operations on nonexistent commit ID."""
        response = client.post(
            "/memory/commit/nonexistent_123/edit",
            json={
                "op": "replace",
                "target": "core:mission",
                "after": "Content",
                "reason": "Test",
            }
        )

        assert response.status_code == 400


class TestMemoryDataDir:
    """Tests for canonical memory data directory selection."""

    def test_prefers_lancelot_data_dir_env(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_DATA_DIR", "/tmp/lancelot-test")
        assert _get_memory_data_dir() == Path("/tmp/lancelot-test")


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------
class TestAPIIntegration:
    """Integration tests for the full API workflow."""

    def test_full_commit_workflow(self, client):
        """Test complete commit workflow: begin -> edit -> finish."""
        # 1. Begin commit
        begin_response = client.post(
            "/memory/commit/begin",
            json={
                "created_by": "integration_test",
                "message": "Integration test commit",
            }
        )
        assert begin_response.status_code == 200
        commit_id = begin_response.json()["commit_id"]

        # 2. Add multiple edits
        for i in range(3):
            edit_response = client.post(
                f"/memory/commit/{commit_id}/edit",
                json={
                    "op": "replace",
                    "target": "core:mission",
                    "after": f"Mission content version {i}",
                    "reason": f"Edit {i}",
                    "confidence": 0.9,
                }
            )
            assert edit_response.status_code == 200

        # 3. Finish commit
        finish_response = client.post(
            f"/memory/commit/{commit_id}/finish",
            json={"receipt_id": "integration_test_receipt"}
        )
        assert finish_response.status_code == 200
        assert finish_response.json()["status"] == "committed"

        # 4. Verify in stats
        stats_response = client.get("/memory/stats")
        assert stats_response.status_code == 200

    def test_search_after_updates(self, client):
        """Test that search works after memory updates."""
        # Do some operations
        begin_response = client.post(
            "/memory/commit/begin",
            json={"created_by": "test", "message": "Update"}
        )
        commit_id = begin_response.json()["commit_id"]

        client.post(
            f"/memory/commit/{commit_id}/edit",
            json={
                "op": "replace",
                "target": "core:workspace_state",
                "after": "Working on unique_searchable_term_xyz",
                "reason": "Test",
                "confidence": 0.9,
            }
        )

        client.post(
            f"/memory/commit/{commit_id}/finish",
            json={}
        )

        # Search should still work
        search_response = client.post(
            "/memory/search",
            json={
                "query": "test",
                "tiers": ["working"],
            }
        )
        assert search_response.status_code == 200
