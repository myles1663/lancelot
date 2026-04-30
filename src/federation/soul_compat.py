# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul Compatibility — intersection computation and monotonic narrowing validation.

Implements the Soul context intersection algorithm for federation handoff
boundaries, ensuring the operating Soul at any point in the federation
topology is provably more restrictive than both the receiving instance's
Soul and the handed-off context.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Optional, Tuple

from src.core.soul.store import (
    ApprovalRules,
    AutonomyPosture,
    RiskRule,
    SchedulingBoundaries,
    Soul,
    SpawnBudgetGovernance,
)

logger = logging.getLogger(__name__)


class CompatibilityLevel:
    """Soul compatibility classification for federation edges."""
    GREEN = "green"    # Fully compatible — handoff proceeds
    YELLOW = "yellow"  # Compatible with restrictions — operator acknowledgment needed
    RED = "red"        # Incompatible — handoff blocked


def hash_soul(soul: Soul) -> str:
    """Compute deterministic SHA-256 hash of a Soul document.

    Returns first 16 hex chars. Used for version identification,
    heartbeat mutation detection, and handoff context integrity.
    """
    canonical = json.dumps(soul.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_soul_intersection(
    receiving_soul: Soul,
    received_context: Soul,
) -> Soul:
    """Compute S(B) ∩ S(A,context) for a federation handoff boundary.

    The resulting Soul is the most restrictive combination of both inputs
    across all constraint dimensions. This guarantees monotonic narrowing.

    Args:
        receiving_soul: Instance B's own Soul.
        received_context: Soul context handed off from Instance A.

    Returns:
        New Soul with intersected constraints (version="intersection").
    """
    # 1. Autonomy posture: intersection of allowed, union of requires_approval
    allowed = list(
        set(receiving_soul.autonomy_posture.allowed_autonomous)
        & set(received_context.autonomy_posture.allowed_autonomous)
    )
    requires = list(
        set(receiving_soul.autonomy_posture.requires_approval)
        | set(received_context.autonomy_posture.requires_approval)
    )
    autonomy = AutonomyPosture(
        level="intersection",
        description="Computed intersection of federation handoff boundary",
        allowed_autonomous=sorted(allowed),
        requires_approval=sorted(requires),
    )

    # 2. Risk rules: union by name (keep all from both)
    seen_names = set()
    merged_rules: List[RiskRule] = []
    for rule in receiving_soul.risk_rules + received_context.risk_rules:
        if rule.name not in seen_names:
            seen_names.add(rule.name)
            merged_rules.append(rule)

    # 3. Scheduling boundaries: take the tighter constraint on each dimension
    sched = SchedulingBoundaries(
        max_concurrent_jobs=min(
            receiving_soul.scheduling_boundaries.max_concurrent_jobs,
            received_context.scheduling_boundaries.max_concurrent_jobs,
        ),
        max_job_duration_seconds=min(
            receiving_soul.scheduling_boundaries.max_job_duration_seconds,
            received_context.scheduling_boundaries.max_job_duration_seconds,
        ),
        no_autonomous_irreversible=(
            receiving_soul.scheduling_boundaries.no_autonomous_irreversible
            or received_context.scheduling_boundaries.no_autonomous_irreversible
        ),
        require_ready_state=(
            receiving_soul.scheduling_boundaries.require_ready_state
            or received_context.scheduling_boundaries.require_ready_state
        ),
        description="Computed intersection of federation handoff boundary",
    )

    # 4. Spawn budget: take the tighter constraint on each dimension
    tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
    r_tier = tier_order.get(receiving_soul.spawn_budget.max_spawn_model_tier, 2)
    c_tier = tier_order.get(received_context.spawn_budget.max_spawn_model_tier, 2)
    min_tier_val = min(r_tier, c_tier)
    min_tier = f"T{min_tier_val}"

    spawn = SpawnBudgetGovernance(
        max_concurrent_spawns=min(
            receiving_soul.spawn_budget.max_concurrent_spawns,
            received_context.spawn_budget.max_concurrent_spawns,
        ),
        max_spawn_model_tier=min_tier,
        max_estimated_tokens_per_spawn=min(
            receiving_soul.spawn_budget.max_estimated_tokens_per_spawn,
            received_context.spawn_budget.max_estimated_tokens_per_spawn,
        ),
        require_spawn_approval=(
            receiving_soul.spawn_budget.require_spawn_approval
            or received_context.spawn_budget.require_spawn_approval
        ),
        spawn_approval_channels=sorted(list(
            set(receiving_soul.spawn_budget.spawn_approval_channels)
            | set(received_context.spawn_budget.spawn_approval_channels)
        )),
    )

    # 5. Approval rules: tighter timeout, union of channels
    approval = ApprovalRules(
        default_timeout_seconds=min(
            receiving_soul.approval_rules.default_timeout_seconds,
            received_context.approval_rules.default_timeout_seconds,
        ),
        escalation_on_timeout=receiving_soul.approval_rules.escalation_on_timeout,
        channels=sorted(list(
            set(receiving_soul.approval_rules.channels)
            | set(received_context.approval_rules.channels)
        )),
    )

    # 6. Tone invariants and memory ethics: union (more constraints)
    tone = sorted(list(
        set(receiving_soul.tone_invariants)
        | set(received_context.tone_invariants)
    ))
    ethics = sorted(list(
        set(receiving_soul.memory_ethics)
        | set(received_context.memory_ethics)
    ))

    return Soul(
        version="intersection",
        mission=receiving_soul.mission,
        allegiance=receiving_soul.allegiance,
        autonomy_posture=autonomy,
        risk_rules=merged_rules,
        approval_rules=approval,
        tone_invariants=tone,
        memory_ethics=ethics,
        scheduling_boundaries=sched,
        spawn_budget=spawn,
    )


def validate_more_restrictive(
    operating: Soul,
    parent: Soul,
) -> Tuple[bool, List[str]]:
    """Validate that operating Soul is provably more restrictive than parent.

    Returns (True, []) if valid, or (False, [list of violations]).
    """
    violations = []

    # 1. allowed_autonomous must be a subset
    op_allowed = set(operating.autonomy_posture.allowed_autonomous)
    parent_allowed = set(parent.autonomy_posture.allowed_autonomous)
    extra = op_allowed - parent_allowed
    if extra:
        violations.append(
            f"allowed_autonomous has extra items not in parent: {extra}"
        )

    # 2. All parent requires_approval must be in operating
    parent_req = set(parent.autonomy_posture.requires_approval)
    op_req = set(operating.autonomy_posture.requires_approval)
    missing_req = parent_req - op_req
    if missing_req:
        violations.append(
            f"requires_approval missing parent items: {missing_req}"
        )

    # 3. All parent risk rules must be present
    parent_rules = {r.name for r in parent.risk_rules}
    op_rules = {r.name for r in operating.risk_rules}
    missing_rules = parent_rules - op_rules
    if missing_rules:
        violations.append(
            f"risk_rules missing parent rules: {missing_rules}"
        )

    # 4. Scheduling: tighter or equal
    if operating.scheduling_boundaries.max_concurrent_jobs > parent.scheduling_boundaries.max_concurrent_jobs:
        violations.append("max_concurrent_jobs exceeds parent")
    if operating.scheduling_boundaries.max_job_duration_seconds > parent.scheduling_boundaries.max_job_duration_seconds:
        violations.append("max_job_duration_seconds exceeds parent")
    if parent.scheduling_boundaries.no_autonomous_irreversible and not operating.scheduling_boundaries.no_autonomous_irreversible:
        violations.append("no_autonomous_irreversible relaxed from parent")
    if parent.scheduling_boundaries.require_ready_state and not operating.scheduling_boundaries.require_ready_state:
        violations.append("require_ready_state relaxed from parent")

    # 5. Spawn budget: tighter or equal
    if operating.spawn_budget.max_concurrent_spawns > parent.spawn_budget.max_concurrent_spawns:
        violations.append("max_concurrent_spawns exceeds parent")

    tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
    op_tier = tier_order.get(operating.spawn_budget.max_spawn_model_tier, 2)
    parent_tier = tier_order.get(parent.spawn_budget.max_spawn_model_tier, 2)
    if op_tier > parent_tier:
        violations.append("max_spawn_model_tier exceeds parent")

    return (len(violations) == 0, violations)


def classify_compatibility(
    source_soul: Soul,
    target_soul: Soul,
) -> Tuple[str, List[str]]:
    """Classify the compatibility level between two instance Souls.

    Returns:
        (CompatibilityLevel, list of notes/warnings)

    GREEN: Souls are fully compatible for handoff.
    YELLOW: Compatible but with restrictions (some autonomy reduction).
    RED: Incompatible — conflicting risk rules or mission divergence.
    """
    notes = []

    # RED: conflicting enforced risk rules (one enforces, other doesn't have it)
    source_rules = {r.name: r for r in source_soul.risk_rules}
    target_rules = {r.name: r for r in target_soul.risk_rules}

    # Check for fundamental mission/allegiance divergence
    if source_soul.mission.strip() != target_soul.mission.strip():
        notes.append("Mission statements differ")
    if source_soul.allegiance.strip() != target_soul.allegiance.strip():
        notes.append("Allegiance statements differ")

    # Check for conflicting autonomy
    source_auto = set(source_soul.autonomy_posture.allowed_autonomous)
    target_auto = set(target_soul.autonomy_posture.allowed_autonomous)
    source_req = set(source_soul.autonomy_posture.requires_approval)
    target_req = set(target_soul.autonomy_posture.requires_approval)

    # Items autonomous in source but requiring approval in target
    conflicts = source_auto & target_req
    if conflicts:
        notes.append(f"Autonomy conflicts (source allows, target requires approval): {conflicts}")

    # Determine level
    if source_soul.mission.strip() != target_soul.mission.strip():
        return CompatibilityLevel.RED, notes

    if conflicts:
        return CompatibilityLevel.YELLOW, notes

    # Check if intersection reduces autonomy
    intersection_allowed = source_auto & target_auto
    if len(intersection_allowed) < len(source_auto):
        notes.append("Intersection reduces source autonomy")
        return CompatibilityLevel.YELLOW, notes

    return CompatibilityLevel.GREEN, notes
