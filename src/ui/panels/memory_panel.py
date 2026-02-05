"""
War Room — Memory Panel.

Provides visibility into the Memory vNext subsystem:
- Core block status and token usage
- Memory tier statistics
- Quarantine queue
- Recent commits
- Search and debug tools
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.feature_flags import FEATURE_MEMORY_VNEXT

logger = logging.getLogger(__name__)


class MemoryPanel:
    """
    Memory panel data provider for the War Room.

    Provides methods to:
    - View core block status
    - View memory tier statistics
    - View quarantine queue
    - View commit history
    - Approve/reject quarantined items
    - Search memory
    """

    def __init__(
        self,
        core_store: Any = None,
        store_manager: Any = None,
        commit_manager: Any = None,
        quarantine_manager: Any = None,
        memory_index: Any = None,
    ):
        """
        Initialize the memory panel.

        Args:
            core_store: CoreBlockStore instance
            store_manager: MemoryStoreManager instance
            commit_manager: CommitManager instance
            quarantine_manager: QuarantineManager instance
            memory_index: MemoryIndex instance
        """
        self._core_store = core_store
        self._store_manager = store_manager
        self._commit_manager = commit_manager
        self._quarantine_manager = quarantine_manager
        self._memory_index = memory_index
        self._enabled = FEATURE_MEMORY_VNEXT

    @property
    def is_enabled(self) -> bool:
        """Check if memory panel is enabled."""
        return self._enabled and self._core_store is not None

    def get_core_blocks_summary(self) -> List[Dict[str, Any]]:
        """
        Get summary of all core memory blocks.

        Returns:
            List of block summaries with status and token info
        """
        if not self.is_enabled:
            return []

        try:
            blocks = self._core_store.get_all_blocks()
            summaries = []

            for block_type, block in blocks.items():
                utilization = (block.token_count / block.token_budget * 100) if block.token_budget > 0 else 0
                summaries.append({
                    "block_type": block_type,
                    "status": block.status.value,
                    "token_count": block.token_count,
                    "token_budget": block.token_budget,
                    "utilization_pct": round(utilization, 1),
                    "version": block.version,
                    "updated_at": block.updated_at.isoformat(),
                    "updated_by": block.updated_by,
                    "confidence": block.confidence,
                    "content_preview": block.content[:100] + "..." if len(block.content) > 100 else block.content,
                })

            return summaries

        except Exception as exc:
            logger.warning("Failed to get core blocks summary: %s", exc)
            return []

    def get_tier_statistics(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for each memory tier.

        Returns:
            Dict with tier stats (counts, token totals, etc.)
        """
        if not self.is_enabled or self._store_manager is None:
            return {}

        try:
            from src.core.memory.schemas import MemoryTier, MemoryStatus

            stats = {}
            for tier in [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]:
                store = self._store_manager.get_store(tier)

                # Get counts by status
                active_count = store.count(status=MemoryStatus.active)
                quarantined_count = store.count(status=MemoryStatus.quarantined)
                staged_count = store.count(status=MemoryStatus.staged)
                total_count = store.count()

                stats[tier.value] = {
                    "total_items": total_count,
                    "active": active_count,
                    "quarantined": quarantined_count,
                    "staged": staged_count,
                    "tier": tier.value,
                }

            return stats

        except Exception as exc:
            logger.warning("Failed to get tier statistics: %s", exc)
            return {}

    def get_quarantine_queue(self) -> Dict[str, Any]:
        """
        Get items currently in quarantine.

        Returns:
            Dict with quarantined core blocks and items
        """
        if not self.is_enabled or self._quarantine_manager is None:
            return {"core_blocks": [], "items": []}

        try:
            core_blocks = []
            for block_type, block in self._quarantine_manager.list_quarantined_core_blocks():
                core_blocks.append({
                    "id": f"core:{block_type.value}",
                    "block_type": block_type.value,
                    "content_preview": block.content[:200] if block.content else "",
                    "updated_at": block.updated_at.isoformat(),
                    "updated_by": block.updated_by,
                })

            items = []
            for item in self._quarantine_manager.list_quarantined_items():
                items.append({
                    "id": item.id,
                    "tier": item.tier.value,
                    "title": item.title,
                    "content_preview": item.content[:200] if item.content else "",
                    "namespace": item.namespace,
                    "created_at": item.created_at.isoformat(),
                    "confidence": item.confidence,
                })

            return {
                "core_blocks": core_blocks,
                "items": items,
                "total_count": len(core_blocks) + len(items),
            }

        except Exception as exc:
            logger.warning("Failed to get quarantine queue: %s", exc)
            return {"core_blocks": [], "items": [], "total_count": 0}

    def get_recent_commits(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent memory commits.

        Args:
            limit: Maximum commits to return

        Returns:
            List of recent commits with edit counts
        """
        if not self.is_enabled or self._commit_manager is None:
            return []

        try:
            commits = self._commit_manager.list_commits(limit=limit)
            return [
                {
                    "commit_id": c.commit_id,
                    "status": c.status.value,
                    "created_at": c.created_at.isoformat(),
                    "created_by": c.created_by,
                    "message": c.message,
                    "edit_count": len(c.edits),
                    "receipt_id": c.receipt_id,
                }
                for c in commits
            ]

        except Exception as exc:
            logger.warning("Failed to get recent commits: %s", exc)
            return []

    def approve_quarantined(self, item_id: str, approver: str) -> Dict[str, Any]:
        """
        Approve a quarantined item.

        Args:
            item_id: Item or block ID to approve
            approver: Who is approving

        Returns:
            Result dict with status
        """
        if not self.is_enabled or self._quarantine_manager is None:
            return {"error": "Memory panel not enabled"}

        try:
            if item_id.startswith("core:"):
                from src.core.memory.schemas import CoreBlockType
                block_type_str = item_id.replace("core:", "")
                block_type = CoreBlockType(block_type_str)
                result = self._quarantine_manager.approve_core_block(block_type, approver)
            else:
                # Try each tier to find the quarantined item
                result = False
                for tier_name in ["working", "episodic", "archival"]:
                    result = self._quarantine_manager.approve_item(item_id, tier_name, approver)
                    if result:
                        break

            if result:
                return {"status": "approved", "item_id": item_id, "approver": approver}
            else:
                return {"error": f"Failed to approve {item_id}"}

        except Exception as exc:
            return {"error": str(exc)}

    def reject_quarantined(self, item_id: str, rejector: str, reason: str = "") -> Dict[str, Any]:
        """
        Reject a quarantined item.

        Args:
            item_id: Item or block ID to reject
            rejector: Who is rejecting
            reason: Reason for rejection

        Returns:
            Result dict with status
        """
        if not self.is_enabled or self._quarantine_manager is None:
            return {"error": "Memory panel not enabled"}

        try:
            if item_id.startswith("core:"):
                from src.core.memory.schemas import CoreBlockType
                block_type_str = item_id.replace("core:", "")
                block_type = CoreBlockType(block_type_str)
                result = self._quarantine_manager.reject_core_block(block_type, rejector, reason)
            else:
                # Try each tier to find the quarantined item
                result = False
                for tier_name in ["working", "episodic", "archival"]:
                    result = self._quarantine_manager.reject_item(item_id, tier_name, rejector)
                    if result:
                        break

            if result:
                return {"status": "rejected", "item_id": item_id, "rejector": rejector}
            else:
                return {"error": f"Failed to reject {item_id}"}

        except Exception as exc:
            return {"error": str(exc)}

    def search_memory(
        self,
        query: str,
        tiers: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search across memory tiers.

        Args:
            query: Search query
            tiers: Tiers to search (default: all)
            limit: Maximum results

        Returns:
            List of search results
        """
        if not self.is_enabled or self._memory_index is None:
            return []

        try:
            from src.core.memory.schemas import MemoryTier

            tier_enums = None
            if tiers:
                tier_enums = [MemoryTier(t) for t in tiers]

            results = self._memory_index.search(
                query=query,
                tiers=tier_enums,
                limit=limit,
            )

            return [
                {
                    "id": r.item.id,
                    "tier": r.item.tier.value,
                    "title": r.item.title,
                    "content_preview": r.item.content[:200] if r.item.content else "",
                    "score": r.score,
                    "confidence": r.item.confidence,
                    "namespace": r.item.namespace,
                    "tags": r.item.tags,
                }
                for r in results
            ]

        except Exception as exc:
            logger.warning("Memory search failed: %s", exc)
            return []

    def get_budget_status(self) -> Dict[str, Any]:
        """
        Get token budget status across core blocks.

        Returns:
            Budget status with any issues
        """
        if not self.is_enabled:
            return {}

        try:
            total_tokens = self._core_store.total_tokens()
            budget_issues = self._core_store.validate_budgets()

            # Convert list of tuples to list of dicts for easier display
            issues_list = [
                {"block_type": block_type, "message": message}
                for block_type, message in budget_issues
            ]

            return {
                "total_core_tokens": total_tokens,
                "budget_issues": issues_list,
                "has_issues": len(issues_list) > 0,
            }

        except Exception as exc:
            logger.warning("Failed to get budget status: %s", exc)
            return {}

    def render_data(self) -> Dict[str, Any]:
        """
        Render all panel data for the War Room.

        Returns:
            Complete panel data dict
        """
        if not self.is_enabled:
            return {
                "panel": "memory",
                "enabled": False,
                "message": "Memory vNext is disabled. Set FEATURE_MEMORY_VNEXT=true to enable.",
            }

        return {
            "panel": "memory",
            "enabled": True,
            "core_blocks": self.get_core_blocks_summary(),
            "tier_stats": self.get_tier_statistics(),
            "quarantine": self.get_quarantine_queue(),
            "recent_commits": self.get_recent_commits(),
            "budget_status": self.get_budget_status(),
            "timestamp": datetime.utcnow().isoformat(),
        }
