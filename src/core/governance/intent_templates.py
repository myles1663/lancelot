"""
Lancelot vNext4: Intent Template System

Caches known-good execution plan skeletons for recurring intents.
Templates are learned from successful executions and promoted
after a configurable success threshold.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import IntentTemplateConfig
from .models import RiskTier

logger = logging.getLogger(__name__)


@dataclass
class PlanStepTemplate:
    """A single step in a cached plan skeleton."""
    capability: str
    scope: str = "workspace"
    parameters: dict = field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.T0_INERT

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "scope": self.scope,
            "parameters": dict(self.parameters),
            "risk_tier": int(self.risk_tier),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanStepTemplate:
        return cls(
            capability=data["capability"],
            scope=data.get("scope", "workspace"),
            parameters=data.get("parameters", {}),
            risk_tier=RiskTier(data.get("risk_tier", 0)),
        )


@dataclass
class IntentTemplate:
    """A cached execution plan template for a known intent."""
    template_id: str
    intent_pattern: str
    plan_skeleton: list[PlanStepTemplate]
    max_risk_tier: RiskTier
    success_count: int = 0
    failure_count: int = 0
    last_used: str = ""
    created_from: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    active: bool = False
    invalidation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "intent_pattern": self.intent_pattern,
            "plan_skeleton": [s.to_dict() for s in self.plan_skeleton],
            "max_risk_tier": int(self.max_risk_tier),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "created_from": self.created_from,
            "created_at": self.created_at,
            "active": self.active,
            "invalidation_reason": self.invalidation_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> IntentTemplate:
        return cls(
            template_id=data["template_id"],
            intent_pattern=data["intent_pattern"],
            plan_skeleton=[
                PlanStepTemplate.from_dict(s) for s in data.get("plan_skeleton", [])
            ],
            max_risk_tier=RiskTier(data.get("max_risk_tier", 0)),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            last_used=data.get("last_used", ""),
            created_from=data.get("created_from", ""),
            created_at=data.get("created_at", ""),
            active=data.get("active", False),
            invalidation_reason=data.get("invalidation_reason", ""),
        )


class IntentTemplateRegistry:
    """Registry for cached execution plan templates.

    Templates are learned from successful executions and promoted
    after reaching the configurable success threshold.
    """

    def __init__(self, config: IntentTemplateConfig, data_dir: str = "data"):
        self._config = config
        self._data_dir = data_dir
        self._templates: dict[str, IntentTemplate] = {}
        self._persistence_path = os.path.join(data_dir, "intent_templates.json")
        self._load()

    def _load(self):
        """Load templates from JSON persistence file."""
        if os.path.exists(self._persistence_path):
            try:
                with open(self._persistence_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        template = IntentTemplate.from_dict(item)
                        self._templates[template.template_id] = template
                logger.info("Loaded %d intent templates", len(self._templates))
            except Exception as e:
                logger.error("Failed to load intent templates: %s", e)

    def _save(self):
        """Persist all templates to JSON."""
        os.makedirs(self._data_dir, exist_ok=True)
        data = [t.to_dict() for t in self._templates.values()]
        with open(self._persistence_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def create_candidate(
        self,
        intent: str,
        plan_steps: list[dict],
        receipt_id: str = "",
    ) -> str:
        """Create a template candidate from a successful execution.

        Returns template_id.

        Safety: Rejects if any step has risk_tier > config.max_template_risk_tier.
        """
        max_allowed = self._config.max_template_risk_tier

        skeleton = []
        highest_tier = RiskTier.T0_INERT
        for step_data in plan_steps:
            step = PlanStepTemplate(
                capability=step_data.get("capability", ""),
                scope=step_data.get("scope", "workspace"),
                parameters=step_data.get("parameters", {}),
                risk_tier=RiskTier(step_data.get("risk_tier", 0)),
            )
            if int(step.risk_tier) > max_allowed:
                raise ValueError(
                    f"Step {step.capability} has risk_tier {step.risk_tier.name} "
                    f"which exceeds max_template_risk_tier ({max_allowed})"
                )
            if step.risk_tier > highest_tier:
                highest_tier = step.risk_tier
            skeleton.append(step)

        template_id = str(uuid.uuid4())
        template = IntentTemplate(
            template_id=template_id,
            intent_pattern=intent,
            plan_skeleton=skeleton,
            max_risk_tier=highest_tier,
            success_count=1,
            created_from=receipt_id,
            active=False,
        )
        self._templates[template_id] = template
        self._save()
        logger.info("Created template candidate %s for intent '%s'", template_id, intent)
        return template_id

    # ── Matching (Prompt 17) ─────────────────────────────────────

    def match(self, intent: str, parameters: dict = None) -> Optional[IntentTemplate]:
        """Find an active template matching the given intent.

        Uses simple keyword matching: if the intent string contains
        the template's intent_pattern (case-insensitive), it's a match.

        Only returns active templates (promoted past threshold).
        Updates last_used timestamp on match.
        """
        intent_lower = intent.lower()
        for template in self._templates.values():
            if template.active and template.intent_pattern.lower() in intent_lower:
                template.last_used = datetime.now(timezone.utc).isoformat()
                self._save()
                return template
        return None

    # ── Success/Failure Tracking + Promotion ─────────────────────

    def record_success(self, template_id: str) -> None:
        """Increment success_count. Promote if threshold reached."""
        template = self._templates.get(template_id)
        if template is None:
            return
        template.success_count += 1
        if (
            not template.active
            and template.success_count >= self._config.promotion_threshold
        ):
            template.active = True
            logger.info("Template %s promoted to active", template_id)
        self._save()

    def record_failure(self, template_id: str) -> None:
        """Increment failure_count. Deactivate if failures > successes."""
        template = self._templates.get(template_id)
        if template is None:
            return
        template.failure_count += 1
        if template.failure_count > template.success_count:
            template.active = False
            logger.info("Template %s deactivated (failures > successes)", template_id)
        self._save()

    # ── Invalidation ─────────────────────────────────────────────

    def invalidate(self, template_id: str, reason: str = "") -> None:
        """Mark a template as inactive. Does not delete it."""
        template = self._templates.get(template_id)
        if template is not None:
            template.active = False
            template.invalidation_reason = reason
            self._save()

    def invalidate_all(self, reason: str = "") -> int:
        """Invalidate all templates. Returns count of invalidated.

        Used when Soul changes.
        """
        count = 0
        for template in self._templates.values():
            if template.active:
                template.active = False
                template.invalidation_reason = reason
                count += 1
        self._save()
        return count

    def cleanup_stale(self, max_age_days: int = None) -> int:
        """Remove templates not used in max_template_age_days.

        Returns count of removed.
        """
        age_days = max_age_days if max_age_days is not None else self._config.max_template_age_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
        to_remove = []

        for tid, template in self._templates.items():
            ref_date = template.last_used or template.created_at
            if not ref_date:
                to_remove.append(tid)
                continue
            try:
                parsed = datetime.fromisoformat(ref_date.replace("Z", "+00:00"))
                if parsed < cutoff:
                    to_remove.append(tid)
            except (ValueError, TypeError):
                to_remove.append(tid)

        for tid in to_remove:
            del self._templates[tid]

        if to_remove:
            self._save()
        return len(to_remove)

    # ── Queries ──────────────────────────────────────────────────

    def list_active(self) -> list[IntentTemplate]:
        """Return all promoted (active) templates."""
        return [t for t in self._templates.values() if t.active]

    def list_all(self) -> list[IntentTemplate]:
        """Return all templates."""
        return list(self._templates.values())

    def get_template(self, template_id: str) -> Optional[IntentTemplate]:
        """Retrieve a template by ID."""
        return self._templates.get(template_id)
