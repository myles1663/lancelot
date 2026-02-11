"""
Lancelot vNext4: Rollback Snapshot System

Creates pre-execution snapshots for T1 actions and provides
rollback callables for the AsyncVerificationQueue.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RollbackSnapshot:
    """Pre-execution state capture for a T1 action."""
    snapshot_id: str
    task_id: str
    step_index: int
    capability: str
    created_at: str
    snapshot_data: dict = field(default_factory=dict)
    rolled_back: bool = False
    rolled_back_at: Optional[str] = None


class RollbackManager:
    """Creates and manages pre-execution snapshots for T1 actions.

    Provides rollback_action callables that can be passed to
    VerificationJob for automatic rollback on verification failure.
    """

    def __init__(self, workspace: str = "", data_dir: str = "lancelot_data"):
        self._workspace = workspace
        self._data_dir = data_dir
        self._snapshots: dict[str, RollbackSnapshot] = {}

    def create_snapshot(
        self,
        task_id: str,
        step_index: int,
        capability: str,
        target: str = "",
        **kwargs: Any,
    ) -> RollbackSnapshot:
        """Create a pre-execution snapshot based on capability type.

        For fs.write: Captures current file content (or notes file doesn't exist)
        For git.commit: Notes that rollback uses git revert
        For memory.write: Notes that rollback uses CommitManager
        For other T1 capabilities: Stores generic kwargs
        """
        snapshot_id = str(uuid.uuid4())
        snapshot_data: dict[str, Any] = {}

        if capability == "fs.write":
            file_path = os.path.join(self._workspace, target) if target else ""
            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        snapshot_data = {
                            "file_existed": True,
                            "content": f.read(),
                            "path": file_path,
                        }
                except Exception as e:
                    snapshot_data = {
                        "file_existed": True,
                        "content": None,
                        "path": file_path,
                        "read_error": str(e),
                    }
            else:
                snapshot_data = {"file_existed": False, "path": file_path}

        elif capability == "git.commit":
            snapshot_data = {"note": "git rollback via git revert", **kwargs}

        elif capability == "memory.write":
            snapshot_data = {"note": "memory rollback via CommitManager", **kwargs}

        else:
            snapshot_data = dict(kwargs)

        snapshot = RollbackSnapshot(
            snapshot_id=snapshot_id,
            task_id=task_id,
            step_index=step_index,
            capability=capability,
            created_at=datetime.now(timezone.utc).isoformat(),
            snapshot_data=snapshot_data,
        )
        self._snapshots[snapshot_id] = snapshot
        return snapshot

    def get_rollback_action(self, snapshot_id: str) -> Callable:
        """Return a callable that rolls back the given snapshot.

        This callable is passed to VerificationJob.rollback_action.
        """
        def rollback():
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None or snapshot.rolled_back:
                return

            if snapshot.capability == "fs.write":
                path = snapshot.snapshot_data.get("path", "")
                if snapshot.snapshot_data.get("file_existed"):
                    content = snapshot.snapshot_data.get("content")
                    if content is not None and path:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(content)
                        logger.info("Rolled back fs.write: restored %s", path)
                elif path and os.path.exists(path):
                    os.remove(path)
                    logger.info("Rolled back fs.write: removed new file %s", path)

            # Mark as rolled back
            snapshot.rolled_back = True
            snapshot.rolled_back_at = datetime.now(timezone.utc).isoformat()

        return rollback

    def get_snapshot(self, snapshot_id: str) -> Optional[RollbackSnapshot]:
        """Retrieve a snapshot by ID."""
        return self._snapshots.get(snapshot_id)

    @property
    def active_snapshots(self) -> list[RollbackSnapshot]:
        """Snapshots that have not been rolled back."""
        return [s for s in self._snapshots.values() if not s.rolled_back]
