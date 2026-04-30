"""
PatternDetector — identifies repeating decision patterns.

Single-dimension (P69), multi-dimensional (P70), and proposal generation (P71).
Specificity-first: proposes the most specific rule the data supports.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from src.core.governance.approval_learning.config import APLConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    ApprovalPattern,
    AutomationRule,
    DecisionContext,
    DecisionRecord,
)


# ── Time buckets for temporal pattern detection ─────────────────

_TIME_BUCKETS = [
    ("morning", (6, 12)),
    ("afternoon", (12, 17)),
    ("evening", (17, 22)),
    ("night", (22, 6)),
    ("business_hours", (9, 17)),
]

_DAY_BUCKETS = [
    ("weekdays", (0, 4)),
    ("weekends", (5, 6)),
]


class PatternDetector:
    """Detects patterns in owner approval/denial decisions."""

    def __init__(self, config: APLConfig):
        self._config = config

    # ── Single-Dimension Detection (P69) ────────────────────────

    def detect_single_dimension(
        self, decisions: List[DecisionRecord]
    ) -> List[ApprovalPattern]:
        """Find patterns across individual dimensions."""
        patterns: List[ApprovalPattern] = []

        # 1. By capability
        groups = self._group_by_dimension(decisions, lambda d: d.context.capability)
        for key, group in groups.items():
            p = self._build_pattern(group, capability=key)
            if p:
                patterns.append(p)

        # 2. By target_domain
        groups = self._group_by_dimension(
            decisions, lambda d: d.context.target_domain
        )
        for key, group in groups.items():
            if key:  # Skip empty domains
                p = self._build_pattern(group, target_domain=key)
                if p:
                    patterns.append(p)

        # 3. By target_category
        groups = self._group_by_dimension(
            decisions, lambda d: d.context.target_category
        )
        for key, group in groups.items():
            if key:
                p = self._build_pattern(group, target_category=key)
                if p:
                    patterns.append(p)

        # 4. By scope
        groups = self._group_by_dimension(decisions, lambda d: d.context.scope)
        for key, group in groups.items():
            if key:
                p = self._build_pattern(group, scope=key)
                if p:
                    patterns.append(p)

        # 5. Time patterns
        patterns.extend(self._detect_time_patterns(decisions))

        # 6. Day patterns
        patterns.extend(self._detect_day_patterns(decisions))

        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns

    # ── Multi-Dimensional Detection (P70) ───────────────────────

    def detect_multi_dimension(
        self,
        decisions: List[DecisionRecord],
        base_patterns: List[ApprovalPattern],
    ) -> List[ApprovalPattern]:
        """Extend single-dimension patterns to multi-dimensional ones.

        For each base pattern, filter matching decisions and sub-group
        by remaining dimensions. If sub-group has higher confidence,
        create a more specific pattern.
        """
        multi_patterns: List[ApprovalPattern] = []
        max_dims = self._config.detection.max_pattern_dimensions

        current_patterns = list(base_patterns)
        for _depth in range(max_dims - 1):
            next_level: List[ApprovalPattern] = []
            for bp in current_patterns:
                if bp.specificity >= max_dims:
                    continue

                # Filter decisions matching this base pattern
                matching = [d for d in decisions if bp.matches(d.context)]
                if len(matching) < self._config.detection.min_observations:
                    continue

                # Try adding each unset dimension
                extensions = self._extend_pattern(bp, matching)
                next_level.extend(extensions)

            multi_patterns.extend(next_level)
            current_patterns = next_level
            if not next_level:
                break

        return multi_patterns

    def _extend_pattern(
        self, base: ApprovalPattern, decisions: List[DecisionRecord]
    ) -> List[ApprovalPattern]:
        """Try adding each unset dimension to a base pattern."""
        extensions: List[ApprovalPattern] = []

        dimension_extractors: List[
            Tuple[str, Callable[[DecisionRecord], str], str]
        ] = []

        if base.capability is None:
            dimension_extractors.append(
                ("capability", lambda d: d.context.capability, "capability")
            )
        if base.target_domain is None:
            dimension_extractors.append(
                ("target_domain", lambda d: d.context.target_domain, "target_domain")
            )
        if base.target_category is None:
            dimension_extractors.append(
                (
                    "target_category",
                    lambda d: d.context.target_category,
                    "target_category",
                )
            )
        if base.scope is None:
            dimension_extractors.append(
                ("scope", lambda d: d.context.scope, "scope")
            )
        if base.time_range is None:
            # Try time buckets
            for name, (start, end) in _TIME_BUCKETS:
                sub = [d for d in decisions if self._in_time_range(d, start, end)]
                if len(sub) >= self._config.detection.min_observations:
                    p = self._build_pattern_from_base(
                        base, sub, decisions, time_range=(start, end)
                    )
                    if p and p.confidence >= base.confidence:
                        extensions.append(p)

        if base.day_range is None:
            for name, (start, end) in _DAY_BUCKETS:
                sub = [d for d in decisions if start <= d.context.day_of_week <= end]
                if len(sub) >= self._config.detection.min_observations:
                    p = self._build_pattern_from_base(
                        base, sub, decisions, day_range=(start, end)
                    )
                    if p and p.confidence >= base.confidence:
                        extensions.append(p)

        for dim_name, extractor, kwarg_name in dimension_extractors:
            groups = self._group_by_dimension(decisions, extractor)
            for key, group in groups.items():
                if not key:
                    continue
                if len(group) < self._config.detection.min_observations:
                    continue
                p = self._build_pattern_from_base(
                    base, group, decisions, **{kwarg_name: key}
                )
                if p and p.confidence >= base.confidence:
                    extensions.append(p)

        return extensions

    def _build_pattern_from_base(
        self,
        base: ApprovalPattern,
        sub_decisions: List[DecisionRecord],
        all_decisions: List[DecisionRecord],
        **new_dims,
    ) -> Optional[ApprovalPattern]:
        """Create a more specific pattern by adding dimensions to a base."""
        approvals = sum(1 for d in sub_decisions if d.is_approval)
        denials = len(sub_decisions) - approvals

        if approvals >= denials:
            pattern_type = "approval"
            consistent = approvals
        else:
            pattern_type = "denial"
            consistent = denials

        p = ApprovalPattern(
            id=str(uuid.uuid4()),
            pattern_type=pattern_type,
            capability=new_dims.get("capability", base.capability),
            target_domain=new_dims.get("target_domain", base.target_domain),
            target_category=new_dims.get("target_category", base.target_category),
            scope=new_dims.get("scope", base.scope),
            time_range=new_dims.get("time_range", base.time_range),
            day_range=new_dims.get("day_range", base.day_range),
            total_observations=len(sub_decisions),
            consistent_decisions=consistent,
            first_observed=sub_decisions[0].recorded_at if sub_decisions else "",
            last_observed=sub_decisions[-1].recorded_at if sub_decisions else "",
            avg_decision_time_ms=(
                sum(d.decision_time_ms for d in sub_decisions) / len(sub_decisions)
                if sub_decisions
                else 0.0
            ),
        )

        if p.confidence >= self._config.detection.confidence_threshold:
            return p
        return None

    def detect_all(
        self, decisions: List[DecisionRecord]
    ) -> List[ApprovalPattern]:
        """Full analysis pipeline.

        1. detect_single_dimension()
        2. detect_multi_dimension()
        3. Deduplicate: prefer more specific patterns
        4. Filter by confidence_threshold
        5. Sort by score descending
        """
        single = self.detect_single_dimension(decisions)
        multi = self.detect_multi_dimension(decisions, single)

        all_patterns = single + multi

        # Deduplicate: if a multi-dim pattern subsumes a single-dim one
        # with similar or higher confidence, drop the single-dim one
        deduped = self._deduplicate(all_patterns)

        # Filter by threshold
        threshold = self._config.detection.confidence_threshold
        filtered = [p for p in deduped if p.confidence >= threshold]

        # Sort by score (confidence * specificity bonus)
        filtered.sort(key=lambda p: self._score_pattern(p), reverse=True)
        return filtered

    def _deduplicate(
        self, patterns: List[ApprovalPattern]
    ) -> List[ApprovalPattern]:
        """Remove less specific patterns when a more specific one exists."""
        if not patterns:
            return []

        # Sort by specificity descending, then confidence descending
        patterns.sort(key=lambda p: (p.specificity, p.confidence), reverse=True)

        kept: List[ApprovalPattern] = []
        for p in patterns:
            subsumed = False
            for existing in kept:
                if existing.specificity > p.specificity and self._subsumes(existing, p):
                    subsumed = True
                    break
            if not subsumed:
                kept.append(p)

        return kept

    def _subsumes(self, specific: ApprovalPattern, general: ApprovalPattern) -> bool:
        """Check if the specific pattern covers the same ground as general."""
        # The specific pattern must have all the same non-None conditions as general
        if general.capability is not None and specific.capability != general.capability:
            return False
        if (
            general.target_domain is not None
            and specific.target_domain != general.target_domain
        ):
            return False
        if (
            general.target_category is not None
            and specific.target_category != general.target_category
        ):
            return False
        if general.scope is not None and specific.scope != general.scope:
            return False
        return True

    def _score_pattern(self, pattern: ApprovalPattern) -> float:
        """Combined score: confidence * (1 + 0.2 * specificity)."""
        return pattern.confidence * (1 + 0.2 * pattern.specificity)

    def should_analyze(self, decision_log: DecisionLog) -> bool:
        """True if enough new decisions since last analysis."""
        return (
            decision_log.count_since_last_analysis()
            >= self._config.detection.analysis_trigger_interval
        )

    # ── Proposal Generation (P71) ──────────────────────────────

    def generate_proposals(
        self,
        patterns: List[ApprovalPattern],
        config: APLConfig,
    ) -> List[AutomationRule]:
        """Convert detected patterns into rule proposals."""
        proposals: List[AutomationRule] = []

        for pattern in patterns:
            # Check never_automate
            if pattern.capability and config.is_never_automate(pattern.capability):
                continue

            soul_compatible = True
            if pattern.capability and config.is_never_automate(pattern.capability):
                soul_compatible = False

            name = self._generate_rule_name(pattern)
            description = self._generate_rule_description(pattern)
            conditions = self._serialize_conditions(pattern)

            rule = AutomationRule(
                id=str(uuid.uuid4()),
                name=name,
                description=description,
                pattern_id=pattern.id,
                pattern_type=(
                    "auto_approve" if pattern.pattern_type == "approval" else "auto_deny"
                ),
                conditions=conditions,
                status="proposed",
                created_at=datetime.now(timezone.utc).isoformat(),
                max_auto_decisions_per_day=config.rules.max_auto_decisions_per_day,
                max_auto_decisions_total=config.rules.max_auto_decisions_total,
                owner_confirmed=False,
                soul_compatible=soul_compatible,
            )
            proposals.append(rule)

        return proposals

    def _generate_rule_name(self, pattern: ApprovalPattern) -> str:
        """Human-readable name from pattern conditions."""
        parts = []
        action = "Auto-approve" if pattern.pattern_type == "approval" else "Auto-deny"
        parts.append(action)

        if pattern.capability:
            # Shorten capability: "connector.email.send_message" → "email send_message"
            cap_parts = pattern.capability.split(".")
            if len(cap_parts) >= 3:
                parts.append(f"{cap_parts[1]} {cap_parts[-1]}")
            else:
                parts.append(pattern.capability)

        if pattern.target_domain:
            parts.append(f"to @{pattern.target_domain}")

        if pattern.scope:
            parts.append(f"in {pattern.scope}")

        if pattern.time_range:
            start, end = pattern.time_range
            parts.append(f"({start:02d}:00-{end:02d}:00)")

        if pattern.day_range:
            start, end = pattern.day_range
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            parts.append(f"{day_names[start]}-{day_names[end]}")

        return " ".join(parts)

    def _generate_rule_description(self, pattern: ApprovalPattern) -> str:
        """Detailed description with stats."""
        lines = []
        action_word = "approved" if pattern.pattern_type == "approval" else "denied"
        lines.append(
            f"Based on {pattern.consistent_decisions} consistent {action_word} decisions "
            f"out of {pattern.total_observations} observations."
        )
        if pattern.avg_decision_time_ms > 0:
            lines.append(
                f"Average decision time: {pattern.avg_decision_time_ms:.0f}ms."
            )
        if pattern.first_observed and pattern.last_observed:
            lines.append(
                f"Pattern observed from {pattern.first_observed[:10]} "
                f"to {pattern.last_observed[:10]}."
            )
        lines.append(f"Confidence: {pattern.confidence:.1%}.")
        return " ".join(lines)

    def _serialize_conditions(self, pattern: ApprovalPattern) -> dict:
        """Convert pattern to storable conditions dict. Only non-None fields."""
        conditions: Dict = {}
        if pattern.capability is not None:
            conditions["capability"] = pattern.capability
        if pattern.target_domain is not None:
            conditions["target_domain"] = pattern.target_domain
        if pattern.target_category is not None:
            conditions["target_category"] = pattern.target_category
        if pattern.scope is not None:
            conditions["scope"] = pattern.scope
        if pattern.time_range is not None:
            conditions["time_range"] = list(pattern.time_range)
        if pattern.day_range is not None:
            conditions["day_range"] = list(pattern.day_range)
        return conditions

    # ── Internal helpers ────────────────────────────────────────

    def _group_by_dimension(
        self,
        decisions: List[DecisionRecord],
        extractor: Callable[[DecisionRecord], str],
    ) -> Dict[str, List[DecisionRecord]]:
        """Group decisions by a single extracted dimension value."""
        groups: Dict[str, List[DecisionRecord]] = defaultdict(list)
        for d in decisions:
            key = extractor(d)
            groups[key].append(d)
        return dict(groups)

    def _build_pattern(
        self,
        decisions: List[DecisionRecord],
        **kwargs,
    ) -> Optional[ApprovalPattern]:
        """Build a pattern from a group of decisions. Returns None if below thresholds."""
        if len(decisions) < self._config.detection.min_observations:
            return None

        approvals = sum(1 for d in decisions if d.is_approval)
        denials = len(decisions) - approvals

        if approvals >= denials:
            pattern_type = "approval"
            consistent = approvals
        else:
            pattern_type = "denial"
            consistent = denials

        p = ApprovalPattern(
            id=str(uuid.uuid4()),
            pattern_type=pattern_type,
            total_observations=len(decisions),
            consistent_decisions=consistent,
            first_observed=decisions[0].recorded_at if decisions else "",
            last_observed=decisions[-1].recorded_at if decisions else "",
            avg_decision_time_ms=(
                sum(d.decision_time_ms for d in decisions) / len(decisions)
                if decisions
                else 0.0
            ),
            **kwargs,
        )

        if p.confidence >= self._config.detection.confidence_threshold:
            return p
        return None

    def _detect_time_patterns(
        self, decisions: List[DecisionRecord]
    ) -> List[ApprovalPattern]:
        """Check if approvals cluster in specific time windows."""
        patterns: List[ApprovalPattern] = []
        for name, (start, end) in _TIME_BUCKETS:
            sub = [d for d in decisions if self._in_time_range(d, start, end)]
            p = self._build_pattern(sub, time_range=(start, end))
            if p:
                patterns.append(p)
        return patterns

    def _detect_day_patterns(
        self, decisions: List[DecisionRecord]
    ) -> List[ApprovalPattern]:
        """Check weekday vs weekend patterns."""
        patterns: List[ApprovalPattern] = []
        for name, (start, end) in _DAY_BUCKETS:
            sub = [d for d in decisions if start <= d.context.day_of_week <= end]
            p = self._build_pattern(sub, day_range=(start, end))
            if p:
                patterns.append(p)
        return patterns

    @staticmethod
    def _in_time_range(decision: DecisionRecord, start: int, end: int) -> bool:
        """Check if decision's hour falls in [start, end) range."""
        hour = decision.context.hour_of_day
        if start <= end:
            return start <= hour < end
        else:
            return hour >= start or hour < end
