"""
Soul Layers — composable overlay system for domain-specific governance.

The Soul layer system enables additive overlays on top of the base Soul.
Overlays can ONLY append rules; they can NEVER remove, weaken, or override
base Soul fields (mission, allegiance, version).

Public API:
    SoulOverlay                         — Pydantic model for an overlay document
    load_overlays(soul_dir, features)   -> list[SoulOverlay]
    merge_soul(base, overlays)          -> Soul
    load_active_soul_with_overlays(...)  -> Soul
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Set

import yaml
from pydantic import BaseModel, Field

from src.core.soul.store import (
    Soul,
    RiskRule,
    AutonomyPosture,
    SchedulingBoundaries,
    SoulStoreError,
    load_active_soul,
    _resolve_soul_dir,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SoulOverlay model
# ---------------------------------------------------------------------------

class OverlayAutonomyPosture(BaseModel):
    """Additive autonomy posture entries from an overlay."""
    allowed_autonomous: List[str] = Field(default_factory=list)
    requires_approval: List[str] = Field(default_factory=list)


class SoulOverlay(BaseModel):
    """A domain-specific governance overlay for the Soul.

    Overlays are lighter than a full Soul document.  They contain only
    the additive fields that extend the base Soul's governance.
    """
    overlay_name: str
    feature_flag: str
    description: str = ""

    risk_rules: List[RiskRule] = Field(default_factory=list)
    tone_invariants: List[str] = Field(default_factory=list)
    memory_ethics: List[str] = Field(default_factory=list)
    autonomy_posture: OverlayAutonomyPosture = Field(
        default_factory=OverlayAutonomyPosture,
    )
    scheduling_boundaries: Optional[str] = None  # description text to append


# ---------------------------------------------------------------------------
# Overlay loading
# ---------------------------------------------------------------------------

def load_overlays(
    soul_dir: Optional[str] = None,
    active_features: Optional[Set[str]] = None,
) -> List[SoulOverlay]:
    """Load all overlays whose feature flags are currently active.

    Scans soul_dir/overlays/ for YAML files, parses them as SoulOverlay,
    and returns only those whose feature_flag is present in active_features.

    Args:
        soul_dir: Path to the soul directory.
        active_features: Set of active feature flag names.
            If None, reads from feature_flags module.

    Returns:
        List of matching SoulOverlay instances.
    """
    d = _resolve_soul_dir(soul_dir)
    overlays_dir = d / "overlays"

    if not overlays_dir.exists():
        logger.debug("No overlays directory found at %s", overlays_dir)
        return []

    if active_features is None:
        active_features = _get_active_feature_flags()

    overlays: List[SoulOverlay] = []

    for yaml_file in sorted(overlays_dir.iterdir()):
        if yaml_file.suffix not in (".yaml", ".yml"):
            continue

        try:
            text = yaml_file.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                logger.warning("Overlay file %s is not a YAML mapping, skipping",
                               yaml_file)
                continue

            overlay = SoulOverlay(**data)

            if overlay.feature_flag in active_features:
                overlays.append(overlay)
                logger.info("Soul overlay loaded: %s (flag=%s)",
                            overlay.overlay_name, overlay.feature_flag)
            else:
                logger.debug("Soul overlay skipped (flag inactive): %s (flag=%s)",
                             overlay.overlay_name, overlay.feature_flag)

        except Exception as exc:
            logger.warning("Failed to load overlay %s: %s", yaml_file.name, exc)

    return overlays


def _get_active_feature_flags() -> Set[str]:
    """Read all currently active feature flags from the feature_flags module."""
    try:
        from src.core.feature_flags import get_all_flags
        all_flags = get_all_flags()
        return {name for name, value in all_flags.items() if value}
    except ImportError:
        try:
            from feature_flags import get_all_flags
            all_flags = get_all_flags()
            return {name for name, value in all_flags.items() if value}
        except ImportError:
            return set()


# ---------------------------------------------------------------------------
# Soul merge
# ---------------------------------------------------------------------------

def merge_soul(base: Soul, overlays: List[SoulOverlay]) -> Soul:
    """Merge a base Soul with one or more overlays.

    Merge rules (ADDITIVE ONLY):
    - mission, allegiance, version: NEVER overridden
    - risk_rules: base + overlay lists (appended, deduplicated by name)
    - tone_invariants: base + overlay lists (appended, deduplicated)
    - memory_ethics: base + overlay lists (appended, deduplicated)
    - autonomy_posture.allowed_autonomous: base + overlay (appended)
    - autonomy_posture.requires_approval: base + overlay (appended)
    - autonomy_posture.level, description: NEVER overridden
    - scheduling_boundaries description: overlay text appended
    - approval_rules: NEVER overridden
    - scheduling numeric limits: NEVER overridden

    Args:
        base: The validated base Soul instance.
        overlays: List of SoulOverlay instances to merge.

    Returns:
        A new Soul instance with merged governance rules.
    """
    if not overlays:
        return base

    # Start from copies of the base lists
    merged_risk_rules = list(base.risk_rules)
    merged_tone_invariants = list(base.tone_invariants)
    merged_memory_ethics = list(base.memory_ethics)
    merged_allowed_autonomous = list(base.autonomy_posture.allowed_autonomous)
    merged_requires_approval = list(base.autonomy_posture.requires_approval)
    merged_sched_description = base.scheduling_boundaries.description or ""

    # Track existing values to avoid exact duplicates
    existing_risk_names = {r.name for r in merged_risk_rules}
    existing_tone = set(merged_tone_invariants)
    existing_ethics = set(merged_memory_ethics)
    existing_allowed = set(merged_allowed_autonomous)
    existing_approval = set(merged_requires_approval)

    for overlay in overlays:
        # Append risk rules (skip exact name duplicates)
        for rule in overlay.risk_rules:
            if rule.name not in existing_risk_names:
                merged_risk_rules.append(rule)
                existing_risk_names.add(rule.name)

        # Append tone invariants (skip exact duplicates)
        for tone in overlay.tone_invariants:
            if tone not in existing_tone:
                merged_tone_invariants.append(tone)
                existing_tone.add(tone)

        # Append memory ethics (skip exact duplicates)
        for ethic in overlay.memory_ethics:
            if ethic not in existing_ethics:
                merged_memory_ethics.append(ethic)
                existing_ethics.add(ethic)

        # Append autonomy posture entries
        for action in overlay.autonomy_posture.allowed_autonomous:
            if action not in existing_allowed:
                merged_allowed_autonomous.append(action)
                existing_allowed.add(action)

        for action in overlay.autonomy_posture.requires_approval:
            if action not in existing_approval:
                merged_requires_approval.append(action)
                existing_approval.add(action)

        # Append scheduling description
        if overlay.scheduling_boundaries:
            merged_sched_description = (
                f"{merged_sched_description}\n\n[{overlay.overlay_name}] "
                f"{overlay.scheduling_boundaries}"
            ).strip()

    # Construct merged Soul — immutable fields preserved from base.
    # Serialize models to dicts before passing to Soul() to avoid Pydantic
    # class-identity mismatches when modules are imported via different paths
    # (e.g. "soul.store.RiskRule" vs "src.core.soul.store.RiskRule").
    merged_soul = Soul(
        version=base.version,
        mission=base.mission,
        allegiance=base.allegiance,
        autonomy_posture={
            "level": base.autonomy_posture.level,
            "description": base.autonomy_posture.description,
            "allowed_autonomous": merged_allowed_autonomous,
            "requires_approval": merged_requires_approval,
        },
        risk_rules=[r.model_dump() if hasattr(r, "model_dump") else {"name": r.name, "description": r.description, "enforced": r.enforced} for r in merged_risk_rules],
        approval_rules=base.approval_rules.model_dump() if hasattr(base.approval_rules, "model_dump") else base.approval_rules,
        tone_invariants=merged_tone_invariants,
        memory_ethics=merged_memory_ethics,
        scheduling_boundaries={
            "max_concurrent_jobs": base.scheduling_boundaries.max_concurrent_jobs,
            "max_job_duration_seconds": base.scheduling_boundaries.max_job_duration_seconds,
            "no_autonomous_irreversible": base.scheduling_boundaries.no_autonomous_irreversible,
            "require_ready_state": base.scheduling_boundaries.require_ready_state,
            "description": merged_sched_description,
        },
    )

    overlay_names = [o.overlay_name for o in overlays]
    logger.info("Soul merged with overlays: %s", overlay_names)
    return merged_soul


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------

def load_active_soul_with_overlays(
    soul_dir: Optional[str] = None,
    active_features: Optional[Set[str]] = None,
) -> Soul:
    """Load the active Soul and merge any active overlays.

    This is the primary entry point for loading a Soul with governance
    overlays applied.  The base Soul document stays pure on disk; overlays
    are separate files that are merged at load time.

    Args:
        soul_dir: Path to the soul directory.
        active_features: Set of active feature flag names.

    Returns:
        A merged Soul instance.
    """
    base_soul = load_active_soul(soul_dir)
    overlays = load_overlays(soul_dir, active_features)

    if overlays:
        return merge_soul(base_soul, overlays)
    return base_soul
