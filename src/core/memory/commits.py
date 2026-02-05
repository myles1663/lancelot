"""
Memory vNext Commits — Atomic memory edit management with rollback.

This module provides the CommitManager for:
- Staged edit workflow (begin → add edits → finish)
- Atomic commit application
- Diff generation and tracking
- Receipt emission
- Rollback support
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from .config import MEMORY_DIR, COMMITS_DIR
from .schemas import (
    CommitStatus,
    CoreBlockType,
    CoreBlocksSnapshot,
    MemoryCommit,
    MemoryEdit,
    MemoryEditOp,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    generate_id,
)
from .store import CoreBlockStore, estimate_tokens
from .sqlite_store import MemoryStoreManager

logger = logging.getLogger(__name__)


MAX_RETAINED_SNAPSHOTS = 50


class CommitError(Exception):
    """Error during commit operations."""
    pass


class CommitManager:
    """
    Manages atomic memory commits with staged edits and rollback.

    Provides a transactional workflow:
    1. begin_edits() - Start a new staged commit
    2. add_edit() - Add edits to the staged commit
    3. finish_edits() - Apply all edits atomically

    Features:
    - Snapshot-based rollback
    - Diff tracking for audit
    - Receipt generation
    - Parent commit chaining
    """

    def __init__(
        self,
        core_store: CoreBlockStore,
        store_manager: MemoryStoreManager,
        data_dir: str | Path,
    ):
        """
        Initialize the commit manager.

        Args:
            core_store: Store for core blocks
            store_manager: Manager for tiered memory stores
            data_dir: Base data directory
        """
        self.core_store = core_store
        self.store_manager = store_manager
        self.data_dir = Path(data_dir)
        self.commits_dir = self.data_dir / MEMORY_DIR / COMMITS_DIR

        self._lock = RLock()
        self._staged_commits: dict[str, MemoryCommit] = {}
        self._snapshots: dict[str, CoreBlocksSnapshot] = {}
        self._item_undo_log: list[tuple[str, str, str, Optional[MemoryItem]]] = []
        self._last_commit_id: Optional[str] = None

        # Ensure commits directory exists
        self.commits_dir.mkdir(parents=True, exist_ok=True)

    def begin_edits(
        self,
        created_by: str,
        message: str = "",
    ) -> str:
        """
        Begin a new staged commit.

        Args:
            created_by: Agent/model name creating the commit
            message: Optional commit message

        Returns:
            The staged commit ID
        """
        with self._lock:
            commit = MemoryCommit(
                created_by=created_by,
                message=message,
                status=CommitStatus.staged,
                parent_commit_id=self._last_commit_id,
            )

            # Create snapshot for potential rollback
            snapshot = self.core_store.create_snapshot(commit_id=commit.commit_id)

            self._staged_commits[commit.commit_id] = commit
            self._snapshots[commit.commit_id] = snapshot

            # Evict oldest snapshots if limit exceeded
            if len(self._snapshots) > MAX_RETAINED_SNAPSHOTS:
                oldest_keys = list(self._snapshots.keys())[:-MAX_RETAINED_SNAPSHOTS]
                for key in oldest_keys:
                    del self._snapshots[key]
                    self._staged_commits.pop(key, None)

            logger.info("Started staged commit %s by %s", commit.commit_id, created_by)
            return commit.commit_id

    def add_edit(
        self,
        commit_id: str,
        op: MemoryEditOp,
        target: str,
        reason: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        selector: Optional[str] = None,
        confidence: float = 0.5,
        provenance: Optional[list[Provenance]] = None,
    ) -> str:
        """
        Add an edit to a staged commit.

        Args:
            commit_id: The staged commit ID
            op: Edit operation type
            target: Target (e.g., "core:human" or "working:item_id")
            reason: Reason for the edit
            before: Content before edit (for replace/delete)
            after: Content after edit (for insert/replace)
            selector: Optional selector for partial edits
            confidence: Confidence score for this edit
            provenance: Evidence for this edit

        Returns:
            The edit ID

        Raises:
            CommitError: If commit not found or not staged
        """
        with self._lock:
            commit = self._staged_commits.get(commit_id)
            if commit is None:
                raise CommitError(f"Staged commit {commit_id} not found")

            if commit.status != CommitStatus.staged:
                raise CommitError(f"Commit {commit_id} is not staged (status: {commit.status})")

            # Capture current content for 'before' if not provided
            if before is None and op in (MemoryEditOp.replace, MemoryEditOp.delete):
                before = self._get_current_content(target)

            edit = MemoryEdit(
                op=op,
                target=target,
                selector=selector,
                before=before,
                after=after,
                reason=reason,
                confidence=confidence,
                provenance=provenance or [],
            )

            commit.add_edit(edit)
            logger.debug("Added edit %s to commit %s: %s %s", edit.id, commit_id, op.value, target)
            return edit.id

    def finish_edits(
        self,
        commit_id: str,
        receipt_id: Optional[str] = None,
    ) -> str:
        """
        Finish and apply a staged commit atomically.

        Args:
            commit_id: The staged commit ID
            receipt_id: Optional receipt ID to associate

        Returns:
            The committed commit ID

        Raises:
            CommitError: If commit not found, not staged, or apply fails
        """
        with self._lock:
            commit = self._staged_commits.get(commit_id)
            if commit is None:
                raise CommitError(f"Staged commit {commit_id} not found")

            if commit.status != CommitStatus.staged:
                raise CommitError(f"Commit {commit_id} is not staged")

            if not commit.edits:
                raise CommitError(f"Commit {commit_id} has no edits")

            try:
                # Clear undo log for this commit
                self._item_undo_log = []

                # Apply all edits
                for edit in commit.edits:
                    self._apply_edit(edit)

                # Update commit status
                commit.status = CommitStatus.committed
                commit.receipt_id = receipt_id

                # Persist commit
                self._persist_commit(commit)

                # Update last commit pointer
                self._last_commit_id = commit.commit_id

                # Clean up staged state (keep snapshot for rollback)
                del self._staged_commits[commit_id]
                self._item_undo_log = []

                logger.info(
                    "Committed %s with %d edits",
                    commit_id, len(commit.edits)
                )
                return commit.commit_id

            except Exception as e:
                logger.error("Failed to apply commit %s: %s", commit_id, e)
                # Rollback on failure — core blocks and item edits
                self._rollback_to_snapshot(commit_id)
                self._rollback_item_edits()
                raise CommitError(f"Commit failed: {e}") from e

    def cancel_edits(self, commit_id: str) -> bool:
        """
        Cancel a staged commit without applying.

        Args:
            commit_id: The staged commit ID

        Returns:
            True if cancelled, False if not found
        """
        with self._lock:
            if commit_id in self._staged_commits:
                del self._staged_commits[commit_id]
                if commit_id in self._snapshots:
                    del self._snapshots[commit_id]
                logger.info("Cancelled staged commit %s", commit_id)
                return True
            return False

    def rollback(
        self,
        commit_id: str,
        reason: str,
        created_by: str,
    ) -> str:
        """
        Rollback to the state before a commit.

        Creates a new commit that reverses the specified commit.

        Args:
            commit_id: The commit ID to rollback
            reason: Reason for rollback
            created_by: Who is performing the rollback

        Returns:
            The rollback commit ID

        Raises:
            CommitError: If commit or snapshot not found
        """
        with self._lock:
            snapshot = self._snapshots.get(commit_id)
            if snapshot is None:
                raise CommitError(f"Snapshot for commit {commit_id} not found")

            # Create rollback commit
            rollback_commit = MemoryCommit(
                created_by=created_by,
                message=f"Rollback of {commit_id}: {reason}",
                status=CommitStatus.committed,
                parent_commit_id=self._last_commit_id,
                rollback_of=commit_id,
            )

            # Restore from snapshot
            self.core_store.restore_snapshot(snapshot)

            # Persist rollback commit
            self._persist_commit(rollback_commit)
            self._last_commit_id = rollback_commit.commit_id

            logger.info("Rolled back commit %s -> %s", commit_id, rollback_commit.commit_id)
            return rollback_commit.commit_id

    def _get_current_content(self, target: str) -> Optional[str]:
        """Get current content for a target."""
        tier, id_or_type = self._parse_target(target)

        if tier == "core":
            block = self.core_store.get_block(CoreBlockType(id_or_type))
            return block.content if block else None

        store = self._get_store_for_tier(tier)
        if store:
            item = store.get(id_or_type)
            return item.content if item else None

        return None

    def _apply_edit(self, edit: MemoryEdit) -> None:
        """Apply a single edit."""
        tier, id_or_type = self._parse_target(edit.target)

        if tier == "core":
            self._apply_core_edit(edit, CoreBlockType(id_or_type))
        else:
            self._apply_item_edit(edit, tier, id_or_type)

    def _apply_core_edit(self, edit: MemoryEdit, block_type: CoreBlockType) -> None:
        """Apply an edit to a core block."""
        if edit.op == MemoryEditOp.insert:
            # Insert adds content to an existing block
            current = self.core_store.get_block(block_type)
            new_content = (current.content + "\n" + edit.after) if current and current.content else (edit.after or "")

            self.core_store.set_block(
                block_type=block_type,
                content=new_content,
                updated_by="agent",
                provenance=edit.provenance,
                confidence=edit.confidence,
                status=MemoryStatus.staged,  # Core edits go to staged first
            )

        elif edit.op == MemoryEditOp.replace:
            self.core_store.set_block(
                block_type=block_type,
                content=edit.after or "",
                updated_by="agent",
                provenance=edit.provenance,
                confidence=edit.confidence,
                status=MemoryStatus.staged,
            )

        elif edit.op == MemoryEditOp.delete:
            self.core_store.set_block(
                block_type=block_type,
                content="",
                updated_by="agent",
                provenance=edit.provenance,
                confidence=1.0,
                status=MemoryStatus.active,
            )

        elif edit.op == MemoryEditOp.rethink:
            raise CommitError("rethink operation not yet implemented")

    def _apply_item_edit(self, edit: MemoryEdit, tier: str, item_id: str) -> None:
        """Apply an edit to a memory item, recording undo info for rollback."""
        store = self._get_store_for_tier(tier)
        if store is None:
            raise CommitError(f"Unknown tier: {tier}")

        if edit.op == MemoryEditOp.insert:
            # Insert creates a new item
            actual_id = item_id if item_id else generate_id()
            item = MemoryItem(
                id=actual_id,
                tier=MemoryTier(tier),
                title=edit.reason,  # Use reason as title for new items
                content=edit.after or "",
                confidence=edit.confidence,
                provenance=edit.provenance,
            )
            store.insert(item)
            # Undo: delete the inserted item
            self._item_undo_log.append((tier, "delete", actual_id, None))

        elif edit.op == MemoryEditOp.replace:
            item = store.get(item_id)
            if item is None:
                raise CommitError(f"Item {item_id} not found in {tier}")

            # Save original for undo
            from copy import deepcopy
            original = deepcopy(item)
            self._item_undo_log.append((tier, "restore", item_id, original))

            item.content = edit.after or ""
            item.confidence = edit.confidence
            item.provenance.extend(edit.provenance)
            item.updated_at = datetime.utcnow()
            store.update(item)

        elif edit.op == MemoryEditOp.delete:
            # Save original for undo
            item = store.get(item_id)
            if item:
                from copy import deepcopy
                self._item_undo_log.append((tier, "restore", item_id, deepcopy(item)))
            store.delete(item_id)

        elif edit.op == MemoryEditOp.rethink:
            raise CommitError("rethink operation not yet implemented")

    def _get_store_for_tier(self, tier: str) -> Optional[Any]:
        """Get the store for a tier."""
        tier_map = {
            "working": self.store_manager.working,
            "episodic": self.store_manager.episodic,
            "archival": self.store_manager.archival,
        }
        return tier_map.get(tier)

    def _parse_target(self, target: str) -> tuple[str, str]:
        """Parse a target string into (tier, id_or_type)."""
        parts = target.split(":", 1)
        if len(parts) != 2:
            raise CommitError(f"Invalid target format: {target}")
        return parts[0], parts[1]

    def _rollback_to_snapshot(self, commit_id: str) -> None:
        """Rollback core blocks to snapshot."""
        snapshot = self._snapshots.get(commit_id)
        if snapshot:
            self.core_store.restore_snapshot(snapshot)
            logger.warning("Rolled back core blocks for failed commit %s", commit_id)

    def _rollback_item_edits(self) -> None:
        """Rollback item-level edits using the undo log (reverse order)."""
        for tier, action, item_id, original_item in reversed(self._item_undo_log):
            try:
                store = self._get_store_for_tier(tier)
                if store is None:
                    continue
                if action == "delete":
                    store.delete(item_id)
                elif action == "restore" and original_item is not None:
                    store.update(original_item)
            except Exception as exc:
                logger.error("Failed to rollback item %s in %s: %s", item_id, tier, exc)
        count = len(self._item_undo_log)
        self._item_undo_log = []
        logger.warning("Rolled back %d item-level edits", count)

    @staticmethod
    def _validate_commit_id(commit_id: str) -> None:
        """Validate commit_id is safe for use in file paths."""
        if not re.match(r'^[a-zA-Z0-9_-]+$', commit_id):
            raise ValueError(f"Invalid commit_id format: {commit_id}")

    def _persist_commit(self, commit: MemoryCommit) -> None:
        """Persist a commit to disk."""
        self._validate_commit_id(commit.commit_id)
        commit_file = self.commits_dir / f"{commit.commit_id}.json"

        # Verify resolved path stays under commits_dir
        if not str(commit_file.resolve()).startswith(str(self.commits_dir.resolve())):
            raise ValueError(f"Commit path escapes commits directory: {commit.commit_id}")

        data = commit.model_dump(mode="json")

        with open(commit_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.debug("Persisted commit %s to %s", commit.commit_id, commit_file)

    def load_commit(self, commit_id: str) -> Optional[MemoryCommit]:
        """
        Load a commit from disk.

        Args:
            commit_id: The commit ID

        Returns:
            The MemoryCommit or None if not found
        """
        try:
            self._validate_commit_id(commit_id)
        except ValueError as e:
            logger.error("Invalid commit_id: %s", e)
            return None

        commit_file = self.commits_dir / f"{commit_id}.json"

        if not str(commit_file.resolve()).startswith(str(self.commits_dir.resolve())):
            logger.error("Commit path escapes commits directory: %s", commit_id)
            return None

        if not commit_file.exists():
            return None

        try:
            with open(commit_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return MemoryCommit.model_validate(data)
        except Exception as e:
            logger.error("Failed to load commit %s: %s", commit_id, e)
            return None

    def list_commits(self, limit: int = 50) -> list[MemoryCommit]:
        """
        List recent commits.

        Args:
            limit: Maximum commits to return

        Returns:
            List of MemoryCommit objects
        """
        commits = []
        commit_files = sorted(
            self.commits_dir.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for commit_file in commit_files[:limit]:
            try:
                with open(commit_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                commits.append(MemoryCommit.model_validate(data))
            except Exception as e:
                logger.warning("Failed to load commit file %s: %s", commit_file, e)

        return commits

    def get_staged_commit(self, commit_id: str) -> Optional[MemoryCommit]:
        """
        Get a staged commit.

        Args:
            commit_id: The commit ID

        Returns:
            The staged MemoryCommit or None
        """
        return self._staged_commits.get(commit_id)

    def create_receipt_data(self, commit: MemoryCommit) -> dict[str, Any]:
        """
        Create receipt data for a commit.

        Args:
            commit: The commit

        Returns:
            Dictionary suitable for receipt creation
        """
        return {
            "commit_id": commit.commit_id,
            "created_by": commit.created_by,
            "message": commit.message,
            "status": commit.status.value,
            "edit_count": len(commit.edits),
            "has_core_edits": commit.has_core_edits(),
            "affected_targets": list(commit.get_affected_targets()),
            "parent_commit_id": commit.parent_commit_id,
            "rollback_of": commit.rollback_of,
        }
