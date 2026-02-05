"""
Memory vNext — Governed Block Memory + Context Compiler.

This package provides Lancelot's memory subsystem with:
- Core Memory Blocks (persona, human, mission, operating_rules, workspace_state)
- Tiered Memory (working, episodic, archival)
- Governed Self-Edits with atomic commits and rollback
- Context Compiler for deterministic prompt assembly

Feature Flag: FEATURE_MEMORY_VNEXT (default: false)

Usage:
    from src.core.memory import get_memory_service, is_memory_enabled

    if is_memory_enabled():
        service = get_memory_service()
        block = service.get_core_block(CoreBlockType.persona)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..feature_flags import FEATURE_MEMORY_VNEXT

if TYPE_CHECKING:
    from .store import CoreBlockStore

logger = logging.getLogger(__name__)

# Module-level singleton
_core_block_store: Optional["CoreBlockStore"] = None


def is_memory_enabled() -> bool:
    """
    Check if the Memory vNext feature is enabled.

    Returns:
        True if FEATURE_MEMORY_VNEXT is enabled
    """
    return FEATURE_MEMORY_VNEXT


def get_core_block_store(data_dir: Optional[str | Path] = None) -> "CoreBlockStore":
    """
    Get or create the CoreBlockStore singleton.

    Args:
        data_dir: Base data directory (default: lancelot_data/)

    Returns:
        The CoreBlockStore instance

    Raises:
        RuntimeError: If FEATURE_MEMORY_VNEXT is disabled
    """
    global _core_block_store

    if not is_memory_enabled():
        raise RuntimeError(
            "Memory vNext is disabled. Set FEATURE_MEMORY_VNEXT=true to enable."
        )

    if _core_block_store is None:
        from .store import CoreBlockStore

        if data_dir is None:
            data_dir = Path("lancelot_data")

        _core_block_store = CoreBlockStore(data_dir=data_dir)
        _core_block_store.initialize()
        logger.info("Memory vNext CoreBlockStore initialized")

    return _core_block_store


def reset_store() -> None:
    """
    Reset the store singleton. Used for testing.
    """
    global _core_block_store
    _core_block_store = None


# Re-export commonly used types
from .schemas import (
    CoreBlock,
    CoreBlockType,
    CoreBlocksSnapshot,
    MemoryCommit,
    MemoryEdit,
    MemoryEditOp,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
    CompiledContext,
    CommitStatus,
)

from .config import (
    MemoryConfig,
    default_config,
    DEFAULT_CORE_BLOCK_BUDGETS,
    MAX_CONTEXT_TOKENS,
)

from .sqlite_store import (
    MemoryItemStore,
    MemoryStoreManager,
)

__all__ = [
    # Feature flag
    "is_memory_enabled",
    "get_core_block_store",
    "reset_store",
    # Schemas
    "CoreBlock",
    "CoreBlockType",
    "CoreBlocksSnapshot",
    "MemoryCommit",
    "MemoryEdit",
    "MemoryEditOp",
    "MemoryItem",
    "MemoryStatus",
    "MemoryTier",
    "Provenance",
    "ProvenanceType",
    "CompiledContext",
    "CommitStatus",
    # Config
    "MemoryConfig",
    "default_config",
    "DEFAULT_CORE_BLOCK_BUDGETS",
    "MAX_CONTEXT_TOKENS",
    # SQLite Stores
    "MemoryItemStore",
    "MemoryStoreManager",
]
