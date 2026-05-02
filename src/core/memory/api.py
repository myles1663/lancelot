"""
Structured Memory API — FastAPI endpoints for memory operations.

This module provides REST API endpoints for:
- Core block management
- Memory search
- Commit operations
- Quarantine management
- Context compilation
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity

from .schemas import (
    CoreBlockType,
    MemoryEdit,
    MemoryEditOp,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from .receipt_events import MemoryReceiptEmitter
from src.shared.receipts import ReceiptStatus

logger = logging.getLogger(__name__)

import threading

# Create router for memory endpoints
router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(require_authenticated_request)],
)


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------
class CoreBlockResponse(BaseModel):
    """Response for core block queries."""
    block_type: str
    content: str
    token_count: int
    token_budget: int
    status: str
    updated_at: str
    updated_by: str
    version: int
    confidence: float


class CoreBlocksResponse(BaseModel):
    """Response for all core blocks."""
    blocks: dict[str, CoreBlockResponse]
    total_tokens: int


class SearchRequest(BaseModel):
    """Request for memory search."""
    model_config = ConfigDict(extra="forbid")
    query: str
    tiers: list[str] = Field(default=["working", "episodic", "archival"])
    namespace: Optional[str] = None
    tags: Optional[list[str]] = None
    min_confidence: float = 0.3
    limit: int = 20
    include_blobs: bool = False


class SearchResultItem(BaseModel):
    """Single search result item."""
    id: str
    tier: str
    title: str
    content: str
    confidence: float
    score: float
    tags: list[str]
    namespace: str


class SearchResponse(BaseModel):
    """Response for memory search."""
    results: list[SearchResultItem]
    total_count: int
    query: str


class RecentMemoryItemResponse(BaseModel):
    """Recent memory item summary."""
    id: str
    tier: str
    title: str
    content: str
    namespace: str
    confidence: float
    token_count: int
    created_at: str
    updated_at: str
    tags: list[str]


class RecentMemoryResponse(BaseModel):
    """Response for recent memory listing."""
    items: list[RecentMemoryItemResponse]
    total_count: int


class BeginCommitRequest(BaseModel):
    """Request to begin a staged commit."""
    model_config = ConfigDict(extra="forbid")
    created_by: Optional[str] = Field(
        default=None,
        description="Deprecated client field. Operator identity is derived server-side.",
    )
    message: str = ""


class BeginCommitResponse(BaseModel):
    """Response with staged commit ID."""
    commit_id: str
    status: str


class AddEditRequest(BaseModel):
    """Request to add an edit to a staged commit."""
    model_config = ConfigDict(extra="forbid")
    op: str  # insert, replace, delete
    target: str  # core:type or tier:id
    after: Optional[str] = None
    reason: str
    confidence: float = 0.5
    editor: str = "agent"
    provenance_type: Optional[str] = None
    provenance_ref: Optional[str] = None


class AddEditResponse(BaseModel):
    """Response for added edit."""
    edit_id: str
    commit_id: str


class FinishCommitRequest(BaseModel):
    """Request to finish a staged commit."""
    model_config = ConfigDict(extra="forbid")
    receipt_id: Optional[str] = None


class FinishCommitResponse(BaseModel):
    """Response for finished commit."""
    commit_id: str
    status: str
    edit_count: int


class RollbackRequest(BaseModel):
    """Request to rollback a commit."""
    model_config = ConfigDict(extra="forbid")
    reason: str
    created_by: Optional[str] = Field(
        default=None,
        description="Deprecated client field. Operator identity is derived server-side.",
    )


class RollbackResponse(BaseModel):
    """Response for rollback."""
    rollback_commit_id: str
    rolled_back_commit_id: str


class CommitSummaryResponse(BaseModel):
    """Compact summary of a committed or staged memory commit."""
    commit_id: str
    created_at: str
    created_by: str
    status: str
    message: str
    edit_count: int
    affected_targets: list[str]
    has_core_edits: bool
    receipt_id: Optional[str] = None
    rollback_of: Optional[str] = None


class CommitHistoryResponse(BaseModel):
    """Response for recent commit history."""
    commits: list[CommitSummaryResponse]
    total_count: int


class QuarantineItemResponse(BaseModel):
    """Quarantined item info."""
    id: str
    tier: str
    title: str
    content: str
    status: str
    flagged_reason: Optional[str] = None
    detection_metadata: dict[str, Any] = Field(default_factory=dict)


class QuarantineResponse(BaseModel):
    """Response for quarantine listing."""
    core_blocks: list[dict[str, Any]]
    items: list[QuarantineItemResponse]


class PromoteRequest(BaseModel):
    """Request to promote a quarantined item."""
    model_config = ConfigDict(extra="forbid")
    approver: Optional[str] = Field(
        default=None,
        description="Deprecated client field. Operator identity is derived server-side.",
    )


class MemoryActionRequest(BaseModel):
    """Request model for governed memory actions."""
    model_config = ConfigDict(extra="forbid")
    reason: str = ""
    operator: Optional[str] = Field(
        default=None,
        description="Deprecated client field. Operator identity is derived server-side.",
    )


class MemoryActionResponse(BaseModel):
    """Response for governed memory mutations."""
    status: str
    item_id: str
    tier: Optional[str] = None
    block_type: Optional[str] = None
    reason: str = ""


class CompileContextRequest(BaseModel):
    """Request to compile context."""
    model_config = ConfigDict(extra="forbid")
    objective: str
    quest_id: Optional[str] = None
    mode: str = "normal"
    search_query: Optional[str] = None


class CompileContextResponse(BaseModel):
    """Response for compiled context."""
    context_id: str
    token_estimate: int
    token_breakdown: dict[str, int]
    included_blocks: list[str]
    included_memory_count: int
    excluded_count: int


# ---------------------------------------------------------------------------
# Service Factory (Dependency Injection)
# ---------------------------------------------------------------------------
_memory_service = None
_service_lock = threading.Lock()


def _get_memory_data_dir() -> Path:
    """Return the canonical memory data directory used by the live app."""
    return Path(os.getenv("LANCELOT_DATA_DIR", "/home/lancelot/data"))


def get_memory_service():
    """Get or create the memory service singleton (thread-safe)."""
    global _memory_service
    if _memory_service is not None:
        return _memory_service
    with _service_lock:
        if _memory_service is not None:
            return _memory_service
        try:
            from feature_flags import FEATURE_MEMORY_VNEXT
        except ImportError:
            from ..feature_flags import FEATURE_MEMORY_VNEXT
        if not FEATURE_MEMORY_VNEXT:
            raise HTTPException(
                status_code=503,
                detail="Structured memory is disabled. Set FEATURE_MEMORY_VNEXT=true"
            )

        from .store import CoreBlockStore
        from .sqlite_store import MemoryStoreManager
        from .commits import CommitManager
        from .gates import WriteGateValidator, QuarantineManager
        from .index import MemoryIndex
        from .compiler import ContextCompilerService
        from .receipt_events import MemoryReceiptEmitter

        data_dir = _get_memory_data_dir()

        core_store = CoreBlockStore(data_dir=data_dir)
        core_store.initialize()

        store_manager = MemoryStoreManager(data_dir=data_dir)

        _memory_service = {
            "core_store": core_store,
            "store_manager": store_manager,
            "commit_manager": CommitManager(core_store, store_manager, data_dir),
            "gate_validator": WriteGateValidator(),
            "quarantine_manager": QuarantineManager(core_store, store_manager),
            "memory_index": MemoryIndex(store_manager),
            "compiler_service": ContextCompilerService(
                data_dir, core_store=core_store, memory_manager=store_manager
            ),
            "receipt_emitter": MemoryReceiptEmitter(data_dir),
        }

    return _memory_service


def _receipt_emitter(service: dict) -> MemoryReceiptEmitter:
    """Return a memory receipt emitter, adding one to test-local services if needed."""
    emitter = service.get("receipt_emitter")
    if emitter is not None:
        return emitter
    data_dir = getattr(service.get("core_store"), "data_dir", _get_memory_data_dir())
    emitter = MemoryReceiptEmitter(data_dir)
    service["receipt_emitter"] = emitter
    return emitter


def _identity_fields(identity: Any) -> dict[str, str]:
    return {
        "operator_id": getattr(identity, "operator_id", "") or "",
        "session_id": getattr(identity, "session_id", "") or "",
    }


# ---------------------------------------------------------------------------
# Core Block Endpoints
# ---------------------------------------------------------------------------
@router.get("/core", response_model=CoreBlocksResponse)
async def get_core_blocks(service: dict = Depends(get_memory_service)):
    """Get all core memory blocks."""
    core_store = service["core_store"]
    blocks = core_store.get_all_blocks()

    response_blocks = {}
    for block_type, block in blocks.items():
        response_blocks[block_type] = CoreBlockResponse(
            block_type=block.block_type.value,
            content=block.content,
            token_count=block.token_count,
            token_budget=block.token_budget,
            status=block.status.value,
            updated_at=block.updated_at.isoformat(),
            updated_by=block.updated_by,
            version=block.version,
            confidence=block.confidence,
        )

    return CoreBlocksResponse(
        blocks=response_blocks,
        total_tokens=core_store.total_tokens(),
    )


@router.get("/core/{block_type}", response_model=CoreBlockResponse)
async def get_core_block(
    block_type: str,
    service: dict = Depends(get_memory_service),
):
    """Get a specific core block."""
    try:
        bt = CoreBlockType(block_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid block type: {block_type}")

    core_store = service["core_store"]
    block = core_store.get_block(bt)

    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_type} not found")

    return CoreBlockResponse(
        block_type=block.block_type.value,
        content=block.content,
        token_count=block.token_count,
        token_budget=block.token_budget,
        status=block.status.value,
        updated_at=block.updated_at.isoformat(),
        updated_by=block.updated_by,
        version=block.version,
        confidence=block.confidence,
    )


# ---------------------------------------------------------------------------
# Search Endpoints
# ---------------------------------------------------------------------------
@router.post("/search", response_model=SearchResponse)
async def search_memory(
    request: SearchRequest,
    service: dict = Depends(get_memory_service),
):
    """Search across memory tiers."""
    memory_index = service["memory_index"]

    tiers = [MemoryTier(t) for t in request.tiers if t != "core"]

    results = memory_index.search(
        query=request.query,
        tiers=tiers,
        namespace=request.namespace,
        tags=request.tags,
        min_confidence=request.min_confidence,
        limit=request.limit,
        include_blobs=request.include_blobs,
    )

    result_items = [
        SearchResultItem(
            id=r.item.id,
            tier=r.item.tier.value,
            title=r.item.title,
            content=r.item.content[:500],  # Truncate for response
            confidence=r.item.confidence,
            score=r.score,
            tags=r.item.tags,
            namespace=r.item.namespace,
        )
        for r in results
    ]

    return SearchResponse(
        results=result_items,
        total_count=len(result_items),
        query=request.query,
    )


@router.post("/query", response_model=SearchResponse)
async def query_memory(
    request: SearchRequest,
    service: dict = Depends(get_memory_service),
):
    """Run a natural-language memory query using entity-aware retrieval."""
    return await search_memory(request, service)


@router.get("/recent", response_model=RecentMemoryResponse)
async def get_recent_memory(
    limit: int = 12,
    service: dict = Depends(get_memory_service),
):
    """Get recent memory items across all non-core tiers."""
    memory_index = service["memory_index"]

    recent_items = []
    per_tier_limit = max(1, min(limit, 50))
    for tier in (MemoryTier.working, MemoryTier.episodic, MemoryTier.archival):
        try:
            recent_items.extend(memory_index.get_recent(tier=tier, limit=per_tier_limit))
        except Exception as exc:
            logger.warning("Failed to fetch recent memory for tier %s: %s", tier.value, exc)

    recent_items.sort(key=lambda item: item.updated_at, reverse=True)
    recent_items = recent_items[:limit]

    return RecentMemoryResponse(
        items=[
            RecentMemoryItemResponse(
                id=item.id,
                tier=item.tier.value,
                title=item.title,
                content=item.content[:500],
                namespace=item.namespace,
                confidence=item.confidence,
                token_count=item.token_count,
                created_at=item.created_at.isoformat(),
                updated_at=item.updated_at.isoformat(),
                tags=item.tags,
            )
            for item in recent_items
        ],
        total_count=len(recent_items),
    )


# ---------------------------------------------------------------------------
# Commit Endpoints
# ---------------------------------------------------------------------------
@router.post("/commit/begin", response_model=BeginCommitResponse)
async def begin_commit(
    request: BeginCommitRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Begin a new staged commit."""
    commit_manager = service["commit_manager"]

    identity = resolve_authenticated_identity(http_request)
    created_by = identity.display_name or identity.operator_id or "operator"
    commit_id = commit_manager.begin_edits(
        created_by=created_by,
        message=request.message,
    )

    return BeginCommitResponse(
        commit_id=commit_id,
        status="staged",
    )


@router.post("/commit/{commit_id}/edit", response_model=AddEditResponse)
async def add_edit(
    commit_id: str,
    request: AddEditRequest,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Add an edit to a staged commit."""
    commit_manager = service["commit_manager"]
    gate_validator = service["gate_validator"]

    try:
        op = MemoryEditOp(request.op)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid operation: {request.op}")

    # Build provenance if provided
    provenance = []
    if request.provenance_type and request.provenance_ref:
        try:
            prov_type = ProvenanceType(request.provenance_type)
            provenance.append(Provenance(type=prov_type, ref=request.provenance_ref))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provenance type: {request.provenance_type}",
            )

    # Verify commit exists and is staged before gate validation
    staged = commit_manager.get_staged_commit(commit_id)
    if staged is None:
        raise HTTPException(status_code=400, detail=f"Staged commit {commit_id} not found")

    # Validate edit against write gates before allowing
    edit = MemoryEdit(
        op=op,
        target=request.target,
        after=request.after,
        reason=request.reason or "API edit",
        confidence=request.confidence or 0.5,
        provenance=provenance,
    )
    if request.editor not in {"agent", "owner", "system"}:
        raise HTTPException(status_code=400, detail=f"Invalid editor: {request.editor}")

    gate_result = gate_validator.validate_edit(edit, editor=request.editor)
    if not gate_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Edit blocked by write gate: {gate_result.reason}",
        )

    after_content = gate_result.scrubbed_content if gate_result.scrubbed_content is not None else request.after

    try:
        edit_id = commit_manager.add_edit(
            commit_id=commit_id,
            op=op,
            target=request.target,
            after=after_content,
            reason=request.reason,
            confidence=request.confidence,
            provenance=provenance,
            suggested_status=getattr(gate_result, "suggested_status", MemoryStatus.staged),
            editor=request.editor,
        )

        return AddEditResponse(
            edit_id=edit_id,
            commit_id=commit_id,
        )

    except Exception as e:
        logger.error("Failed to add edit to commit %s: %s", commit_id, e)
        raise HTTPException(status_code=400, detail="Failed to add edit to commit")


@router.get("/commits", response_model=CommitHistoryResponse)
async def list_commits(
    limit: int = 25,
    service: dict = Depends(get_memory_service),
):
    """List recent governed memory commits."""
    commit_manager = service["commit_manager"]
    commits = commit_manager.list_commits(limit=max(1, min(limit, 100)))

    return CommitHistoryResponse(
        commits=[
            CommitSummaryResponse(
                commit_id=commit.commit_id,
                created_at=commit.created_at.isoformat(),
                created_by=commit.created_by,
                status=commit.status.value,
                message=commit.message,
                edit_count=len(commit.edits),
                affected_targets=sorted(commit.get_affected_targets()),
                has_core_edits=commit.has_core_edits(),
                receipt_id=commit.receipt_id,
                rollback_of=commit.rollback_of,
            )
            for commit in commits
        ],
        total_count=len(commits),
    )


@router.post("/commit/{commit_id}/finish", response_model=FinishCommitResponse)
async def finish_commit(
    commit_id: str,
    request: FinishCommitRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Finish and apply a staged commit."""
    commit_manager = service["commit_manager"]

    try:
        result_id = commit_manager.finish_edits(
            commit_id=commit_id,
            receipt_id=request.receipt_id,
        )

        commit = commit_manager.load_commit(result_id)
        edit_count = len(commit.edits) if commit else 0
        identity = resolve_authenticated_identity(http_request)
        _receipt_emitter(service).emit(
            action_type="memory_commit_apply",
            action_name="finish_commit",
            inputs={"commit_id": commit_id},
            outputs={
                "commit_id": result_id,
                "edit_count": edit_count,
                "affected_targets": sorted(commit.get_affected_targets()) if commit else [],
                "has_core_edits": commit.has_core_edits() if commit else False,
                "parent_commit_id": commit.parent_commit_id if commit else None,
            },
            **_identity_fields(identity),
        )

        return FinishCommitResponse(
            commit_id=result_id,
            status="committed",
            edit_count=edit_count,
        )

    except Exception as e:
        logger.error("Failed to finish commit %s: %s", commit_id, e)
        raise HTTPException(status_code=400, detail="Failed to finish commit")


@router.post("/rollback/{commit_id}", response_model=RollbackResponse)
async def rollback_commit(
    commit_id: str,
    request: RollbackRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Rollback a commit."""
    commit_manager = service["commit_manager"]

    try:
        identity = resolve_authenticated_identity(http_request)
        created_by = identity.display_name or identity.operator_id or "operator"
        original = commit_manager.load_commit(commit_id)
        rollback_id = commit_manager.rollback(
            commit_id=commit_id,
            reason=request.reason,
            created_by=created_by,
        )
        rollback_commit = commit_manager.load_commit(rollback_id)
        snapshot = commit_manager._snapshots.get(commit_id)
        restored_targets = sorted(original.get_affected_targets()) if original else []
        _receipt_emitter(service).emit(
            action_type="memory_commit_rollback",
            action_name="rollback_commit",
            inputs={"commit_id": commit_id, "reason": request.reason},
            outputs={
                "rollback_commit_id": rollback_id,
                "rollback_of": commit_id,
                "restored_targets": restored_targets,
                "snapshot_ids": [snapshot.snapshot_id] if snapshot else [],
                "parent_commit_id": rollback_commit.parent_commit_id if rollback_commit else None,
            },
            **_identity_fields(identity),
        )

        return RollbackResponse(
            rollback_commit_id=rollback_id,
            rolled_back_commit_id=commit_id,
        )

    except Exception as e:
        logger.error("Failed to rollback commit %s: %s", commit_id, e)
        raise HTTPException(status_code=400, detail="Failed to rollback commit")


# ---------------------------------------------------------------------------
# Quarantine Endpoints
# ---------------------------------------------------------------------------
@router.get("/quarantine", response_model=QuarantineResponse)
async def get_quarantine(service: dict = Depends(get_memory_service)):
    """Get all quarantined items."""
    quarantine_manager = service["quarantine_manager"]

    core_blocks = []
    for block_type, block in quarantine_manager.list_quarantined_core_blocks():
        core_blocks.append({
            "block_type": block_type.value,
            "content": block.content[:200],
            "updated_at": block.updated_at.isoformat(),
        })

    items = []
    for item in quarantine_manager.list_quarantined_items():
        items.append(QuarantineItemResponse(
            id=item.id,
            tier=item.tier.value,
            title=item.title,
            content=item.content[:200],
            status=item.status.value,
            flagged_reason=(item.metadata or {}).get("flagged_reason"),
            detection_metadata=(item.metadata or {}).get("injection_detection", {}),
        ))

    return QuarantineResponse(
        core_blocks=core_blocks,
        items=items,
    )


@router.post("/promote/{item_id}")
async def promote_item(
    item_id: str,
    request: PromoteRequest,
    http_request: Request,
    tier: str = "working",
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Promote a quarantined item to active."""
    quarantine_manager = service["quarantine_manager"]

    identity = resolve_authenticated_identity(http_request)
    approver = identity.display_name or identity.operator_id or "operator"
    # Check if it's a core block
    if item_id.startswith("core:"):
        block_type_str = item_id.replace("core:", "")
        try:
            block_type = CoreBlockType(block_type_str)
            block = service["core_store"].get_block(block_type)
            original_status = block.status.value if block else ""
            result = quarantine_manager.approve_core_block(block_type, approver)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid block type: {block_type_str}")
        tier_value = "core"
    else:
        tier_enum = MemoryTier(tier)
        item = service["store_manager"].get_store(tier_enum).get(item_id)
        original_status = item.status.value if item else ""
        result = quarantine_manager.approve_item(item_id, tier, approver)
        tier_value = tier

    if not result:
        raise HTTPException(status_code=404, detail="Item not found or not quarantined")

    _receipt_emitter(service).emit(
        action_type="memory_quarantine_approve",
        action_name="promote_quarantined_memory",
        inputs={"block_or_item_id": item_id, "tier": tier_value},
        outputs={"block_or_item_id": item_id, "tier": tier_value, "approver": approver, "original_status": original_status},
        **_identity_fields(identity),
    )

    return {"status": "promoted", "item_id": item_id}


@router.post("/quarantine/core/{block_type}/approve", response_model=MemoryActionResponse)
async def approve_quarantined_core_block(
    block_type: str,
    request: MemoryActionRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Approve a quarantined core block."""
    quarantine_manager = service["quarantine_manager"]

    try:
        core_block_type = CoreBlockType(block_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid block type: {block_type}")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    block = service["core_store"].get_block(core_block_type)
    original_status = block.status.value if block else ""
    if not quarantine_manager.approve_core_block(core_block_type, operator):
        raise HTTPException(status_code=404, detail="Core block not found or not quarantined")

    _receipt_emitter(service).emit(
        action_type="memory_quarantine_approve",
        action_name="approve_quarantined_core_block",
        inputs={"block_or_item_id": f"core:{block_type}", "tier": "core"},
        outputs={
            "block_or_item_id": f"core:{block_type}",
            "tier": "core",
            "approver": operator,
            "original_status": original_status,
        },
        **_identity_fields(identity),
    )

    return MemoryActionResponse(status="approved", item_id=f"core:{block_type}", block_type=block_type, reason=request.reason)


@router.post("/quarantine/core/{block_type}/reject", response_model=MemoryActionResponse)
async def reject_quarantined_core_block(
    block_type: str,
    request: MemoryActionRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Reject a quarantined core block."""
    quarantine_manager = service["quarantine_manager"]

    try:
        core_block_type = CoreBlockType(block_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid block type: {block_type}")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    block = service["core_store"].get_block(core_block_type)
    original_status = block.status.value if block else ""
    if not quarantine_manager.reject_core_block(core_block_type, operator, request.reason):
        raise HTTPException(status_code=404, detail="Core block not found or not quarantined")

    _receipt_emitter(service).emit(
        action_type="memory_quarantine_reject",
        action_name="reject_quarantined_core_block",
        inputs={"block_or_item_id": f"core:{block_type}", "tier": "core", "reason": request.reason},
        outputs={
            "block_or_item_id": f"core:{block_type}",
            "tier": "core",
            "rejector": operator,
            "reason": request.reason,
            "original_status": original_status,
        },
        **_identity_fields(identity),
    )

    return MemoryActionResponse(status="rejected", item_id=f"core:{block_type}", block_type=block_type, reason=request.reason)


@router.post("/quarantine/{tier}/{item_id}/approve", response_model=MemoryActionResponse)
async def approve_quarantined_item(
    tier: str,
    item_id: str,
    request: MemoryActionRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Approve a quarantined tiered memory item."""
    quarantine_manager = service["quarantine_manager"]

    try:
        MemoryTier(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    item = service["store_manager"].get_store(MemoryTier(tier)).get(item_id)
    original_status = item.status.value if item else ""
    if not quarantine_manager.approve_item(item_id, tier, operator):
        raise HTTPException(status_code=404, detail="Item not found or not quarantined")

    _receipt_emitter(service).emit(
        action_type="memory_quarantine_approve",
        action_name="approve_quarantined_item",
        inputs={"block_or_item_id": item_id, "tier": tier},
        outputs={
            "block_or_item_id": item_id,
            "tier": tier,
            "approver": operator,
            "original_status": original_status,
        },
        **_identity_fields(identity),
    )

    return MemoryActionResponse(status="approved", item_id=item_id, tier=tier, reason=request.reason)


@router.post("/quarantine/{tier}/{item_id}/reject", response_model=MemoryActionResponse)
async def reject_quarantined_item(
    tier: str,
    item_id: str,
    request: MemoryActionRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Reject and delete a quarantined tiered memory item."""
    quarantine_manager = service["quarantine_manager"]

    try:
        MemoryTier(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    item = service["store_manager"].get_store(MemoryTier(tier)).get(item_id)
    original_status = item.status.value if item else ""
    if not quarantine_manager.reject_item(item_id, tier, operator):
        raise HTTPException(status_code=404, detail="Item not found or not quarantined")

    _receipt_emitter(service).emit(
        action_type="memory_quarantine_reject",
        action_name="reject_quarantined_item",
        inputs={"block_or_item_id": item_id, "tier": tier, "reason": request.reason},
        outputs={
            "block_or_item_id": item_id,
            "tier": tier,
            "rejector": operator,
            "reason": request.reason,
            "original_status": original_status,
        },
        **_identity_fields(identity),
    )

    return MemoryActionResponse(status="rejected", item_id=item_id, tier=tier, reason=request.reason)


@router.post("/item/{tier}/{item_id}/status", response_model=MemoryActionResponse)
async def update_memory_item_status(
    tier: str,
    item_id: str,
    request: MemoryActionRequest,
    http_request: Request,
    status: str,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Update the lifecycle status of a tiered memory item."""
    store_manager = service["store_manager"]

    try:
        tier_enum = MemoryTier(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    if tier_enum == MemoryTier.core:
        raise HTTPException(status_code=400, detail="Core blocks must be managed through governed commit actions")

    try:
        status_enum = MemoryStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    store = store_manager.get_store(tier_enum)
    item = store.get(item_id)
    from_status = item.status.value if item else ""
    if not store.update_status(item_id, status_enum):
        raise HTTPException(status_code=404, detail="Memory item not found")

    _receipt_emitter(service).emit(
        action_type="memory_item_status_change",
        action_name="update_memory_item_status",
        inputs={"item_id": item_id, "tier": tier, "to_status": status},
        outputs={
            "item_id": item_id,
            "tier": tier,
            "from_status": from_status,
            "to_status": status_enum.value,
            "actor": operator,
        },
        **_identity_fields(identity),
    )

    logger.info("Operator %s updated memory item %s in %s to %s", operator, item_id, tier, status)
    return MemoryActionResponse(status=status_enum.value, item_id=item_id, tier=tier, reason=request.reason)


@router.post("/item/{tier}/{item_id}/delete", response_model=MemoryActionResponse)
async def delete_memory_item(
    tier: str,
    item_id: str,
    request: MemoryActionRequest,
    http_request: Request,
    _authz: None = Depends(require_operator_capability("memory.admin")),
    service: dict = Depends(get_memory_service),
):
    """Hard-delete a tiered memory item."""
    store_manager = service["store_manager"]

    try:
        tier_enum = MemoryTier(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    if tier_enum == MemoryTier.core:
        raise HTTPException(status_code=400, detail="Core blocks cannot be deleted directly")

    identity = resolve_authenticated_identity(http_request)
    operator = identity.display_name or identity.operator_id or "operator"
    store = store_manager.get_store(tier_enum)
    item = store.get(item_id)
    if not store.delete(item_id):
        raise HTTPException(status_code=404, detail="Memory item not found")

    _receipt_emitter(service).emit(
        action_type="memory_item_delete",
        action_name="delete_memory_item",
        inputs={"item_id": item_id, "tier": tier, "reason": request.reason},
        outputs={
            "item_id": item_id,
            "tier": tier,
            "actor": operator,
            "from_status": item.status.value if item else "",
        },
        **_identity_fields(identity),
    )

    logger.info("Operator %s deleted memory item %s in %s", operator, item_id, tier)
    return MemoryActionResponse(status="deleted", item_id=item_id, tier=tier, reason=request.reason)


# ---------------------------------------------------------------------------
# Context Compiler Endpoints
# ---------------------------------------------------------------------------
@router.post("/compile", response_model=CompileContextResponse)
async def compile_context(
    request: CompileContextRequest,
    service: dict = Depends(get_memory_service),
):
    """Compile a context for an objective."""
    compiler_service = service["compiler_service"]

    ctx = compiler_service.compile_for_objective(
        objective=request.objective,
        quest_id=request.quest_id,
        mode=request.mode,
        search_query=request.search_query,
        emit_receipt=True,
        receipt_emitter=_receipt_emitter(service),
    )

    return CompileContextResponse(
        context_id=ctx.context_id,
        token_estimate=ctx.token_estimate,
        token_breakdown=ctx.token_breakdown,
        included_blocks=[b.value for b in ctx.included_blocks],
        included_memory_count=len(ctx.included_memory_item_ids),
        excluded_count=len(ctx.excluded_candidates),
    )


# ---------------------------------------------------------------------------
# Stats Endpoint
# ---------------------------------------------------------------------------
@router.get("/stats")
async def get_memory_stats(service: dict = Depends(get_memory_service)):
    """Get memory subsystem statistics."""
    memory_index = service["memory_index"]
    core_store = service["core_store"]
    gate_validator = service["gate_validator"]

    index_stats = memory_index.get_stats()
    allowlist = gate_validator.get_allowlist_summary()
    budget_issues = core_store.validate_budgets()

    return {
        "index": index_stats,
        "core_blocks": {
            "total_tokens": core_store.total_tokens(),
            "budget_issues": budget_issues,
        },
        "gates": allowlist,
    }
