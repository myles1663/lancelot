"""
Memory vNext Schemas — Pydantic v2 models for the memory subsystem.

This module defines all data models for:
- Core Memory Blocks (persona, human, mission, operating_rules, workspace_state)
- Memory Items (working, episodic, archival tiers)
- Provenance tracking
- Memory Commits and Edits
- Compiled Context artifacts
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def generate_id() -> str:
    """Generate a unique ID for memory objects."""
    return uuid4().hex[:16]


class CoreBlockType(str, Enum):
    """Types of core memory blocks that are always included in context."""
    persona = "persona"
    human = "human"
    mission = "mission"
    operating_rules = "operating_rules"
    workspace_state = "workspace_state"


class ProvenanceType(str, Enum):
    """Types of evidence sources for memory provenance."""
    user_message = "user_message"
    receipt = "receipt"
    external_doc = "external_doc"
    system = "system"
    agent_inference = "agent_inference"


class MemoryTier(str, Enum):
    """Memory storage tiers with different lifecycles."""
    core = "core"
    working = "working"
    episodic = "episodic"
    archival = "archival"


class MemoryStatus(str, Enum):
    """Status of a memory item."""
    active = "active"
    staged = "staged"
    quarantined = "quarantined"
    deprecated = "deprecated"


class MemoryEditOp(str, Enum):
    """Types of memory edit operations."""
    insert = "insert"
    replace = "replace"
    delete = "delete"
    rethink = "rethink"  # Restricted operation


class CommitStatus(str, Enum):
    """Status of a memory commit."""
    staged = "staged"
    committed = "committed"
    rolled_back = "rolled_back"


class Provenance(BaseModel):
    """
    Tracks the source and evidence for a memory item.

    Every memory write must have provenance to support audit and rollback.
    """
    type: ProvenanceType
    ref: str = Field(description="Reference ID (message_id, receipt_id, doc_id)")
    snippet: Optional[str] = Field(default=None, description="Relevant excerpt from source")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": True}


class CoreBlock(BaseModel):
    """
    A core memory block that is always included in the compiled context.

    Core blocks are pinned, curated, and have hard token limits.
    """
    block_type: CoreBlockType
    content: str
    token_budget: int = Field(gt=0, description="Maximum allowed tokens for this block")
    token_count: int = Field(default=0, ge=0, description="Current token count")
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = Field(description="Who updated: 'owner', 'agent', or 'system'")
    status: MemoryStatus = Field(default=MemoryStatus.active)
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)

    @field_validator("updated_by")
    @classmethod
    def validate_updater(cls, v: str) -> str:
        allowed = {"owner", "agent", "system"}
        if v not in allowed:
            raise ValueError(f"updated_by must be one of {allowed}")
        return v

    def within_budget(self) -> bool:
        """Check if content is within token budget."""
        return self.token_count <= self.token_budget

    def budget_utilization(self) -> float:
        """Return budget utilization as a ratio (0.0 to 1.0+)."""
        if self.token_budget == 0:
            return 0.0
        return self.token_count / self.token_budget


class MemoryItem(BaseModel):
    """
    A memory item in working, episodic, or archival tier.

    Items have lifecycle management (TTL, decay) and can be searched.
    """
    id: str = Field(default_factory=generate_id)
    tier: MemoryTier
    namespace: str = Field(default="global", description="Scope: 'global', 'quest:<id>', 'project:<id>'")
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = Field(default=None)
    decay_half_life_days: Optional[int] = Field(default=None, ge=1)
    provenance: list[Provenance] = Field(default_factory=list)
    status: MemoryStatus = Field(default=MemoryStatus.active)
    token_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if this item has expired."""
        if self.expires_at is None:
            return False
        now = now or datetime.utcnow()
        return now >= self.expires_at

    def apply_decay(self, days_elapsed: int) -> float:
        """
        Apply confidence decay based on half-life.

        Returns the new confidence value.
        """
        if self.decay_half_life_days is None or days_elapsed <= 0:
            return self.confidence

        decay_factor = 0.5 ** (days_elapsed / self.decay_half_life_days)
        return self.confidence * decay_factor


class MemoryEdit(BaseModel):
    """
    A single edit operation within a memory commit.

    Edits are atomic and reversible.
    """
    id: str = Field(default_factory=generate_id)
    op: MemoryEditOp
    target: str = Field(description="Target: 'core:<type>' or '<tier>:<id>'")
    selector: Optional[str] = Field(default=None, description="For replace/delete: what to match")
    before: Optional[str] = Field(default=None, description="Content before edit")
    after: Optional[str] = Field(default=None, description="Content after edit")
    reason: str = Field(description="Why this edit is being made")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    provenance: list[Provenance] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("Target must be in format 'tier:id' or 'core:type'")
        return v

    def is_core_edit(self) -> bool:
        """Check if this edit targets a core block."""
        return self.target.startswith("core:")

    def get_target_parts(self) -> tuple[str, str]:
        """Parse target into (tier_or_core, id_or_type)."""
        parts = self.target.split(":", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""


class MemoryCommit(BaseModel):
    """
    An atomic set of memory edits with full audit trail.

    Commits enable rollback and diff tracking.
    """
    commit_id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(description="Agent/model name that created this commit")
    edits: list[MemoryEdit] = Field(default_factory=list)
    status: CommitStatus = Field(default=CommitStatus.staged)
    parent_commit_id: Optional[str] = Field(default=None)
    receipt_id: Optional[str] = Field(default=None)
    message: str = Field(default="", description="Commit message describing the changes")
    rollback_of: Optional[str] = Field(default=None, description="If this is a rollback, the commit being reverted")

    def add_edit(self, edit: MemoryEdit) -> None:
        """Add an edit to this commit."""
        self.edits.append(edit)

    def has_core_edits(self) -> bool:
        """Check if any edits target core blocks."""
        return any(edit.is_core_edit() for edit in self.edits)

    def get_affected_targets(self) -> set[str]:
        """Get all targets affected by this commit."""
        return {edit.target for edit in self.edits}


class CompiledContext(BaseModel):
    """
    The output of the Context Compiler.

    Represents a fully assembled context ready for the LLM.
    """
    context_id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    objective: str = Field(description="The objective/goal for this context")
    quest_id: Optional[str] = Field(default=None)
    mode: str = Field(default="normal", description="Execution mode: 'normal' or 'crusader'")

    included_blocks: list[CoreBlockType] = Field(default_factory=list)
    included_memory_item_ids: list[str] = Field(default_factory=list)
    excluded_candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Items excluded with reasons (budget, low confidence, quarantined)"
    )

    token_estimate: int = Field(default=0, ge=0)
    token_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Token counts per section"
    )
    rendered_prompt: str = Field(default="")

    compiler_version: str = Field(default="1.0.0")
    soul_version: Optional[str] = Field(default=None)

    def add_exclusion(self, item_id: str, reason: str, **kwargs: Any) -> None:
        """Record why an item was excluded from context."""
        self.excluded_candidates.append({
            "item_id": item_id,
            "reason": reason,
            **kwargs
        })


class CoreBlocksSnapshot(BaseModel):
    """
    A snapshot of all core blocks for persistence and rollback.
    """
    snapshot_id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    blocks: dict[str, CoreBlock] = Field(default_factory=dict)
    commit_id: Optional[str] = Field(default=None, description="Commit that created this snapshot")

    def get_block(self, block_type: CoreBlockType) -> Optional[CoreBlock]:
        """Get a core block by type."""
        return self.blocks.get(block_type.value)

    def set_block(self, block: CoreBlock) -> None:
        """Set a core block."""
        self.blocks[block.block_type.value] = block

    def total_tokens(self) -> int:
        """Calculate total tokens across all blocks."""
        return sum(block.token_count for block in self.blocks.values())
