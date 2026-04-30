"""
Trust Ledger Data Models and Configuration.

Provides data structures for progressive tier relaxation:
- TrustRecord tracks per-capability success/failure history
- GraduationProposal/Event track tier transitions
- Pydantic config models for trust_graduation.yaml
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel

from src.core.governance.models import RiskTier

logger = logging.getLogger(__name__)


# ── Graduation Event ─────────────────────────────────────────────

@dataclass
class GraduationEvent:
    """Records a single tier transition (up or down)."""
    timestamp: str
    from_tier: RiskTier
    to_tier: RiskTier
    trigger: str  # "threshold_met", "owner_approval", "failure_revocation"
    consecutive_successes_at_time: int
    owner_approved: Optional[bool] = None


# ── Graduation Proposal ──────────────────────────────────────────

@dataclass
class GraduationProposal:
    """A pending request to lower a capability's risk tier."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    capability: str = ""
    scope: str = ""
    current_tier: RiskTier = RiskTier.T3_IRREVERSIBLE
    proposed_tier: RiskTier = RiskTier.T2_CONTROLLED
    consecutive_successes: int = 0
    total_successes: int = 0
    total_failures: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "pending"


# ── Trust Record ─────────────────────────────────────────────────

@dataclass
class TrustRecord:
    """Per-capability trust tracking record."""
    capability: str
    scope: str
    current_tier: RiskTier
    default_tier: RiskTier
    soul_minimum_tier: RiskTier = RiskTier.T0_INERT
    consecutive_successes: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_rollbacks: int = 0
    last_success: str = ""
    last_failure: str = ""
    graduation_history: List[GraduationEvent] = field(default_factory=list)
    pending_proposal: Optional[GraduationProposal] = None
    cooldown_remaining: int = 0

    @property
    def success_rate(self) -> float:
        """Success rate as a float between 0.0 and 1.0."""
        total = self.total_successes + self.total_failures
        if total == 0:
            return 0.0
        return self.total_successes / total

    @property
    def is_graduated(self) -> bool:
        """True if current tier is lower (less restrictive) than default."""
        return self.current_tier < self.default_tier

    @property
    def can_graduate(self) -> bool:
        """True if this record is eligible for graduation."""
        return (
            self.current_tier > self.soul_minimum_tier
            and self.cooldown_remaining == 0
            and self.pending_proposal is None
        )


# ── Pydantic Config Models ───────────────────────────────────────

class TrustGraduationThresholds(BaseModel):
    """Number of consecutive successes needed for each tier transition."""
    T3_to_T2: int = 50
    T2_to_T1: int = 100
    T1_to_T0: int = 200


class TrustRevocationConfig(BaseModel):
    """Policy for trust revocation on failure/rollback."""
    on_failure: str = "reset_to_default"
    on_rollback: str = "reset_above_default"
    cooldown_after_denial: int = 50
    cooldown_after_revocation: int = 25


class TrustGraduationConfig(BaseModel):
    """Top-level trust graduation configuration."""
    version: str = "1.0"
    thresholds: TrustGraduationThresholds = TrustGraduationThresholds()
    revocation: TrustRevocationConfig = TrustRevocationConfig()
    proposal_delivery: str = "war_room"


# ── Config Loader ────────────────────────────────────────────────

def load_trust_config(path: str = "config/trust_graduation.yaml") -> TrustGraduationConfig:
    """Load trust graduation config from YAML. Returns defaults if file missing."""
    try:
        config_path = Path(path)
        if not config_path.exists():
            logger.warning("Trust config not found at %s, using defaults", path)
            return TrustGraduationConfig()
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return TrustGraduationConfig(**data)
    except Exception as e:
        logger.error("Failed to load trust config: %s", e)
        return TrustGraduationConfig()
