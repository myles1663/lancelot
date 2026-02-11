"""
Lancelot vNext4: Governance Configuration Loader

Loads and validates governance.yaml using Pydantic v2.
Provides sensible defaults when config file is missing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


# ── Config Models ────────────────────────────────────────────────

class ScopeEscalation(BaseModel):
    """A rule that escalates a capability to a higher tier based on scope/pattern."""
    capability: str
    scope: str = ""
    pattern: str = ""
    escalate_to: int = 3
    reason: str = ""

    @field_validator("escalate_to")
    @classmethod
    def validate_escalate_to(cls, v: int) -> int:
        if v < 0 or v > 3:
            raise ValueError(f"escalate_to must be 0-3, got {v}")
        return v


class RiskClassificationConfig(BaseModel):
    """Default tier assignments and scope escalation rules."""
    defaults: dict[str, int] = {}
    scope_escalations: list[ScopeEscalation] = []


class PolicyCacheConfig(BaseModel):
    """Configuration for the precomputed policy cache."""
    enabled: bool = True
    recompile_on_soul_change: bool = True
    validate_soul_version: bool = True


class AsyncVerificationConfig(BaseModel):
    """Configuration for the async verification queue."""
    enabled: bool = True
    max_workers: int = 2
    queue_max_depth: int = 10
    fallback_to_sync_on_full: bool = True
    drain_timeout_seconds: int = 30

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError(f"max_workers must be 1-10, got {v}")
        return v

    @field_validator("queue_max_depth")
    @classmethod
    def validate_queue_max_depth(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError(f"queue_max_depth must be 1-100, got {v}")
        return v

    @field_validator("drain_timeout_seconds")
    @classmethod
    def validate_drain_timeout(cls, v: int) -> int:
        if v < 1 or v > 300:
            raise ValueError(f"drain_timeout_seconds must be 1-300, got {v}")
        return v


class IntentTemplateConfig(BaseModel):
    """Configuration for intent template caching."""
    enabled: bool = True
    promotion_threshold: int = 3
    max_template_age_days: int = 30
    max_cached_templates: int = 100
    max_template_risk_tier: int = 1  # Templates NEVER for T2+

    @field_validator("promotion_threshold")
    @classmethod
    def validate_promotion_threshold(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError(f"promotion_threshold must be 1-100, got {v}")
        return v

    @field_validator("max_template_risk_tier")
    @classmethod
    def validate_max_risk_tier(cls, v: int) -> int:
        if v < 0 or v > 1:
            raise ValueError(f"max_template_risk_tier must be 0-1, got {v}")
        return v


class BatchReceiptConfig(BaseModel):
    """Configuration for batched receipt emission."""
    enabled: bool = True
    buffer_size: int = 20
    flush_on_tier_boundary: bool = True
    flush_on_task_complete: bool = True

    @field_validator("buffer_size")
    @classmethod
    def validate_buffer_size(cls, v: int) -> int:
        if v < 1 or v > 1000:
            raise ValueError(f"buffer_size must be 1-1000, got {v}")
        return v


class GovernanceConfig(BaseModel):
    """Top-level governance configuration."""
    version: str = "1.0"
    risk_classification: RiskClassificationConfig = RiskClassificationConfig()
    policy_cache: PolicyCacheConfig = PolicyCacheConfig()
    async_verification: AsyncVerificationConfig = AsyncVerificationConfig()
    intent_templates: IntentTemplateConfig = IntentTemplateConfig()
    batch_receipts: BatchReceiptConfig = BatchReceiptConfig()


# ── Loader ───────────────────────────────────────────────────────

def load_governance_config(config_path: Optional[str] = None) -> GovernanceConfig:
    """Load governance config from YAML.

    Args:
        config_path: Path to governance.yaml. If None, searches standard locations.

    Returns:
        Parsed GovernanceConfig. Returns defaults if file is missing.
    """
    if config_path is None:
        # Search standard locations
        candidates = [
            "config/governance.yaml",
            "/home/lancelot/app/config/governance.yaml",
            os.path.join(os.path.dirname(__file__), "../../../config/governance.yaml"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                config_path = candidate
                break

    if config_path is None or not os.path.exists(config_path):
        logger.warning("Governance config not found, using defaults")
        return GovernanceConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            logger.warning("Governance config is empty, using defaults")
            return GovernanceConfig()

        return GovernanceConfig.model_validate(raw)
    except Exception as e:
        logger.error("Failed to load governance config: %s", e)
        return GovernanceConfig()
