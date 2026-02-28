# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCard and ActionButton models — channel-agnostic interactive elements.

Each channel renderer translates ActionCards to native controls:
- Telegram: InlineKeyboardMarkup via send_message_with_keyboard()
- War Room: React <ActionCard /> component via WebSocket event
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ActionCardType(str, Enum):
    """Categories of action cards."""
    APPROVAL = "approval"
    CONFIRMATION = "confirmation"
    CHOICE = "choice"
    INFO = "info"


class ActionButtonStyle(str, Enum):
    """Visual style for buttons."""
    PRIMARY = "primary"
    DANGER = "danger"
    SECONDARY = "secondary"


@dataclass
class ActionButton:
    """A single interactive button on an ActionCard."""
    id: str
    label: str
    style: str = ActionButtonStyle.SECONDARY.value
    callback_data: str = ""
    requires_confirmation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "style": self.style,
            "callback_data": self.callback_data,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass
class ActionCard:
    """A channel-agnostic interactive card.

    Persisted in SQLite via ActionCardStore. Dispatched to active channels
    via EventBus. Resolved when a user clicks a button (from any channel).
    """
    card_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    card_type: str = ActionCardType.INFO.value
    title: str = ""
    description: str = ""
    source_system: str = ""
    source_item_id: str = ""
    buttons: List[ActionButton] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    quest_id: Optional[str] = None
    resolved: bool = False
    resolved_action: Optional[str] = None
    resolved_at: Optional[float] = None
    resolved_channel: Optional[str] = None

    # Telegram message_id for editing on resolution (set after send)
    telegram_message_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON/WebSocket transport."""
        return {
            "card_id": self.card_id,
            "card_type": self.card_type,
            "title": self.title,
            "description": self.description,
            "source_system": self.source_system,
            "source_item_id": self.source_item_id,
            "buttons": [b.to_dict() for b in self.buttons],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "quest_id": self.quest_id,
            "resolved": self.resolved,
            "resolved_action": self.resolved_action,
            "resolved_at": self.resolved_at,
            "resolved_channel": self.resolved_channel,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionCard:
        """Deserialize from dictionary."""
        buttons_data = data.pop("buttons", [])
        buttons = [
            ActionButton(**b) if isinstance(b, dict) else b
            for b in buttons_data
        ]
        return cls(buttons=buttons, **data)

    def short_id(self) -> str:
        """First 8 chars of card_id — used for Telegram callback_data."""
        return self.card_id[:8]

    def to_telegram_keyboard(self) -> dict:
        """Convert to Telegram InlineKeyboardMarkup.

        Callback data format: ac:{short_id}:{button_id}
        Must fit within Telegram's 64-byte callback_data limit.
        """
        rows = []
        for button in self.buttons:
            callback = f"ac:{self.short_id()}:{button.id}"
            if len(callback.encode("utf-8")) > 64:
                callback = callback[:64]
            rows.append([{
                "text": button.label,
                "callback_data": callback,
            }])
        return {"inline_keyboard": rows}

    def to_telegram_text(self) -> str:
        """Format card as Telegram message text (Markdown)."""
        lines = [f"*{self.title}*"]
        if self.description:
            lines.append(self.description)
        if self.source_system:
            lines.append(f"_Source: {self.source_system}_")
        return "\n\n".join(lines)

    def to_event_bus(self):
        """Convert to EventBus Event for broadcasting."""
        from event_bus import Event
        return Event(
            type="actioncard_presented",
            payload=self.to_dict(),
            timestamp=self.created_at,
        )

    def is_expired(self) -> bool:
        """Check if this card has passed its expiry time."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
