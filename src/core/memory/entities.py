"""Rule-based entity extraction for structured memory retrieval."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryEntity:
    """A normalized entity reference extracted from a memory item or query."""

    entity_type: str
    entity_value: str
    entity_normalized: str
    confidence: float = 0.8


_FILE_PATTERN = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/])?(?:[\w.-]+[\\/])*[\w.-]+\.(?:py|ts|tsx|js|jsx|md|json|ya?ml|toml|txt|db|sqlite|png|jpg|jpeg|csv|xlsx|docx)(?!\w)",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_PROJECT_PATTERN = re.compile(
    r"\b(?:project|quest|workflow|initiative|customer|client|system|repo|repository)\s*[:#-]?\s+"
    r"([A-Za-z][\w.-]*(?:[- ][A-Za-z][\w.-]*){0,2})"
    r"(?=\s*(?:$|[,.?;:]|\bin\b|\bon\b|\bat\b|\bfor\b|\bwith\b))",
    re.IGNORECASE,
)
_CAPITALIZED_PHRASE_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:[- ][A-Z][A-Za-z0-9]*){0,4})\b"
)

_STOP_PHRASES = {
    "The",
    "This",
    "That",
    "Use",
    "User",
    "Memory",
    "Summary",
    "Source Blob",
    "Content",
    "Active Objective",
}


def normalize_entity(value: str) -> str:
    """Normalize entity values for case- and accent-insensitive matching."""
    cleaned = unicodedata.normalize("NFKD", str(value or ""))
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    cleaned = re.sub(r"[_\s]+", " ", cleaned.replace("\\", "/")).strip().lower()
    return cleaned


def extract_entities_from_item_fields(
    *,
    title: str = "",
    content: str = "",
    namespace: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[MemoryEntity]:
    """Extract entities from memory item fields."""
    metadata = metadata or {}
    tags = tags or []
    text = "\n".join([title or "", content or "", namespace or "", " ".join(tags)])
    entities: dict[tuple[str, str], MemoryEntity] = {}

    def add(entity_type: str, value: str, confidence: float = 0.8) -> None:
        normalized = normalize_entity(value)
        if not normalized or len(normalized) < 2:
            return
        key = (entity_type, normalized)
        entities[key] = MemoryEntity(entity_type, value.strip(), normalized, confidence)

    for match in _FILE_PATTERN.finditer(text):
        add("file", match.group(0), 0.95)

    for match in _DATE_PATTERN.finditer(text):
        add("date", match.group(0), 0.85)

    for match in _PROJECT_PATTERN.finditer(text):
        add("project", match.group(1), 0.85)

    if namespace and ":" in namespace:
        prefix, value = namespace.split(":", 1)
        if prefix in {"project", "quest", "workflow", "operator", "customer", "client"}:
            add("project" if prefix in {"project", "quest", "workflow"} else prefix, value, 0.9)

    for key in ("project", "project_id", "quest_id", "workflow_id", "customer", "client", "operator_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            entity_type = "project" if key in {"project", "project_id", "quest_id", "workflow_id"} else key.replace("_id", "")
            add(entity_type, value, 0.9)

    for tag in tags:
        if ":" in tag:
            prefix, value = tag.split(":", 1)
            if prefix in {"project", "quest", "workflow", "customer", "client", "operator"}:
                add("project" if prefix in {"project", "quest", "workflow"} else prefix, value, 0.85)

    for match in _CAPITALIZED_PHRASE_PATTERN.finditer(text):
        value = match.group(1).strip()
        if value in _STOP_PHRASES or len(value) < 3:
            continue
        entity_type = "org" if re.search(r"\b(?:Inc|LLC|Corp|Company|Systems|Labs)\b", value) else "person"
        add(entity_type, value, 0.7)

    return sorted(entities.values(), key=lambda e: (e.entity_type, e.entity_normalized))


def extract_entities(text: str) -> list[MemoryEntity]:
    """Extract entities from an ad hoc query or text block."""
    return extract_entities_from_item_fields(content=text)
