"""Structured factual claims and contradiction handling for memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .entities import normalize_entity
from .schemas import MemoryItem


@dataclass(frozen=True)
class MemoryClaim:
    """Entity-attribute-value claim extracted from a memory item."""

    entity_normalized: str
    attribute: str
    value: str
    valid_from: datetime


_CLAIM_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9_. -]{1,80}?)\s+([a-z][a-z0-9_ -]{1,40})\s+(?:is|=)\s+([^.\n]{1,160})",
    re.IGNORECASE,
)


def _claim_from_dict(data: dict[str, Any], fallback_time: datetime) -> MemoryClaim | None:
    entity = str(data.get("entity") or data.get("entity_normalized") or "").strip()
    attribute = str(data.get("attribute") or "").strip().lower().replace(" ", "_")
    value = str(data.get("value") or "").strip()
    if not entity or not attribute or not value:
        return None
    valid_from_raw = data.get("valid_from")
    try:
        valid_from = datetime.fromisoformat(str(valid_from_raw)) if valid_from_raw else fallback_time
    except ValueError:
        valid_from = fallback_time
    return MemoryClaim(
        entity_normalized=normalize_entity(entity),
        attribute=attribute,
        value=value,
        valid_from=valid_from,
    )


def extract_claims(item: MemoryItem) -> list[MemoryClaim]:
    """Extract explicit and simple textual factual claims from a memory item."""
    claims: dict[tuple[str, str], MemoryClaim] = {}
    metadata = item.metadata or {}

    explicit = metadata.get("claims")
    if isinstance(explicit, dict):
        explicit = [explicit]
    if isinstance(explicit, list):
        for entry in explicit:
            if isinstance(entry, dict):
                claim = _claim_from_dict(entry, item.created_at)
                if claim is not None:
                    claims[(claim.entity_normalized, claim.attribute)] = claim

    single = metadata.get("claim")
    if isinstance(single, dict):
        claim = _claim_from_dict(single, item.created_at)
        if claim is not None:
            claims[(claim.entity_normalized, claim.attribute)] = claim

    if metadata.get("claim_extraction") == "text":
        for match in _CLAIM_PATTERN.finditer(item.content or ""):
            entity = normalize_entity(match.group(1))
            attribute = match.group(2).strip().lower().replace(" ", "_")
            value = match.group(3).strip()
            if entity and attribute and value:
                claims[(entity, attribute)] = MemoryClaim(
                    entity_normalized=entity,
                    attribute=attribute,
                    value=value,
                    valid_from=item.created_at,
                )

    return sorted(claims.values(), key=lambda c: (c.entity_normalized, c.attribute))
