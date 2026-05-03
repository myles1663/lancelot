"""Receipt coverage for structured memory state changes."""

from __future__ import annotations

import os
import hashlib

os.environ["FEATURE_MEMORY_VNEXT"] = "true"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
import src.core.memory.api as api_module
from src.core.memory.api import get_memory_service, router
from src.core.memory.schemas import CoreBlockType, MemoryItem, MemoryStatus, MemoryTier
from src.shared.receipts import ReceiptStatus


@pytest.fixture
def app(tmp_data_dir):
    original_service = api_module._memory_service
    api_module._memory_service = None
    test_service = None

    def patched_get_memory_service():
        nonlocal test_service
        if test_service is None:
            from src.core.memory.store import CoreBlockStore
            from src.core.memory.sqlite_store import MemoryStoreManager
            from src.core.memory.commits import CommitManager
            from src.core.memory.gates import QuarantineManager, WriteGateValidator
            from src.core.memory.index import MemoryIndex
            from src.core.memory.compiler import ContextCompilerService
            from src.core.memory.receipt_events import MemoryReceiptEmitter

            core_store = CoreBlockStore(data_dir=tmp_data_dir)
            core_store.initialize()
            store_manager = MemoryStoreManager(data_dir=tmp_data_dir)
            test_service = {
                "core_store": core_store,
                "store_manager": store_manager,
                "commit_manager": CommitManager(core_store, store_manager, tmp_data_dir),
                "gate_validator": WriteGateValidator(),
                "quarantine_manager": QuarantineManager(core_store, store_manager),
                "memory_index": MemoryIndex(store_manager),
                "compiler_service": ContextCompilerService(
                    tmp_data_dir,
                    core_store=core_store,
                    memory_manager=store_manager,
                ),
                "receipt_emitter": MemoryReceiptEmitter(tmp_data_dir),
            }
        return test_service

    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_memory_service] = patched_get_memory_service
    yield app
    app.dependency_overrides.clear()
    api_module._memory_service = original_service
    api_auth.init_api_auth(None)


@pytest.fixture
def client(app):
    return TestClient(app)


def _service(app):
    return next(iter(app.dependency_overrides.values()))()


def _receipt_service(app):
    return _service(app)["receipt_emitter"].receipt_service


def test_finish_commit_emits_queryable_receipt(client, app):
    begin = client.post("/memory/commit/begin", json={"message": "receipt test"})
    commit_id = begin.json()["commit_id"]

    client.post(
        f"/memory/commit/{commit_id}/edit",
        json={
            "op": "replace",
            "target": "core:mission",
            "after": "Receipt-backed mission update",
            "reason": "Receipt test",
            "confidence": 0.9,
            "editor": "owner",
        },
    )
    response = client.post(f"/memory/commit/{commit_id}/finish", json={})

    assert response.status_code == 200
    receipts = _receipt_service(app).list(action_type="memory_commit_apply")
    assert len(receipts) == 1
    assert receipts[0].status == ReceiptStatus.SUCCESS.value
    assert receipts[0].outputs["commit_id"] == commit_id
    assert receipts[0].outputs["edit_count"] == 1
    assert receipts[0].outputs["affected_targets"] == ["core:mission"]
    assert receipts[0].outputs["full_commit_ref"] == {
        "type": "memory_commit",
        "commit_id": commit_id,
    }
    edits = receipts[0].outputs["edits"]
    assert len(edits) == 1
    assert edits[0]["op"] == "replace"
    assert edits[0]["target"] == "core:mission"
    assert edits[0]["reason"] == "Receipt test"
    assert edits[0]["confidence"] == 0.9
    assert edits[0]["editor"] == "owner"
    assert edits[0]["after_preview"] == "Receipt-backed mission update"
    assert edits[0]["after_hash"] == hashlib.sha256(
        b"Receipt-backed mission update"
    ).hexdigest()
    assert edits[0]["before_hash"]


def test_rollback_emits_queryable_receipt(client, app):
    begin = client.post("/memory/commit/begin", json={"message": "rollback receipt"})
    commit_id = begin.json()["commit_id"]
    client.post(
        f"/memory/commit/{commit_id}/edit",
        json={
            "op": "replace",
            "target": "core:mission",
            "after": "Temporary mission",
            "reason": "Rollback receipt test",
            "confidence": 0.9,
            "editor": "owner",
        },
    )
    client.post(f"/memory/commit/{commit_id}/finish", json={})

    response = client.post(f"/memory/rollback/{commit_id}", json={"reason": "undo"})

    assert response.status_code == 200
    receipts = _receipt_service(app).list(action_type="memory_commit_rollback")
    assert len(receipts) == 1
    assert receipts[0].outputs["rollback_of"] == commit_id
    assert "core:mission" in receipts[0].outputs["restored_targets"]


def test_quarantine_status_and_delete_emit_receipts(client, app):
    service = _service(app)
    item = MemoryItem(
        id="receipt_status_item",
        tier=MemoryTier.working,
        title="Receipt status item",
        content="status content",
        status=MemoryStatus.quarantined,
    )
    service["store_manager"].working.insert(item)

    approve = client.post("/memory/quarantine/working/receipt_status_item/approve", json={"reason": "ok"})
    status = client.post(
        "/memory/item/working/receipt_status_item/status?status=deprecated",
        json={"reason": "archive"},
    )
    delete = client.post("/memory/item/working/receipt_status_item/delete", json={"reason": "cleanup"})

    assert approve.status_code == 200
    assert status.status_code == 200
    assert delete.status_code == 200
    receipt_service = _receipt_service(app)
    assert receipt_service.count(action_type="memory_quarantine_approve") == 1
    assert receipt_service.count(action_type="memory_item_status_change") == 1
    assert receipt_service.count(action_type="memory_item_delete") == 1


def test_compile_endpoint_emits_receipt(client, app):
    response = client.post("/memory/compile", json={"objective": "compile receipt"})

    assert response.status_code == 200
    receipts = _receipt_service(app).list(action_type="memory_compile")
    assert len(receipts) == 1
    assert receipts[0].outputs["context_id"] == response.json()["context_id"]


def test_agent_core_edit_to_allowed_block_is_quarantined(client, app):
    begin = client.post("/memory/commit/begin", json={"message": "agent core edit"})
    commit_id = begin.json()["commit_id"]

    edit = client.post(
        f"/memory/commit/{commit_id}/edit",
        json={
            "op": "replace",
            "target": "core:mission",
            "after": "Agent proposed mission update",
            "reason": "agent proposal",
            "confidence": 0.9,
            "editor": "agent",
        },
    )
    finish = client.post(f"/memory/commit/{commit_id}/finish", json={})
    queue = client.get("/memory/quarantine")

    assert edit.status_code == 200
    assert finish.status_code == 200
    assert _service(app)["core_store"].get_block(CoreBlockType.mission).status == MemoryStatus.quarantined
    assert any(block["block_type"] == "mission" for block in queue.json()["core_blocks"])


def test_agent_core_edit_to_owner_only_block_is_denied(client):
    begin = client.post("/memory/commit/begin", json={"message": "agent persona edit"})
    commit_id = begin.json()["commit_id"]

    response = client.post(
        f"/memory/commit/{commit_id}/edit",
        json={
            "op": "replace",
            "target": "core:persona",
            "after": "Agent tries to rewrite persona",
            "reason": "not allowed",
            "confidence": 0.9,
            "editor": "agent",
        },
    )

    assert response.status_code == 403
