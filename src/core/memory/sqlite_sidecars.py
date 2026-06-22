"""SQLite memory sidecar persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import sqlite3

from .claims import extract_claims
from .entities import extract_entities_from_item_fields
from .schemas import MemoryItem, MemoryStatus


class MemorySidecarMixin:
    def _replace_entities(self, cursor: sqlite3.Cursor, item: MemoryItem) -> None:
        """Rebuild sidecar entity rows for an item."""
        cursor.execute("DELETE FROM memory_entities WHERE item_id = ?", (item.id,))
        entities = extract_entities_from_item_fields(
            title=item.title,
            content=item.content,
            namespace=item.namespace,
            tags=item.tags,
            metadata=item.metadata,
        )
        cursor.executemany(
            """
            INSERT OR REPLACE INTO memory_entities (
                item_id, entity_type, entity_value, entity_normalized, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.id,
                    entity.entity_type,
                    entity.entity_value,
                    entity.entity_normalized,
                    entity.confidence,
                )
                for entity in entities
            ],
        )

    def _replace_claims(self, cursor: sqlite3.Cursor, item: MemoryItem) -> None:
        """Rebuild structured factual claims, quarantining contradictions until approval."""
        now = datetime.utcnow().isoformat()
        cursor.execute("DELETE FROM memory_claims WHERE item_id = ?", (item.id,))
        claims = extract_claims(item)
        pending_supersessions: list[dict[str, Any]] = []
        for claim in claims:
            cursor.execute(
                """
                SELECT item_id, value FROM memory_claims
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [claim.entity_normalized, claim.attribute, item.id, claim.value],
            )
            superseded_claims = [
                {"item_id": row["item_id"], "value": row["value"]}
                for row in cursor.fetchall()
            ]
            if superseded_claims:
                pending_supersessions.append({
                    "entity_normalized": claim.entity_normalized,
                    "attribute": claim.attribute,
                    "new_value": claim.value,
                    "superseded_count": len(superseded_claims),
                    "superseded_item_ids": [entry["item_id"] for entry in superseded_claims],
                    "superseded_claims": superseded_claims,
                })

        metadata = dict(item.metadata or {})
        if pending_supersessions and not metadata.get("claim_supersession_approved"):
            metadata["claim_supersession_pending"] = True
            metadata["flagged_reason"] = "claim_supersession"
            metadata["superseded_claims"] = pending_supersessions
            item.metadata = metadata
            item.status = MemoryStatus.quarantined
            return

        metadata.pop("claim_supersession_pending", None)
        if metadata.get("flagged_reason") == "claim_supersession":
            metadata.pop("flagged_reason", None)
        item.metadata = metadata

        for claim in claims:
            cursor.execute(
                """
                SELECT item_id, value FROM memory_claims
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [claim.entity_normalized, claim.attribute, item.id, claim.value],
            )
            superseded_claims = [
                {"item_id": row["item_id"], "value": row["value"]}
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                UPDATE memory_claims
                SET valid_until = ?, superseded_by = ?
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [
                    now,
                    item.id,
                    claim.entity_normalized,
                    claim.attribute,
                    item.id,
                    claim.value,
                ],
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO memory_claims (
                    item_id, entity_normalized, attribute, value,
                    valid_from, valid_until, superseded_by
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
                """,
                [
                    item.id,
                    claim.entity_normalized,
                    claim.attribute,
                    claim.value,
                    claim.valid_from.isoformat(),
                ],
            )
            if superseded_claims:
                metadata = dict(item.metadata or {})
                metadata.setdefault("superseded_claims", [])
                metadata["superseded_claims"].append({
                    "entity_normalized": claim.entity_normalized,
                    "attribute": claim.attribute,
                    "new_value": claim.value,
                    "superseded_count": len(superseded_claims),
                    "superseded_item_ids": [entry["item_id"] for entry in superseded_claims],
                })
                item.metadata = metadata
                self._receipt_emitter.emit(
                    action_type="memory_claim_supersede",
                    action_name="memory_claim_contradiction",
                    inputs={
                        "item_id": item.id,
                        "tier": item.tier.value,
                        "entity_normalized": claim.entity_normalized,
                        "attribute": claim.attribute,
                        "value": claim.value,
                    },
                    outputs={
                        "superseded_claims": superseded_claims,
                        "superseded_by": item.id,
                    },
                )

    def _emit_ethics_receipt(self, item: MemoryItem, decision: Any, *, operation: str) -> None:
        """Emit a receipt for ethics decisions that changed memory behavior."""
        if not getattr(decision, "rule_name", ""):
            return
        self._receipt_emitter.emit(
            action_type="memory_ethics_evaluation",
            action_name=f"memory_{operation}_ethics",
            inputs={
                "item_id": item.id,
                "tier": item.tier.value,
                "operation": operation,
            },
            outputs={
                "item_id": item.id,
                "tier": item.tier.value,
                "rule_name": decision.rule_name,
                "action": decision.action.value,
                "reason": decision.reason,
            },
        )
