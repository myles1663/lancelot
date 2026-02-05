"""
Memory vNext Store — Core block persistence and management.

This module provides the CoreBlockStore for persisting and managing
core memory blocks (persona, human, mission, operating_rules, workspace_state).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional

from .config import MemoryConfig, default_config, MEMORY_DIR, CORE_BLOCKS_FILE
from .schemas import (
    CoreBlock,
    CoreBlockType,
    CoreBlocksSnapshot,
    MemoryStatus,
    Provenance,
    ProvenanceType,
)

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """
    Rough token estimation for text.

    Uses a simple heuristic: ~4 characters per token on average.
    This should be replaced with actual tokenizer later.
    """
    if not text:
        return 0
    # Rough estimate: 1 token per 4 characters
    return max(1, len(text) // 4)


class CoreBlockStore:
    """
    Persistent store for core memory blocks.

    Provides thread-safe CRUD operations with JSON file persistence.
    Supports snapshotting for rollback capability.
    """

    def __init__(
        self,
        data_dir: str | Path,
        config: Optional[MemoryConfig] = None,
    ):
        """
        Initialize the core block store.

        Args:
            data_dir: Base directory for lancelot_data
            config: Memory configuration (uses default if not provided)
        """
        self.data_dir = Path(data_dir)
        self.memory_dir = self.data_dir / MEMORY_DIR
        self.blocks_file = self.memory_dir / CORE_BLOCKS_FILE
        self.config = config or default_config
        self._lock = RLock()
        self._blocks: dict[str, CoreBlock] = {}
        self._initialized = False

    def initialize(self) -> None:
        """
        Initialize the store, creating directories and loading existing data.
        """
        with self._lock:
            if self._initialized:
                return

            # Create directories
            self.memory_dir.mkdir(parents=True, exist_ok=True)

            # Load existing blocks or create defaults
            if self.blocks_file.exists():
                self._load_from_file()
            else:
                self._create_default_blocks()
                self._save_to_file()

            self._initialized = True
            logger.info("CoreBlockStore initialized at %s", self.memory_dir)

    def _create_default_blocks(self) -> None:
        """Create default core blocks with empty content."""
        now = datetime.utcnow()
        system_provenance = Provenance(
            type=ProvenanceType.system,
            ref="initialization",
            snippet="Default block created during initialization",
            timestamp=now,
        )

        for block_type in CoreBlockType:
            budget = self.config.get_block_budget(block_type.value)
            self._blocks[block_type.value] = CoreBlock(
                block_type=block_type,
                content="",
                token_budget=budget,
                token_count=0,
                updated_at=now,
                updated_by="system",
                status=MemoryStatus.active,
                provenance=[system_provenance],
                confidence=1.0,
                version=1,
            )

    def _load_from_file(self) -> None:
        """Load blocks from JSON file."""
        try:
            with open(self.blocks_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for block_type, block_data in data.get("blocks", {}).items():
                try:
                    self._blocks[block_type] = CoreBlock.model_validate(block_data)
                except Exception as e:
                    logger.error("Failed to load block %s: %s", block_type, e)
                    # Create default for corrupted block
                    self._create_default_block(CoreBlockType(block_type))

            logger.info("Loaded %d core blocks from %s", len(self._blocks), self.blocks_file)

        except json.JSONDecodeError as e:
            logger.error("Corrupted blocks file, creating backup and reinitializing: %s", e)
            self._backup_corrupted_file()
            self._create_default_blocks()
        except Exception as e:
            logger.error("Error loading blocks file: %s", e)
            raise

    def _create_default_block(self, block_type: CoreBlockType) -> None:
        """Create a single default block."""
        now = datetime.utcnow()
        budget = self.config.get_block_budget(block_type.value)
        self._blocks[block_type.value] = CoreBlock(
            block_type=block_type,
            content="",
            token_budget=budget,
            token_count=0,
            updated_at=now,
            updated_by="system",
            status=MemoryStatus.active,
            provenance=[Provenance(
                type=ProvenanceType.system,
                ref="recovery",
                snippet="Block recreated after corruption",
                timestamp=now,
            )],
            confidence=1.0,
            version=1,
        )

    def _backup_corrupted_file(self) -> None:
        """Backup a corrupted blocks file."""
        if self.blocks_file.exists():
            backup_path = self.blocks_file.with_suffix(
                f".corrupted.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            )
            shutil.copy2(self.blocks_file, backup_path)
            logger.warning("Backed up corrupted file to %s", backup_path)

    def _save_to_file(self) -> None:
        """Save blocks to JSON file."""
        data = {
            "version": "1.0",
            "updated_at": datetime.utcnow().isoformat(),
            "blocks": {
                block_type: block.model_dump(mode="json")
                for block_type, block in self._blocks.items()
            },
        }

        # Write to temp file first, then rename (atomic on most systems)
        temp_file = self.blocks_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        # Atomic rename
        temp_file.replace(self.blocks_file)
        logger.debug("Saved core blocks to %s", self.blocks_file)

    def get_block(self, block_type: CoreBlockType) -> Optional[CoreBlock]:
        """
        Get a core block by type.

        Args:
            block_type: The type of block to retrieve

        Returns:
            The CoreBlock or None if not found
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return self._blocks.get(block_type.value)

    def get_all_blocks(self) -> dict[str, CoreBlock]:
        """
        Get all core blocks.

        Returns:
            Dictionary mapping block type to CoreBlock
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return self._blocks.copy()

    def set_block(
        self,
        block_type: CoreBlockType,
        content: str,
        updated_by: str,
        provenance: list[Provenance],
        confidence: float = 1.0,
        status: MemoryStatus = MemoryStatus.active,
    ) -> CoreBlock:
        """
        Set or update a core block.

        Args:
            block_type: Type of block to set
            content: New content for the block
            updated_by: Who is updating ('owner', 'agent', 'system')
            provenance: Evidence for this update
            confidence: Confidence score (0.0-1.0)
            status: Status of the block

        Returns:
            The updated CoreBlock

        Raises:
            ValueError: If content exceeds token budget
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            token_count = estimate_tokens(content)
            budget = self.config.get_block_budget(block_type.value)

            if token_count > budget:
                raise ValueError(
                    f"Content exceeds token budget for {block_type.value}: "
                    f"{token_count} > {budget}"
                )

            existing = self._blocks.get(block_type.value)
            version = (existing.version + 1) if existing else 1

            block = CoreBlock(
                block_type=block_type,
                content=content,
                token_budget=budget,
                token_count=token_count,
                updated_at=datetime.utcnow(),
                updated_by=updated_by,
                status=status,
                provenance=provenance,
                confidence=confidence,
                version=version,
            )

            self._blocks[block_type.value] = block
            self._save_to_file()

            logger.info(
                "Updated core block %s (v%d, %d tokens, by %s)",
                block_type.value, version, token_count, updated_by
            )

            return block

    def update_block_status(
        self,
        block_type: CoreBlockType,
        status: MemoryStatus,
    ) -> Optional[CoreBlock]:
        """
        Update just the status of a core block.

        Args:
            block_type: Type of block to update
            status: New status

        Returns:
            The updated CoreBlock or None if not found
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            block = self._blocks.get(block_type.value)
            if block is None:
                return None

            # Create updated block (Pydantic models are immutable by default)
            updated = CoreBlock(
                block_type=block.block_type,
                content=block.content,
                token_budget=block.token_budget,
                token_count=block.token_count,
                updated_at=datetime.utcnow(),
                updated_by=block.updated_by,
                status=status,
                provenance=block.provenance,
                confidence=block.confidence,
                version=block.version,
            )

            self._blocks[block_type.value] = updated
            self._save_to_file()

            return updated

    def create_snapshot(self, commit_id: Optional[str] = None) -> CoreBlocksSnapshot:
        """
        Create a snapshot of all current core blocks.

        Args:
            commit_id: Optional commit ID associated with this snapshot

        Returns:
            A CoreBlocksSnapshot containing all blocks
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            snapshot = CoreBlocksSnapshot(
                created_at=datetime.utcnow(),
                blocks=self._blocks.copy(),
                commit_id=commit_id,
            )

            logger.debug("Created snapshot %s with %d blocks", snapshot.snapshot_id, len(snapshot.blocks))
            return snapshot

    def restore_snapshot(self, snapshot: CoreBlocksSnapshot) -> None:
        """
        Restore core blocks from a snapshot.

        Args:
            snapshot: The snapshot to restore from
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            self._blocks = snapshot.blocks.copy()
            self._save_to_file()

            logger.info(
                "Restored %d blocks from snapshot %s",
                len(self._blocks), snapshot.snapshot_id
            )

    def total_tokens(self) -> int:
        """
        Calculate total tokens across all core blocks.

        Returns:
            Total token count
        """
        with self._lock:
            if not self._initialized:
                self.initialize()
            return sum(block.token_count for block in self._blocks.values())

    def validate_budgets(self) -> list[tuple[str, str]]:
        """
        Validate all blocks are within their budgets.

        Returns:
            List of (block_type, message) for any issues found
        """
        with self._lock:
            if not self._initialized:
                self.initialize()

            issues = []
            for block_type, block in self._blocks.items():
                is_valid, message = self.config.validate_block_size(
                    block_type, block.token_count
                )
                if message:  # Has warning or error
                    issues.append((block_type, message))

            return issues

    def bootstrap_from_user_file(self, user_file_path: str | Path) -> Optional[CoreBlock]:
        """
        Bootstrap the 'human' block from USER.md file.

        Args:
            user_file_path: Path to USER.md file (must be under data directory)

        Returns:
            The updated human block, or None if file doesn't exist
        """
        user_file = Path(user_file_path)

        # Validate file is under the data directory to prevent arbitrary reads
        try:
            resolved = user_file.resolve()
            data_resolved = self.data_dir.resolve()
            if not str(resolved).startswith(str(data_resolved)):
                logger.error(
                    "SECURITY: bootstrap_from_user_file blocked — "
                    "path outside data directory: %s", user_file_path
                )
                return None
        except OSError as e:
            logger.error("Invalid path for bootstrap: %s", e)
            return None

        if not user_file.exists():
            logger.info("USER.md not found at %s, skipping bootstrap", user_file)
            return None

        try:
            content = user_file.read_text(encoding="utf-8")

            provenance = Provenance(
                type=ProvenanceType.external_doc,
                ref=str(user_file),
                snippet=content[:100] + "..." if len(content) > 100 else content,
                timestamp=datetime.utcnow(),
            )

            return self.set_block(
                block_type=CoreBlockType.human,
                content=content,
                updated_by="system",
                provenance=[provenance],
                confidence=1.0,
                status=MemoryStatus.active,
            )

        except Exception as e:
            logger.error("Failed to bootstrap from USER.md: %s", e)
            return None
