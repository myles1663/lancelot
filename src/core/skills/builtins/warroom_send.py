"""
Built-in skill: warroom_send — push messages to the War Room dashboard.

Broadcasts messages via the EventBus → WebSocket pipeline so they appear
as toast notifications in the War Room UI. Works from any channel.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "warroom_send",
    "version": "1.0.0",
    "description": "Push a notification message to the War Room dashboard",
    "risk": "LOW",
    "permissions": ["warroom.write"],
    "inputs": [
        {"name": "message", "type": "string", "required": True,
         "description": "The notification message to display in the War Room"},
        {"name": "priority", "type": "string", "required": False,
         "description": "Priority level: 'normal' or 'high' (default: normal)"},
    ],
}


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Push a notification to the War Room via EventBus → WebSocket.

    Args:
        context: SkillContext
        inputs: Dict with 'message' and optional 'priority'

    Returns:
        Dict with 'status' and 'active_clients' count
    """
    message = inputs.get("message", "")
    priority = inputs.get("priority", "normal")

    if not message:
        raise ValueError("Missing required input: 'message'")

    if priority not in ("normal", "high"):
        priority = "normal"

    try:
        from event_bus import event_bus, Event
        from warroom_ws import connection_manager

        event = Event(
            type="warroom_notification",
            payload={
                "message": message,
                "priority": priority,
                "source": "lancelot",
            },
        )
        event_bus.publish_sync(event)

        active = connection_manager.active_count
        logger.info("warroom_send: broadcast to %d clients (priority=%s)", active, priority)

        return {
            "status": "broadcast",
            "active_clients": active,
            "message_length": len(message),
        }
    except Exception as e:
        logger.error("warroom_send failed: %s", e)
        return {
            "status": "error",
            "error": str(e),
        }
