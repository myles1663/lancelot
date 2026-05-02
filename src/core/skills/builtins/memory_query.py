"""Built-in skill: memory_query - governed structured memory retrieval."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "memory_query",
    "version": "1.0.0",
    "description": "Search Lancelot structured memory for relevant prior context",
    "risk": "LOW",
    "permissions": ["memory.read"],
    "inputs": [
        {"name": "query", "type": "string", "required": True, "description": "Natural-language memory query"},
        {"name": "tiers", "type": "array", "required": False, "description": "Memory tiers to search"},
        {"name": "limit", "type": "integer", "required": False, "description": "Maximum results"},
        {"name": "include_blobs", "type": "boolean", "required": False, "description": "Include audit source blobs"},
    ],
}


def _memory_data_dir() -> Path:
    return Path(os.getenv("LANCELOT_DATA_DIR", "/home/lancelot/data"))


def execute(context: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Search structured memory and return compact, ranked results."""
    payload = inputs or {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"status": "error", "error": "query is required", "results": []}

    try:
        try:
            from memory.schemas import MemoryTier
            from memory.sqlite_store import MemoryStoreManager
        except ImportError:
            from src.core.memory.schemas import MemoryTier
            from src.core.memory.sqlite_store import MemoryStoreManager

        tier_values = payload.get("tiers") or ["working", "episodic", "archival"]
        tiers = [MemoryTier(str(tier)) for tier in tier_values if str(tier) != "core"]
        limit = max(1, min(int(payload.get("limit") or 10), 50))
        manager = MemoryStoreManager(data_dir=_memory_data_dir())
        results = manager.search_all(
            query=query,
            tiers=tiers,
            limit=limit,
            total_limit=limit,
            include_blobs=bool(payload.get("include_blobs", False)),
        )
        return {
            "status": "success",
            "query": query,
            "results": [
                {
                    "id": item.id,
                    "tier": item.tier.value,
                    "namespace": item.namespace,
                    "title": item.title,
                    "content": item.content[:500],
                    "confidence": item.confidence,
                    "tags": item.tags,
                }
                for item in results
            ],
            "total_count": len(results),
        }
    except Exception as exc:
        logger.exception("memory_query failed")
        return {"status": "error", "error": str(exc), "results": []}
