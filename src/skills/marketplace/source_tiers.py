"""
Marketplace Source Tiers — security policy based on skill origin.

First-party skills get lighter review; community skills get stricter scrutiny.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceTier(str, Enum):
    FIRST_PARTY = "first-party"
    COMMUNITY = "community"
    USER = "user"


@dataclass(frozen=True)
class SourceTierPolicy:
    """Security policy for a given source tier."""
    tier: SourceTier
    static_analysis_required: bool
    sandbox_test_required: bool
    community_review_required: bool
    auto_approve_read_ops: bool
    trust_graduation_enabled: bool


SOURCE_TIER_POLICIES = {
    SourceTier.FIRST_PARTY: SourceTierPolicy(
        tier=SourceTier.FIRST_PARTY,
        static_analysis_required=True,
        sandbox_test_required=True,
        community_review_required=False,
        auto_approve_read_ops=True,
        trust_graduation_enabled=True,
    ),
    SourceTier.COMMUNITY: SourceTierPolicy(
        tier=SourceTier.COMMUNITY,
        static_analysis_required=True,
        sandbox_test_required=True,
        community_review_required=True,
        auto_approve_read_ops=False,
        trust_graduation_enabled=True,
    ),
    SourceTier.USER: SourceTierPolicy(
        tier=SourceTier.USER,
        static_analysis_required=True,
        sandbox_test_required=True,
        community_review_required=False,
        auto_approve_read_ops=False,
        trust_graduation_enabled=True,
    ),
}


def get_policy(source: str) -> SourceTierPolicy:
    """Get the security policy for a given source string."""
    try:
        tier = SourceTier(source)
    except ValueError:
        raise ValueError(f"Unknown source tier: '{source}'")
    return SOURCE_TIER_POLICIES[tier]
