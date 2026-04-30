"""
RuleEngine — stores, activates, and enforces automation rules.

Handles rule lifecycle (propose → activate → pause/resume → revoke)
and runtime matching (check context against active rules).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.core.governance.approval_learning.config import APLConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    AutomationRule,
    DecisionContext,
    RuleCheckResult,
)

logger = logging.getLogger(__name__)


class RuleEngine:
    """Stores, activates, and enforces APL automation rules."""

    def __init__(self, config: APLConfig, decision_log: DecisionLog):
        self._config = config
        self._decision_log = decision_log
        self._rules: Dict[str, AutomationRule] = {}
        self._declined_patterns: Dict[str, int] = {}  # pattern_id → cooldown remaining
        self._lock = threading.Lock()
        self._load()

    def add_proposal(self, rule: AutomationRule) -> AutomationRule:
        """Add a proposed rule. Does NOT activate."""
        with self._lock:
            # Check max active rules
            active_count = sum(
                1 for r in self._rules.values() if r.status == "active"
            )
            if active_count >= self._config.rules.max_active_rules:
                raise ValueError(
                    f"Max active rules ({self._config.rules.max_active_rules}) reached"
                )

            # Check for duplicate pattern_id
            for existing in self._rules.values():
                if (
                    existing.pattern_id == rule.pattern_id
                    and existing.status in ("proposed", "active")
                ):
                    raise ValueError(
                        f"Rule for pattern {rule.pattern_id} already exists"
                    )

            self._rules[rule.id] = rule
            self._persist()
            return rule

    def activate_rule(self, rule_id: str) -> AutomationRule:
        """Owner confirmed. Set status=active, owner_confirmed=True."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(f"Rule {rule_id} not found")
            rule.status = "active"
            rule.owner_confirmed = True
            rule.activated_at = datetime.now(timezone.utc).isoformat()
            self._persist()
            return rule

    def decline_rule(self, rule_id: str, reason: str = "") -> AutomationRule:
        """Owner declined. Set status=revoked. Add cooldown."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(f"Rule {rule_id} not found")
            rule.status = "revoked"
            rule.revoked_at = datetime.now(timezone.utc).isoformat()
            self._declined_patterns[rule.pattern_id] = (
                self._config.rules.cooldown_after_decline
            )
            self._persist()
            return rule

    def pause_rule(self, rule_id: str) -> AutomationRule:
        """Temporarily disable."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(f"Rule {rule_id} not found")
            rule.status = "paused"
            self._persist()
            return rule

    def resume_rule(self, rule_id: str) -> AutomationRule:
        """Re-enable a paused rule."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(f"Rule {rule_id} not found")
            rule.status = "active"
            self._persist()
            return rule

    def revoke_rule(self, rule_id: str, reason: str = "") -> AutomationRule:
        """Permanently disable."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(f"Rule {rule_id} not found")
            rule.status = "revoked"
            rule.revoked_at = datetime.now(timezone.utc).isoformat()
            self._persist()
            return rule

    def check(self, context: DecisionContext) -> RuleCheckResult:
        """Check if any active rule matches this context.

        1. Collect all active matching rules
        2. If deny + approve both match: DENY WINS
        3. Most specific wins among same type
        4. Increment usage on matching rule
        """
        with self._lock:
            matching_approve: List[AutomationRule] = []
            matching_deny: List[AutomationRule] = []

            for rule in self._rules.values():
                if not rule.is_active:
                    continue
                if not rule.matches_context(context):
                    continue
                if rule.pattern_type == "auto_deny":
                    matching_deny.append(rule)
                else:
                    matching_approve.append(rule)

            # Deny wins over approve
            if matching_deny:
                best = max(matching_deny, key=lambda r: r.specificity)
                best.increment_usage()
                self._persist()
                return RuleCheckResult(
                    action="auto_deny",
                    rule_id=best.id,
                    rule_name=best.name,
                    reason=f"Denied by rule: {best.name}",
                )

            if matching_approve:
                best = max(matching_approve, key=lambda r: r.specificity)
                best.increment_usage()
                self._persist()
                return RuleCheckResult(
                    action="auto_approve",
                    rule_id=best.id,
                    rule_name=best.name,
                    reason=f"Approved by rule: {best.name}",
                )

            return RuleCheckResult(action="ask_owner")

    def check_circuit_breakers(self) -> List[AutomationRule]:
        """Return rules that have hit their daily limit."""
        with self._lock:
            return [
                r
                for r in self._rules.values()
                if r.status == "active"
                and r.owner_confirmed
                and r.auto_decisions_today >= r.max_auto_decisions_per_day
            ]

    def check_reconfirmation(self) -> List[AutomationRule]:
        """Return rules that have hit their total limit."""
        with self._lock:
            return [
                r
                for r in self._rules.values()
                if r.status == "active"
                and r.owner_confirmed
                and r.auto_decisions_total >= r.max_auto_decisions_total
            ]

    def is_pattern_declined(self, pattern_id: str) -> bool:
        """Check if pattern was recently declined (cooldown active)."""
        with self._lock:
            return self._declined_patterns.get(pattern_id, 0) > 0

    def decrement_cooldowns(self) -> None:
        """Decrement cooldowns by 1 (called after each manual decision)."""
        with self._lock:
            to_remove = []
            for pid in self._declined_patterns:
                self._declined_patterns[pid] -= 1
                if self._declined_patterns[pid] <= 0:
                    to_remove.append(pid)
            for pid in to_remove:
                del self._declined_patterns[pid]

    def list_rules(self, status: Optional[str] = None) -> List[AutomationRule]:
        """List rules, optionally filtered by status."""
        with self._lock:
            if status is None:
                return list(self._rules.values())
            return [r for r in self._rules.values() if r.status == status]

    def get_rule(self, rule_id: str) -> Optional[AutomationRule]:
        """Get a specific rule."""
        with self._lock:
            return self._rules.get(rule_id)

    def get_stats(self) -> dict:
        """Summary statistics."""
        with self._lock:
            rules = list(self._rules.values())
            active = [r for r in rules if r.status == "active"]
            return {
                "active": len(active),
                "proposed": sum(1 for r in rules if r.status == "proposed"),
                "paused": sum(1 for r in rules if r.status == "paused"),
                "revoked": sum(1 for r in rules if r.status == "revoked"),
                "auto_decisions_today": sum(r.auto_decisions_today for r in active),
                "auto_decisions_total": sum(r.auto_decisions_total for r in active),
                "top_rules": sorted(
                    active, key=lambda r: r.auto_decisions_total, reverse=True
                )[:5],
            }

    def _persist(self) -> None:
        """Save rules to JSON."""
        path = Path(self._config.persistence.rules_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rules": {
                    rid: self._serialize_rule(r) for rid, r in self._rules.items()
                },
                "declined_patterns": self._declined_patterns,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to persist rules: %s", e)

    def _load(self) -> None:
        """Load rules from JSON."""
        path = Path(self._config.persistence.rules_path)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for rid, rdata in data.get("rules", {}).items():
                self._rules[rid] = self._deserialize_rule(rdata)

            self._declined_patterns = data.get("declined_patterns", {})
        except Exception as e:
            logger.error("Failed to load rules: %s", e)

    @staticmethod
    def _serialize_rule(rule: AutomationRule) -> dict:
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "pattern_id": rule.pattern_id,
            "pattern_type": rule.pattern_type,
            "conditions": rule.conditions,
            "status": rule.status,
            "created_at": rule.created_at,
            "activated_at": rule.activated_at,
            "revoked_at": rule.revoked_at,
            "max_auto_decisions_per_day": rule.max_auto_decisions_per_day,
            "max_auto_decisions_total": rule.max_auto_decisions_total,
            "expires_at": rule.expires_at,
            "auto_decisions_today": rule.auto_decisions_today,
            "auto_decisions_total": rule.auto_decisions_total,
            "last_auto_decision": rule.last_auto_decision,
            "last_reset_date": rule.last_reset_date,
            "owner_confirmed": rule.owner_confirmed,
            "soul_compatible": rule.soul_compatible,
        }

    @staticmethod
    def _deserialize_rule(data: dict) -> AutomationRule:
        return AutomationRule(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            pattern_id=data["pattern_id"],
            pattern_type=data["pattern_type"],
            conditions=data.get("conditions", {}),
            status=data.get("status", "proposed"),
            created_at=data.get("created_at", ""),
            activated_at=data.get("activated_at", ""),
            revoked_at=data.get("revoked_at", ""),
            max_auto_decisions_per_day=data.get("max_auto_decisions_per_day", 50),
            max_auto_decisions_total=data.get("max_auto_decisions_total", 500),
            expires_at=data.get("expires_at", ""),
            auto_decisions_today=data.get("auto_decisions_today", 0),
            auto_decisions_total=data.get("auto_decisions_total", 0),
            last_auto_decision=data.get("last_auto_decision", ""),
            last_reset_date=data.get("last_reset_date", ""),
            owner_confirmed=data.get("owner_confirmed", False),
            soul_compatible=data.get("soul_compatible", True),
        )
