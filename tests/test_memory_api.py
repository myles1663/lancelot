"""
Tests for Memory vNext REST API.

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

from src.core.memory.api import router, get_memory_service, _memory_service
from src.core.memory.schemas import (
    CoreBlockType,
    MemoryTier,
    MemoryStatus,
    ProvenanceType,
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

    # Reset the singleton
    api_module._memory_service = None

    # Patch the data directory
    def patched_get_memory_service():
        if api_module._memory_service is None:
            from src.core.memory.store import CoreBlockStore
            from src.core.memory.sqlite_store import MemoryStoreManager
            from src.core.memory.commits import CommitManager
            from src.core.memory.gates import WriteGateValidator, QuarantineManager
            from src.core.memory.index import MemoryIndex
            from src.core.memory.compiler import ContextCompilerService

            core_store = CoreBlockStore(data_dir=temp_data_dir)
            core_store.initialize()

            store_manager = MemoryStoreManager(data_dir=temp_data_dir)

            api_module._memory_service = {
                "core_store": core_store,
                "store_manager": store_manager,
                "commit_manager": CommitManager(core_store, store_manager, temp_data_dir),
                "gate_validator": WriteGateValidator(),
                "quarantine_manager": QuarantineManager(core_store, store_manager),
                "memory_index": MemoryIndex(store_manager),
                "compiler_service": ContextCompilerService(temp_data_dir),
            }

        return api_module._memory_service

    monkeypatch.setattr(api_module, "get_memory_service", patched_get_memory_service)

    app = FastAPI()
    app.include_router(router)

    yield app

    # Cleanup
    api_module._memory_service = None


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
