"""Built-in skill: memory_cleanup - scheduled memory maintenance."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "memory_cleanup",
    "version": "1.0.0",
    "description": "Run memory subsystem cleanup and summarization maintenance",
    "risk": "LOW",
    "permissions": ["memory.read", "memory.write"],
    "inputs": [
        {
            "name": "dry_run",
            "type": "boolean",
            "required": False,
            "description": "Report work without mutating memory stores",
        }
    ],
}


def _memory_data_dir() -> Path:
    return Path(os.getenv("LANCELOT_DATA_DIR", "/home/lancelot/data"))


def execute(context: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run all memory maintenance jobs and return a compact scheduler result."""
    dry_run = bool((inputs or {}).get("dry_run", False))
    data_dir = _memory_data_dir()

    try:
        try:
            from memory.commits import CommitManager
            from memory.jobs import MemoryJobExecutor
            from memory.sqlite_store import MemoryStoreManager
            from memory.store import CoreBlockStore
        except ImportError:
            from src.core.memory.commits import CommitManager
            from src.core.memory.jobs import MemoryJobExecutor
            from src.core.memory.sqlite_store import MemoryStoreManager
            from src.core.memory.store import CoreBlockStore

        core_store = CoreBlockStore(data_dir=data_dir)
        core_store.initialize()
        store_manager = MemoryStoreManager(data_dir=data_dir)
        commit_manager = CommitManager(
            core_store=core_store,
            store_manager=store_manager,
            data_dir=data_dir,
        )
        executor = MemoryJobExecutor(
            core_store=core_store,
            store_manager=store_manager,
            commit_manager=commit_manager,
            data_dir=data_dir,
        )
        results = executor.run_all_maintenance(dry_run=dry_run)
        payload = {name: result.to_dict() for name, result in results.items()}
        failed = {
            name: result.errors
            for name, result in results.items()
            if not result.success
        }
        status = "error" if failed else "success"
        return {
            "status": status,
            "dry_run": dry_run,
            "jobs_run": len(results),
            "jobs_failed": failed,
            "items_processed": sum(result.items_processed for result in results.values()),
            "items_affected": sum(result.items_affected for result in results.values()),
            "results": payload,
        }
    except Exception as exc:
        logger.exception("memory_cleanup failed")
        return {
            "status": "error",
            "error": str(exc),
            "dry_run": dry_run,
        }
