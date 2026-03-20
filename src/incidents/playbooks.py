# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Playbook Registry — loads, validates, and serves incident response playbooks.

Follows the same pattern as src/core/soul/templates.py:
- YAML files with _playbook_metadata key (stripped before use)
- In-memory cache with invalidation
- Variant overlays extend base playbooks via insert_after

Playbook YAML format:
    _playbook_metadata:
      name: governance-breach-kill-switch
      display_name: "Kill Switch Activation Response"
      ...

    trigger:
      receipt_types: [kill_switch_issued]
      ...

    steps:
      - step: 1
        title: "Acknowledge incident"
        description: "..."
        action_type: MANUAL
        sla_minutes: 5
        decision_points: []
        actions: []
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("lancelot.incidents.playbooks")


@dataclass
class PlaybookMetadata:
    """Metadata extracted from _playbook_metadata key."""
    name: str
    display_name: str
    description: str = ""
    category: str = ""
    severity_default: str = "HIGH"
    industry: str = "general"
    version: str = "1.0"
    author: str = "Lancelot"
    tags: List[str] = field(default_factory=list)


@dataclass
class DecisionPoint:
    """A branching decision within a playbook step."""
    condition: str
    action: str


@dataclass
class PlaybookStep:
    """A single step in a playbook's response sequence."""
    step: int
    title: str
    description: str
    action_type: str = "MANUAL"  # MANUAL, AUTOMATED, VERIFY
    sla_minutes: int = 15
    decision_points: List[Dict[str, str]] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)


@dataclass
class Playbook:
    """A complete incident response playbook."""
    metadata: PlaybookMetadata
    steps: List[PlaybookStep]
    trigger: Dict[str, Any] = field(default_factory=dict)
    paging: Dict[str, Any] = field(default_factory=dict)
    raw_yaml: Dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    # Variant extension tracking
    extends: Optional[str] = None
    variant_steps: List[PlaybookStep] = field(default_factory=list)


# ── Module-level cache ────────────────────────────────────────────

_playbook_cache: Dict[str, Playbook] = {}
_cache_loaded: bool = False
_default_dir: Optional[str] = None


def load_playbooks(playbooks_dir: Optional[str] = None) -> Dict[str, Playbook]:
    """Load all playbooks from the directory tree.

    Loads base playbooks first, then applies variant overlays.
    Invalid playbooks are logged and skipped.
    """
    global _playbook_cache, _cache_loaded, _default_dir

    if playbooks_dir:
        _default_dir = playbooks_dir

    search_dir = playbooks_dir or _default_dir
    if not search_dir or not os.path.isdir(search_dir):
        logger.warning("Playbooks directory not found: %s", search_dir)
        return {}

    base_playbooks: Dict[str, Playbook] = {}
    variant_playbooks: List[Playbook] = []

    # Walk directory tree
    for root, dirs, files in os.walk(search_dir):
        for fname in sorted(files):
            if not fname.endswith((".yaml", ".yml")):
                continue

            path = os.path.join(root, fname)
            try:
                playbook = _load_single(path)
                if playbook is None:
                    continue

                if playbook.extends:
                    variant_playbooks.append(playbook)
                else:
                    base_playbooks[playbook.metadata.name] = playbook
                    logger.debug("Loaded playbook: %s", playbook.metadata.name)
            except Exception as exc:
                logger.warning("Failed to load playbook %s: %s", path, exc)

    # Apply variant overlays
    for variant in variant_playbooks:
        base_name = variant.extends
        if base_name not in base_playbooks:
            logger.warning(
                "Variant %s extends unknown base %s — skipped",
                variant.metadata.name, base_name,
            )
            continue

        merged = _apply_variant(base_playbooks[base_name], variant)
        base_playbooks[variant.metadata.name] = merged
        logger.debug("Applied variant: %s extends %s",
                      variant.metadata.name, base_name)

    _playbook_cache = base_playbooks
    _cache_loaded = True
    logger.info("Playbook registry: %d playbooks loaded", len(_playbook_cache))
    return _playbook_cache


def _load_single(path: str) -> Optional[Playbook]:
    """Load and validate a single playbook YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        logger.warning("Invalid playbook (not a dict): %s", path)
        return None

    # Extract and strip metadata
    meta_raw = raw.pop("_playbook_metadata", None)
    if meta_raw is None:
        logger.warning("Missing _playbook_metadata in %s", path)
        return None

    try:
        metadata = PlaybookMetadata(**{
            k: v for k, v in meta_raw.items()
            if k in PlaybookMetadata.__dataclass_fields__
        })
    except TypeError as exc:
        logger.warning("Invalid metadata in %s: %s", path, exc)
        return None

    if not metadata.name:
        logger.warning("Playbook missing name in %s", path)
        return None

    # Parse steps
    steps_raw = raw.get("steps", [])
    steps = []
    for s in steps_raw:
        try:
            step = PlaybookStep(**{
                k: v for k, v in s.items()
                if k in PlaybookStep.__dataclass_fields__
            })
            steps.append(step)
        except TypeError as exc:
            logger.warning("Invalid step in %s: %s", path, exc)

    # Validate: must have at least one step
    if not steps and not raw.get("extends"):
        logger.warning("Playbook %s has no steps", metadata.name)
        return None

    extends = raw.get("extends")
    variant_steps_raw = raw.get("variant_steps", [])
    variant_steps = []
    for vs in variant_steps_raw:
        try:
            variant_steps.append(PlaybookStep(**{
                k: v for k, v in vs.items()
                if k in PlaybookStep.__dataclass_fields__
            }))
        except TypeError:
            pass

    return Playbook(
        metadata=metadata,
        steps=steps,
        trigger=raw.get("trigger", {}),
        paging=raw.get("paging", {}),
        raw_yaml=raw,
        file_path=path,
        extends=extends,
        variant_steps=variant_steps,
    )


def _apply_variant(base: Playbook, variant: Playbook) -> Playbook:
    """Apply a variant overlay to a base playbook.

    Variant steps use insert_after to specify where they go.
    Strategy: append-after with explicit insert_after field.
    """
    # Start with a copy of base steps
    merged_steps = list(base.steps)

    for vs in variant.variant_steps:
        # Find the insert_after step number from the raw YAML
        insert_after = None
        for vs_raw in variant.raw_yaml.get("variant_steps", []):
            if vs_raw.get("step") == vs.step:
                insert_after = vs_raw.get("insert_after")
                break

        if insert_after is not None:
            # Find the index of the step to insert after
            idx = None
            for i, s in enumerate(merged_steps):
                if s.step == insert_after:
                    idx = i + 1
                    break
            if idx is not None:
                merged_steps.insert(idx, vs)
            else:
                merged_steps.append(vs)
        else:
            # No insert_after — append at end
            merged_steps.append(vs)

    # Renumber steps sequentially
    for i, step in enumerate(merged_steps):
        step.step = i + 1

    # Merge paging (variant overrides base)
    merged_paging = {**base.paging, **variant.paging}

    return Playbook(
        metadata=variant.metadata,
        steps=merged_steps,
        trigger=base.trigger,
        paging=merged_paging,
        raw_yaml=variant.raw_yaml,
        file_path=variant.file_path,
        extends=variant.extends,
        variant_steps=variant.variant_steps,
    )


def get_playbook(name: str) -> Optional[Playbook]:
    """Get a playbook by name from the cache."""
    if not _cache_loaded:
        load_playbooks()
    return _playbook_cache.get(name)


def list_playbook_metadata(
    category: Optional[str] = None,
    industry: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List playbook metadata with optional filters."""
    if not _cache_loaded:
        load_playbooks()

    results = []
    for name, pb in _playbook_cache.items():
        if category and pb.metadata.category != category:
            continue
        if industry and pb.metadata.industry != industry:
            continue
        results.append({
            "name": pb.metadata.name,
            "display_name": pb.metadata.display_name,
            "description": pb.metadata.description,
            "category": pb.metadata.category,
            "severity_default": pb.metadata.severity_default,
            "industry": pb.metadata.industry,
            "version": pb.metadata.version,
            "tags": pb.metadata.tags,
            "step_count": len(pb.steps),
            "extends": pb.extends,
        })

    return sorted(results, key=lambda x: (x["category"], x["name"]))


def invalidate_cache() -> None:
    """Clear the playbook cache. Next access will reload from disk."""
    global _playbook_cache, _cache_loaded
    _playbook_cache = {}
    _cache_loaded = False
    logger.info("Playbook cache invalidated")
