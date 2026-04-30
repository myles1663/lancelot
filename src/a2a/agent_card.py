# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Agent Card Generator — Dynamic Agent Card from active Soul.

Generates Lancelot's Agent Card at /.well-known/agent.json from the active
Soul configuration. Advertises only a2a_visible skills. Regenerates on
Soul update. Includes optional governance_declaration extension.

Public API:
    generate_agent_card(soul, base_url, auth_scheme) → AgentCard
    get_cached_card() → AgentCard | None
    invalidate_card() → None
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from src.a2a.types import AgentCard, AgentCardSkill

logger = logging.getLogger(__name__)

# Module-level cache
_cached_card: Optional[AgentCard] = None
_cached_soul_hash: Optional[str] = None


def _soul_hash(soul: Any) -> str:
    """Compute a hash of Soul fields relevant to the Agent Card."""
    import json
    relevant = {
        "version": getattr(soul, "version", ""),
        "mission": getattr(soul, "mission", ""),
        "inbound_a2a": None,
    }
    inbound = getattr(soul, "inbound_a2a_permissions", None)
    if inbound:
        relevant["inbound_a2a"] = {
            "allow_inbound": inbound.allow_inbound,
            "skill_filter": list(inbound.skill_filter),
        }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


def generate_agent_card(
    soul: Any,
    base_url: str = "http://localhost:8000",
    auth_scheme: Optional[Dict[str, Any]] = None,
    include_governance_declaration: bool = True,
) -> AgentCard:
    """Generate Lancelot's A2A Agent Card from the active Soul.

    Args:
        soul: Active Soul instance.
        base_url: Public URL of this Lancelot instance.
        auth_scheme: Authentication scheme to advertise.
        include_governance_declaration: Include governance extension field.

    Returns:
        AgentCard ready for JSON serialization.
    """
    global _cached_card, _cached_soul_hash

    current_hash = _soul_hash(soul)
    if _cached_card and _cached_soul_hash == current_hash:
        return _cached_card

    # Build skills from Soul — only a2a_visible capabilities
    skills = _extract_a2a_skills(soul)

    # Build authentication
    authentication = auth_scheme or {"type": "bearer_token"}

    # Build governance declaration
    governance = None
    if include_governance_declaration:
        governance = {
            "soul_version_hash": hashlib.sha256(
                getattr(soul, "version", "").encode()
            ).hexdigest()[:16],
            "governance_framework": "lancelot",
            "receipt_system": True,
        }

    card = AgentCard(
        name="Lancelot Governed Agent",
        description=getattr(soul, "mission", "A governed autonomous system"),
        url=base_url,
        version=getattr(soul, "version", "unknown"),
        a2a_protocol_version="0.2",
        skills=skills,
        authentication=authentication,
        capabilities={
            "streaming": True,
            "pushNotifications": False,
        },
        governance_declaration=governance,
    )

    _cached_card = card
    _cached_soul_hash = current_hash
    logger.info("Agent Card generated (soul_hash=%s, skills=%d)", current_hash, len(skills))
    return card


def _extract_a2a_skills(soul: Any) -> List[AgentCardSkill]:
    """Extract skills to advertise in the Agent Card.

    Uses Soul's inbound_a2a_permissions.skill_filter if present.
    Falls back to generic capabilities.
    """
    inbound = getattr(soul, "inbound_a2a_permissions", None)
    skill_filter = set(inbound.skill_filter) if inbound and inbound.skill_filter else None

    # Default skills based on Soul capabilities
    all_skills = [
        AgentCardSkill(id="chat", name="Chat", description="General conversation and task execution"),
        AgentCardSkill(id="data_analysis", name="Data Analysis", description="Analyze data and generate insights"),
        AgentCardSkill(id="report_generation", name="Report Generation", description="Generate structured reports"),
        AgentCardSkill(id="code_review", name="Code Review", description="Review and analyze code"),
        AgentCardSkill(id="research", name="Research", description="Research topics and synthesize findings"),
    ]

    if skill_filter:
        return [s for s in all_skills if s.id in skill_filter]
    return all_skills


def get_cached_card() -> Optional[AgentCard]:
    """Return the cached Agent Card, or None if not generated."""
    return _cached_card


def invalidate_card() -> None:
    """Invalidate the cached Agent Card. Forces regeneration on next request."""
    global _cached_card, _cached_soul_hash
    _cached_card = None
    _cached_soul_hash = None
    logger.info("Agent Card cache invalidated")
