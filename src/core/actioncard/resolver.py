# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCardResolver — routes ActionCard button clicks to the correct approval subsystem.

Handles cross-channel resolution: if approved in Telegram, marks card resolved
in store and emits event for War Room to update. Creates receipts for audit trail.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from actioncard.store import ActionCardStore

logger = logging.getLogger(__name__)


class ActionCardResolver:
    """Routes ActionCard button clicks to the correct approval subsystem.

    Each source_system has a registered handler that performs the actual
    approval/denial action. The resolver handles:
    1. Card lookup and double-resolve guard
    2. Routing to the correct handler
    3. Marking the card resolved in the store
    4. Emitting cross-channel sync events
    5. Creating audit receipts
    """

    def __init__(
        self,
        card_store: ActionCardStore,
        event_bus=None,
        receipt_service=None,
    ):
        self._store = card_store
        self._event_bus = event_bus
        self._receipt_service = receipt_service
        self._handlers: Dict[str, Callable] = {}

    def register_handler(self, source_system: str, handler: Callable) -> None:
        """Register a handler for a source_system.

        Handler signature: handler(source_item_id: str, button_id: str) -> dict
        Must return: {"status": "approved"|"denied"|"error", "message": str}
        """
        self._handlers[source_system] = handler
        logger.info("ActionCard resolver: registered handler for '%s'", source_system)

    def resolve(self, card_id: str, button_id: str, channel: str) -> Dict[str, Any]:
        """Resolve an ActionCard button click.

        Args:
            card_id: Full card ID or prefix (for Telegram)
            button_id: The button that was clicked (e.g., "approve", "deny")
            channel: Where the click came from ("telegram", "warroom", "api")

        Returns:
            {"status": "approved"|"denied"|"error", "message": str}
        """
        # Lookup card (supports prefix for Telegram)
        card = self._store.get(card_id)
        if card is None:
            card = self._store.get_by_prefix(card_id)
        if card is None:
            return {"status": "error", "message": "Card not found"}

        if card.resolved:
            return {
                "status": "error",
                "message": f"Already resolved ({card.resolved_action}) via {card.resolved_channel}",
            }

        if card.is_expired():
            return {"status": "error", "message": "Card expired"}

        # Route to handler
        handler = self._handlers.get(card.source_system)
        if handler is None:
            logger.warning("No handler for source_system='%s'", card.source_system)
            return {
                "status": "error",
                "message": f"No handler for {card.source_system}",
            }

        try:
            result = handler(card.source_item_id, button_id)
        except Exception as exc:
            logger.error("ActionCard handler error for %s: %s", card.source_system, exc)
            return {"status": "error", "message": str(exc)}

        # Mark resolved in store
        self._store.resolve(card.card_id, button_id, channel)

        # Emit cross-channel sync event
        self._emit_resolution_event(card, button_id, channel, result)

        # Create audit receipt
        self._create_receipt(card, button_id, channel, result)

        logger.info(
            "ActionCard resolved: card=%s button=%s channel=%s system=%s item=%s",
            card.short_id(), button_id, channel, card.source_system, card.source_item_id,
        )

        return result

    def _emit_resolution_event(
        self, card, button_id: str, channel: str, result: Dict[str, Any]
    ) -> None:
        """Emit an event for cross-channel sync."""
        if not self._event_bus:
            return
        try:
            from event_bus import Event
            self._event_bus.publish_sync(Event(
                type="actioncard_resolved",
                payload={
                    "card_id": card.card_id,
                    "button_id": button_id,
                    "channel": channel,
                    "source_system": card.source_system,
                    "source_item_id": card.source_item_id,
                    "result": result,
                    "telegram_message_id": card.telegram_message_id,
                },
            ))
        except Exception as exc:
            logger.warning("Failed to emit actioncard_resolved event: %s", exc)

    def _create_receipt(
        self, card, button_id: str, channel: str, result: Dict[str, Any]
    ) -> None:
        """Create an audit receipt for the resolution."""
        if not self._receipt_service:
            return
        try:
            from receipts import Receipt, ActionType, ReceiptStatus
            receipt = Receipt(
                action_type=ActionType.ACTION_CARD_RESOLVED.value,
                action_name=f"actioncard.{card.source_system}.{button_id}",
                inputs={
                    "card_id": card.card_id,
                    "source_system": card.source_system,
                    "source_item_id": card.source_item_id,
                    "button_id": button_id,
                    "channel": channel,
                },
                outputs=result,
                status=ReceiptStatus.SUCCESS.value,
                quest_id=card.quest_id,
                metadata={"card_type": card.card_type},
            )
            self._receipt_service.create(receipt)
        except Exception as exc:
            logger.warning("Failed to create actioncard receipt: %s", exc)
