"""
Structured Memory Promotion Policy.

Promotion is any move from short-lived context into a tier that can influence
future behavior. These rules keep that move explicit, auditable, and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import SECRET_PATTERNS
from .schemas import MemoryItem, MemoryStatus, MemoryTier, ProvenanceType


ARCHIVAL_CONFIDENCE_FLOOR = 0.6
EPISODIC_CONFIDENCE_FLOOR = 0.4
TRANSIENT_TAGS = {
    "active_objective",
    "scratch",
    "todo",
    "transient",
    "draft",
}
VERIFIABLE_PROVENANCE = {
    ProvenanceType.user_message,
    ProvenanceType.receipt,
    ProvenanceType.external_doc,
    ProvenanceType.system,
}


@dataclass
class PromotionDecision:
    """Result of evaluating whether a memory item can move tiers."""

    allowed: bool
    target_tier: MemoryTier
    suggested_status: MemoryStatus
    reason: str
    requires_approval: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "target_tier": self.target_tier.value,
            "suggested_status": self.suggested_status.value,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "warnings": list(self.warnings),
        }


def evaluate_promotion(
    item: MemoryItem,
    target_tier: MemoryTier,
    *,
    operator_approved: bool = False,
) -> PromotionDecision:
    """Evaluate deterministic tier-promotion rules for a memory item."""
    if target_tier == MemoryTier.core:
        return PromotionDecision(
            allowed=False,
            target_tier=target_tier,
            suggested_status=MemoryStatus.quarantined,
            reason="Core memory is promoted only through governed core-block edits",
            requires_approval=True,
        )

    if _contains_secret_material(item):
        return PromotionDecision(
            allowed=False,
            target_tier=target_tier,
            suggested_status=MemoryStatus.quarantined,
            reason="Promotion blocked because candidate content matched secret patterns",
            requires_approval=True,
        )

    if target_tier == MemoryTier.working:
        warnings = []
        if item.expires_at is None:
            warnings.append("Working memory should have an expiry")
        return PromotionDecision(
            allowed=True,
            target_tier=target_tier,
            suggested_status=MemoryStatus.active,
            reason="Working-memory candidate stays task scoped",
            warnings=warnings,
        )

    if not item.provenance:
        return PromotionDecision(
            allowed=False,
            target_tier=target_tier,
            suggested_status=MemoryStatus.quarantined,
            reason="Promotion requires provenance",
            requires_approval=True,
        )

    if target_tier == MemoryTier.episodic:
        if item.confidence < EPISODIC_CONFIDENCE_FLOOR:
            return PromotionDecision(
                allowed=False,
                target_tier=target_tier,
                suggested_status=MemoryStatus.staged,
                reason=(
                    f"Confidence {item.confidence:.2f} below episodic floor "
                    f"{EPISODIC_CONFIDENCE_FLOOR:.2f}"
                ),
            )
        return PromotionDecision(
            allowed=True,
            target_tier=target_tier,
            suggested_status=MemoryStatus.active,
            reason="Episodic candidate has provenance and sufficient confidence",
        )

    if target_tier == MemoryTier.archival:
        return _evaluate_archival_promotion(item, operator_approved=operator_approved)

    return PromotionDecision(
        allowed=False,
        target_tier=target_tier,
        suggested_status=MemoryStatus.staged,
        reason=f"Unsupported promotion target tier: {target_tier.value}",
    )


def _evaluate_archival_promotion(
    item: MemoryItem,
    *,
    operator_approved: bool,
) -> PromotionDecision:
    tags = {tag.strip().lower() for tag in item.tags if tag}
    if tags & TRANSIENT_TAGS:
        return PromotionDecision(
            allowed=False,
            target_tier=MemoryTier.archival,
            suggested_status=MemoryStatus.staged,
            reason="Transient working context must be summarized before archival promotion",
        )

    if item.tier == MemoryTier.working and "summary" not in tags:
        return PromotionDecision(
            allowed=False,
            target_tier=MemoryTier.archival,
            suggested_status=MemoryStatus.staged,
            reason="Raw working memory cannot be promoted directly to archival",
        )

    if item.confidence < ARCHIVAL_CONFIDENCE_FLOOR:
        return PromotionDecision(
            allowed=False,
            target_tier=MemoryTier.archival,
            suggested_status=MemoryStatus.staged,
            reason=(
                f"Confidence {item.confidence:.2f} below archival promotion floor "
                f"{ARCHIVAL_CONFIDENCE_FLOOR:.2f}"
            ),
        )

    provenance_types = {p.type for p in item.provenance}
    if not provenance_types & VERIFIABLE_PROVENANCE:
        return PromotionDecision(
            allowed=True,
            target_tier=MemoryTier.archival,
            suggested_status=MemoryStatus.active if operator_approved else MemoryStatus.quarantined,
            reason="Archival promotion from inference-only provenance requires operator approval",
            requires_approval=not operator_approved,
        )

    return PromotionDecision(
        allowed=True,
        target_tier=MemoryTier.archival,
        suggested_status=MemoryStatus.active,
        reason="Archival candidate has verifiable provenance and sufficient confidence",
    )


def _contains_secret_material(item: MemoryItem) -> bool:
    content = "\n".join([item.title or "", item.content or ""])
    return any(re.search(pattern, content, flags=re.IGNORECASE) for pattern in SECRET_PATTERNS)
