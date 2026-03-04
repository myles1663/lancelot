# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCardFactory — creates ActionCards from approval system events.

Each approval subsystem (soul, skills, scheduler, governance/sentry)
has a dedicated builder method that constructs the appropriate card
and saves it to the ActionCardStore.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from actioncard.models import (
    ActionButton,
    ActionButtonStyle,
    ActionCard,
    ActionCardType,
)
from actioncard.store import ActionCardStore

logger = logging.getLogger(__name__)

# Default expiry: 24 hours for approval cards
_DEFAULT_EXPIRY_SECONDS = 86400


class ActionCardFactory:
    """Creates ActionCards from approval system events."""

    def __init__(self, card_store: ActionCardStore, event_bus=None):
        self._store = card_store
        self._event_bus = event_bus

    def _emit_presented(self, card: ActionCard) -> None:
        """Emit actioncard_presented event for cross-channel delivery."""
        if not self._event_bus:
            return
        try:
            from event_bus import Event
            self._event_bus.publish_sync(Event(
                type="actioncard_presented",
                payload=card.to_dict(),
            ))
        except Exception as exc:
            logger.warning("Failed to emit actioncard_presented: %s", exc)

    def from_sentry_request(
        self,
        req_id: str,
        tool_name: str,
        params: Dict[str, Any],
        quest_id: Optional[str] = None,
    ) -> ActionCard:
        """Build ActionCard for MCP Sentry T3 action approval."""
        params_summary = str(params)[:200]
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"T3 Action: {tool_name}",
            description=f"A high-risk action requires your approval.\n\nParams: {params_summary}",
            source_system="governance",
            source_item_id=req_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            quest_id=quest_id,
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "sentry_t3", "tool_name": tool_name},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: sentry T3 %s (card=%s)", req_id, card.short_id())
        return card

    def from_soul_proposal(
        self,
        proposal_id: str,
        version: str,
        diff_summary: List[str],
    ) -> ActionCard:
        """Build ActionCard for soul amendment approval."""
        diff_text = "\n".join(f"- {d}" for d in diff_summary[:5])
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Soul Amendment: {version}",
            description=f"A soul amendment proposal requires review.\n\n{diff_text}",
            source_system="soul",
            source_item_id=proposal_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "soul_amendment", "version": version},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: soul proposal %s (card=%s)", proposal_id, card.short_id())
        return card

    def from_skill_proposal(
        self,
        proposal_id: str,
        name: str,
        description: str,
    ) -> ActionCard:
        """Build ActionCard for skill proposal approval."""
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Skill Proposal: {name}",
            description=f"{description[:300]}",
            source_system="skills",
            source_item_id=proposal_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="reject", label="Reject",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "skill_proposal", "skill_name": name},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: skill proposal %s (card=%s)", proposal_id, card.short_id())
        return card

    def from_scheduler_approval(
        self,
        job_id: str,
        job_name: str,
        skill: str,
    ) -> ActionCard:
        """Build ActionCard for scheduler job approval."""
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Scheduled Job: {job_name}",
            description=f"Job '{job_name}' requires approval to execute skill '{skill}'.",
            source_system="scheduler",
            source_item_id=job_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "scheduler_job", "skill": skill},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: scheduler job %s (card=%s)", job_id, card.short_id())
        return card

    def create_custom(
        self,
        card_type: str,
        title: str,
        description: str,
        buttons: List[ActionButton],
        source_system: str = "",
        source_item_id: str = "",
        quest_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_seconds: int = _DEFAULT_EXPIRY_SECONDS,
    ) -> ActionCard:
        """Build a custom ActionCard for ad-hoc interactive prompts."""
        card = ActionCard(
            card_type=card_type,
            title=title,
            description=description,
            source_system=source_system,
            source_item_id=source_item_id,
            buttons=buttons,
            quest_id=quest_id,
            metadata=metadata or {},
            expires_at=time.time() + expires_seconds,
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: custom %s (card=%s)", card_type, card.short_id())
        return card
