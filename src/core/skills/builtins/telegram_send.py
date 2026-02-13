"""
Built-in skill: telegram_send — send messages via Telegram Bot API.

Sends messages to the configured Telegram chat using the TelegramBot
integration. Uses the bot token and chat_id from environment variables.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "telegram_send",
    "version": "1.0.0",
    "description": "Send messages via Telegram to the configured chat",
    "risk": "MEDIUM",
    "permissions": ["telegram.write"],
    "inputs": [
        {"name": "message", "type": "string", "required": True,
         "description": "The message text to send"},
        {"name": "chat_id", "type": "string", "required": False,
         "description": "Override chat ID (uses default if omitted)"},
    ],
}


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Send a Telegram message.

    Accesses the TelegramBot instance via the gateway module or
    falls back to a direct HTTP call using env vars.

    Args:
        context: SkillContext
        inputs: Dict with 'message' and optional 'chat_id'

    Returns:
        Dict with 'status', 'chat_id', 'message_length'
    """
    message = inputs.get("message", "")
    chat_id_override = inputs.get("chat_id", None)

    if not message:
        raise ValueError("Missing required input: 'message'")

    # Try to use the gateway's TelegramBot instance
    try:
        import gateway
        if hasattr(gateway, "telegram_bot") and gateway.telegram_bot is not None:
            target_chat = chat_id_override or gateway.telegram_bot.chat_id
            gateway.telegram_bot.send_message(message, chat_id=target_chat)
            return {
                "status": "sent",
                "chat_id": target_chat,
                "message_length": len(message),
            }
    except ImportError:
        pass

    # Fallback: direct API call using env vars
    token = os.environ.get("LANCELOT_TELEGRAM_TOKEN", "")
    chat_id = chat_id_override or os.environ.get("LANCELOT_TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return {
            "status": "error",
            "error": "Telegram not configured. Set LANCELOT_TELEGRAM_TOKEN and LANCELOT_TELEGRAM_CHAT_ID.",
        }

    import json
    from urllib.request import Request, urlopen

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        response = urlopen(req, timeout=15)
        result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            return {
                "status": "sent",
                "chat_id": chat_id,
                "message_length": len(message),
            }
        return {
            "status": "error",
            "error": result.get("description", "Unknown Telegram API error"),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
