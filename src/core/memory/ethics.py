"""Deterministic memory ethics enforcement."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import SECRET_PATTERNS
from .schemas import MemoryItem, MemoryStatus, MemoryTier


class MemoryEthicsAction(str, Enum):
    """Possible outcomes from memory ethics evaluation."""

    allow = "allow"
    scrub = "scrub"
    quarantine = "quarantine"
    exclude = "exclude"


@dataclass
class MemoryEthicsDecision:
    """Result of evaluating a memory item against deterministic ethics rules."""

    action: MemoryEthicsAction = MemoryEthicsAction.allow
    rule_name: str = ""
    reason: str = ""
    scrubbed_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """Whether the item can remain in the memory store."""
        return self.action in {MemoryEthicsAction.allow, MemoryEthicsAction.scrub, MemoryEthicsAction.quarantine}


class MemoryEthicsEvaluator:
    """Evaluate memory reads and writes against the base Soul memory ethics."""

    _EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    _PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")
    _SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _SOUL_MARKERS = (
        "autonomy_posture:",
        "risk_rules:",
        "approval_rules:",
        "memory_ethics:",
        "tone_invariants:",
        "scheduling_boundaries:",
    )

    def evaluate_write(self, item: MemoryItem) -> MemoryEthicsDecision:
        """Evaluate a memory item before storage."""
        soul_decision = self._evaluate_soul_content(item)
        if soul_decision.action != MemoryEthicsAction.allow:
            return soul_decision

        secret_decision = self._evaluate_secret_redaction(item.content)
        if secret_decision.action == MemoryEthicsAction.scrub:
            return secret_decision

        pii_decision = self._evaluate_pii(item)
        if pii_decision.action != MemoryEthicsAction.allow:
            return pii_decision

        return MemoryEthicsDecision()

    def evaluate_retrieval(self, item: MemoryItem) -> MemoryEthicsDecision:
        """Evaluate a memory item before rendering it into LLM context."""
        if (item.metadata or {}).get("ethics_exclude_from_context") is True:
            return MemoryEthicsDecision(
                action=MemoryEthicsAction.exclude,
                rule_name="ethics_exclude_from_context",
                reason="Memory item is marked as excluded from context by ethics policy",
            )
        return self.evaluate_write(item)

    def apply_write_decision(self, item: MemoryItem, decision: MemoryEthicsDecision) -> MemoryItem:
        """Apply a write decision to an item before persistence."""
        if decision.action == MemoryEthicsAction.scrub and decision.scrubbed_content is not None:
            item.content = decision.scrubbed_content
        if decision.action == MemoryEthicsAction.quarantine:
            item.status = MemoryStatus.quarantined
        if decision.rule_name:
            metadata = dict(item.metadata or {})
            metadata["ethics_rule"] = decision.rule_name
            metadata["ethics_reason"] = decision.reason
            metadata.update(decision.metadata)
            item.metadata = metadata
        return item

    def _evaluate_pii(self, item: MemoryItem) -> MemoryEthicsDecision:
        if item.tier not in {MemoryTier.episodic, MemoryTier.archival}:
            return MemoryEthicsDecision()
        if self._has_consent_marker(item):
            return MemoryEthicsDecision()
        content = item.content or ""
        if self._EMAIL_RE.search(content) or self._PHONE_RE.search(content) or self._SSN_RE.search(content):
            return MemoryEthicsDecision(
                action=MemoryEthicsAction.quarantine,
                rule_name="pii_requires_consent",
                reason="Long-term memory containing PII requires explicit consent",
                metadata={"flagged_reason": "memory_ethics"},
            )
        return MemoryEthicsDecision()

    def _evaluate_secret_redaction(self, content: str) -> MemoryEthicsDecision:
        scrubbed = content or ""
        matched = False
        for pattern in SECRET_PATTERNS:
            scrubbed, count = re.subn(pattern, "[REDACTED_SECRET]", scrubbed, flags=re.IGNORECASE)
            matched = matched or count > 0
        if not matched:
            return MemoryEthicsDecision()
        return MemoryEthicsDecision(
            action=MemoryEthicsAction.scrub,
            rule_name="secret_redact",
            reason="Sensitive data was redacted before memory persistence",
            scrubbed_content=scrubbed,
        )

    def _evaluate_soul_content(self, item: MemoryItem) -> MemoryEthicsDecision:
        metadata = item.metadata or {}
        tags = {str(tag).lower() for tag in item.tags or []}
        source_kind = str(metadata.get("source_kind") or metadata.get("source") or "").lower()
        content = (item.content or "").lower()
        marker_hits = sum(1 for marker in self._SOUL_MARKERS if marker in content)
        if "soul" in tags or source_kind == "soul" or marker_hits >= 2:
            return MemoryEthicsDecision(
                action=MemoryEthicsAction.quarantine,
                rule_name="soul_content_excluded_from_memory",
                reason="Soul content is not stored recursively in memory",
                metadata={
                    "flagged_reason": "memory_ethics",
                    "ethics_exclude_from_context": True,
                },
            )
        return MemoryEthicsDecision()

    @staticmethod
    def _has_consent_marker(item: MemoryItem) -> bool:
        metadata = item.metadata or {}
        tags = {str(tag).lower() for tag in item.tags or []}
        return (
            metadata.get("consent_marker") is True
            or metadata.get("pii_consent") is True
            or "consent" in tags
            or "pii-consent" in tags
        )
