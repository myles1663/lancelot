"""
Memory vNext API — FastAPI endpoints for memory operations.

This module provides REST API endpoints for:
- Core block management
- Memory search
- Commit operations
- Quarantine management
- Context compilation
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from .schemas import (
    CoreBlockType,
    MemoryEdit,
    MemoryEditOp,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)

logger = logging.getLogger(__name__)

import threading

# Create router for memory endpoints
router = APIRouter(prefix="/memory", tags=["memory"])


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
    query: str
    tiers: list[str] = Field(default=["working", "episodic", "archival"])
    namespace: Optional[str] = None
    tags: Optional[list[str]] = None
    min_confidence: float = 0.3
    limit: int = 20


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


class BeginCommitRequest(BaseModel):
    """Request to begin a staged commit."""
    created_by: str
    message: str = ""


class BeginCommitResponse(BaseModel):
    """Response with staged commit ID."""
    commit_id: str
    status: str


class AddEditRequest(BaseModel):
    """Request to add an edit to a staged commit."""
    op: str  # insert, replace, delete
    target: str  # core:type or tier:id
    after: Optional[str] = None
    reason: str
    confidence: float = 0.5
    provenance_type: Optional[str] = None
    provenance_ref: Optional[str] = None


class AddEditResponse(BaseModel):
    """Response for added edit."""
    edit_id: str
    commit_id: str


class FinishCommitRequest(BaseModel):
    """Request to finish a staged commit."""
    receipt_id: Optional[str] = None


class FinishCommitResponse(BaseModel):
    """Response for finished commit."""
    commit_id: str
    status: str
    edit_count: int


class RollbackRequest(BaseModel):
    """Request to rollback a commit."""
    reason: str
    created_by: str


class RollbackResponse(BaseModel):
    """Response for rollback."""
    rollback_commit_id: str
    rolled_back_commit_id: str


class QuarantineItemResponse(BaseModel):
    """Quarantined item info."""
    id: str
    tier: str
    title: str
    content: str
    status: str


class QuarantineResponse(BaseModel):
    """Response for quarantine listing."""
    core_blocks: list[dict[str, Any]]
    items: list[QuarantineItemResponse]


class PromoteRequest(BaseModel):
    """Request to promote a quarantined item."""
    approver: str


class CompileContextRequest(BaseModel):
    """Request to compile context."""
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
                detail="Memory vNext is disabled. Set FEATURE_MEMORY_VNEXT=true"
            )

        from .store import CoreBlockStore
        from .sqlite_store import MemoryStoreManager
        from .commits import CommitManager
        from .gates import WriteGateValidator, QuarantineManager
        from .index import MemoryIndex
        from .compiler import ContextCompilerService

        data_dir = Path("lancelot_data")

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
        }

    return _memory_service


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


# ---------------------------------------------------------------------------
# Commit Endpoints
# ---------------------------------------------------------------------------
@router.post("/commit/begin", response_model=BeginCommitResponse)
async def begin_commit(
    request: BeginCommitRequest,
    service: dict = Depends(get_memory_service),
):
    """Begin a new staged commit."""
    commit_manager = service["commit_manager"]

    commit_id = commit_manager.begin_edits(
        created_by=request.created_by,
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
    gate_result = gate_validator.validate_edit(edit, editor="agent")
    if not gate_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Edit blocked by write gate: {gate_result.reason}",
        )

    try:
        edit_id = commit_manager.add_edit(
            commit_id=commit_id,
            op=op,
            target=request.target,
            after=request.after,
            reason=request.reason,
            confidence=request.confidence,
            provenance=provenance,
        )

        return AddEditResponse(
            edit_id=edit_id,
            commit_id=commit_id,
        )

    except Exception as e:
        logger.error("Failed to add edit to commit %s: %s", commit_id, e)
        raise HTTPException(status_code=400, detail="Failed to add edit to commit")


@router.post("/commit/{commit_id}/finish", response_model=FinishCommitResponse)
async def finish_commit(
    commit_id: str,
    request: FinishCommitRequest,
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
    service: dict = Depends(get_memory_service),
):
    """Rollback a commit."""
    commit_manager = service["commit_manager"]

    try:
        rollback_id = commit_manager.rollback(
            commit_id=commit_id,
            reason=request.reason,
            created_by=request.created_by,
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
        ))

    return QuarantineResponse(
        core_blocks=core_blocks,
        items=items,
    )


@router.post("/promote/{item_id}")
async def promote_item(
    item_id: str,
    request: PromoteRequest,
    tier: str = "working",
    service: dict = Depends(get_memory_service),
):
    """Promote a quarantined item to active."""
    quarantine_manager = service["quarantine_manager"]

    # Check if it's a core block
    if item_id.startswith("core:"):
        block_type_str = item_id.replace("core:", "")
        try:
            block_type = CoreBlockType(block_type_str)
            result = quarantine_manager.approve_core_block(block_type, request.approver)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid block type: {block_type_str}")
    else:
        result = quarantine_manager.approve_item(item_id, tier, request.approver)

    if not result:
        raise HTTPException(status_code=404, detail="Item not found or not quarantined")

    return {"status": "promoted", "item_id": item_id}


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
