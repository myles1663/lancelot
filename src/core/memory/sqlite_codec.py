"""Row serialization helpers for SQLite-backed memory items."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .schemas import MemoryItem, MemoryStatus, MemoryTier, Provenance


def item_to_row(item: MemoryItem) -> dict[str, Any]:
    """Convert a MemoryItem to a database row."""
    return {
        "id": item.id,
        "tier": item.tier.value,
        "namespace": item.namespace,
        "title": item.title,
        "content": item.content,
        "tags": json.dumps(item.tags),
        "confidence": item.confidence,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "last_retrieved_at": item.last_retrieved_at.isoformat() if item.last_retrieved_at else None,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "decay_half_life_days": item.decay_half_life_days,
        "provenance": json.dumps([p.model_dump(mode="json") for p in item.provenance]),
        "status": item.status.value,
        "token_count": item.token_count,
        "metadata": json.dumps(item.metadata),
    }


def row_to_item(row: sqlite3.Row) -> MemoryItem:
    """Convert a database row to a MemoryItem."""
    return MemoryItem(
        id=row["id"],
        tier=MemoryTier(row["tier"]),
        namespace=row["namespace"],
        title=row["title"],
        content=row["content"],
        tags=json.loads(row["tags"]),
        confidence=row["confidence"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_retrieved_at=(
            datetime.fromisoformat(row["last_retrieved_at"])
            if "last_retrieved_at" in row.keys() and row["last_retrieved_at"]
            else None
        ),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        decay_half_life_days=row["decay_half_life_days"],
        provenance=[Provenance.model_validate(p) for p in json.loads(row["provenance"])],
        status=MemoryStatus(row["status"]),
        token_count=row["token_count"],
        metadata=json.loads(row["metadata"]),
    )
