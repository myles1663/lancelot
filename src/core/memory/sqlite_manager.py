"""Cross-tier SQLite memory search ranking helpers."""

from __future__ import annotations

from typing import Any, Optional
import re

from .schemas import MemoryItem, MemoryTier


class MemoryStoreManagerSearchMixin:
    @staticmethod
    def _result_signature(item: MemoryItem) -> str:
        text = " ".join(f"{item.title} {item.content}".lower().split())
        return text[:600]

    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        return set(re.findall(r"[A-Za-z0-9_]{2,}", str(query or "").lower()))

    @staticmethod
    def _metadata_values(metadata: dict[str, Any], *keys: str) -> set[str]:
        values: set[str] = set()
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, list):
                values.update(str(entry) for entry in value if entry not in (None, ""))
            elif value not in (None, ""):
                values.add(str(value))
        return values

    def _scope_boost(
        self,
        item: MemoryItem,
        *,
        current_quest_id: str = "",
        operator_id: str = "",
        workflow_id: str = "",
    ) -> float:
        metadata = item.metadata or {}
        score = 0.0
        if current_quest_id:
            quest_values = self._metadata_values(metadata, "quest_id", "quest_ids")
            if item.namespace == f"quest:{current_quest_id}" or current_quest_id in quest_values:
                score += 4.0
        if operator_id:
            operator_values = self._metadata_values(metadata, "operator_id", "operator_ids")
            if item.namespace == f"operator:{operator_id}" or operator_id in operator_values:
                score += 2.5
        if workflow_id:
            workflow_values = self._metadata_values(metadata, "workflow_id", "workflow", "template_id")
            if item.namespace == f"workflow:{workflow_id}" or workflow_id in workflow_values:
                score += 1.5
        if item.namespace == "global":
            score += 0.25
        return score

    @staticmethod
    def _evidence_boost(item: MemoryItem, query_tokens: set[str]) -> float:
        tags = {str(tag).lower() for tag in item.tags}
        score = min(len(tags & query_tokens), 5) * 0.15
        if tags & {"receipt", "receipts", "work_ledger", "session_brief", "task_experience"}:
            score += 0.5
        metadata = item.metadata or {}
        if metadata.get("receipt_ids") or metadata.get("last_receipt_id"):
            score += 0.35
        return score

    def search_all(
        self,
        query: str,
        tiers: Optional[list[MemoryTier]] = None,
        namespace: Optional[str] = None,
        limit: int = 20,
        total_limit: Optional[int] = None,
        current_quest_id: str = "",
        operator_id: str = "",
        workflow_id: str = "",
        include_blobs: bool = False,
    ) -> list[MemoryItem]:
        """
        Search across multiple tiers.

        Args:
            query: Search query
            tiers: Tiers to search (default: all non-core)
            namespace: Filter by namespace
            limit: Maximum results per tier
            total_limit: Optional maximum results after cross-tier dedupe/ranking
            include_blobs: Whether to include full-source audit blobs

        Returns:
            Combined list of matching items
        """
        if tiers is None:
            tiers = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]

        tier_weight = {
            MemoryTier.working: 3.0,
            MemoryTier.episodic: 2.0,
            MemoryTier.archival: 1.0,
        }
        query_tokens = self._query_tokens(query)
        ranked: dict[str, tuple[float, MemoryItem]] = {}
        candidate_limit = max(int(limit), int(total_limit or 0), 10)
        for tier in tiers:
            if tier == MemoryTier.core:
                continue
            store = self.get_store(tier)
            candidates = [
                *store.search_by_entities(
                    query,
                    namespace=namespace,
                    limit=candidate_limit,
                    include_blobs=include_blobs,
                ),
                *store.search(
                    query,
                    namespace=namespace,
                    limit=candidate_limit,
                    include_blobs=include_blobs,
                ),
            ]
            for position, item in enumerate(candidates):
                signature = self._result_signature(item) or item.id
                score = (
                    tier_weight.get(item.tier, 0.0)
                    + item.confidence
                    + self._scope_boost(
                        item,
                        current_quest_id=current_quest_id,
                        operator_id=operator_id,
                        workflow_id=workflow_id,
                    )
                    + self._evidence_boost(item, query_tokens)
                    - (position * 0.01)
                )
                existing = ranked.get(signature)
                if existing is None or score > existing[0]:
                    ranked[signature] = (score, item)

        ordered = [
            item
            for _, item in sorted(
                ranked.values(),
                key=lambda pair: (
                    pair[0],
                    pair[1].updated_at,
                ),
                reverse=True,
            )
        ]
        max_results = total_limit if total_limit is not None else limit * max(1, len(tiers))
        return ordered[: max(1, int(max_results))]
