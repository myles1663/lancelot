"""
Built-in skill: telegram_send — send messages and files via Telegram Bot API.

Sends messages and documents to the configured Telegram chat using the TelegramBot
integration. Uses the bot token and chat_id from environment variables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Default workspace root (must match repo_writer)
DEFAULT_WORKSPACE = os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/data")

# Skill manifest metadata
MANIFEST = {
    "name": "telegram_send",
    "version": "2.0.0",
    "description": "Send messages and files via Telegram to the configured chat",
    "risk": "MEDIUM",
    "permissions": ["telegram.write"],
    "inputs": [
        {"name": "message", "type": "string", "required": False,
         "description": "The message text to send (required if no file_path)"},
        {"name": "file_path", "type": "string", "required": False,
         "description": "Workspace-relative path of a file to send as a document attachment"},
        {"name": "caption", "type": "string", "required": False,
         "description": "Caption for the file attachment (optional, max 1024 chars)"},
        {"name": "chat_id", "type": "string", "required": False,
         "description": "Override chat ID (uses default if omitted)"},
    ],
}


def _resolve_workspace_path(rel_path: str) -> Path:
    """Resolve a relative path within the workspace, preventing path traversal."""
    ws = Path(DEFAULT_WORKSPACE).resolve()
    target = (ws / rel_path).resolve()
    if not str(target).startswith(str(ws)):
        raise ValueError(f"Path traversal blocked: '{rel_path}' escapes workspace")
    return target


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Send a Telegram message or file.

    Args:
        context: SkillContext
        inputs: Dict with 'message' and/or 'file_path', optional 'caption', 'chat_id'

    Returns:
        Dict with 'status', 'chat_id', and delivery details
    """
    message = inputs.get("message", "")
    file_path = inputs.get("file_path", "")
    caption = inputs.get("caption", "")
    chat_id_override = inputs.get("chat_id", None)

    if not message and not file_path:
        raise ValueError("Must provide either 'message' or 'file_path' (or both)")

    # --- File delivery ---
    if file_path:
        return _send_file(file_path, caption or message, chat_id_override)

    # --- Text message ---
    return _send_text(message, chat_id_override)


def _send_file(file_path: str, caption: str, chat_id_override: str = None) -> Dict[str, Any]:
    """Send a workspace file as a Telegram document."""
    # Resolve and validate the file path
    resolved = _resolve_workspace_path(file_path)
    if not resolved.exists():
        return {"status": "error", "error": f"File not found: {file_path}"}
    if not resolved.is_file():
        return {"status": "error", "error": f"Not a file: {file_path}"}

    file_bytes = resolved.read_bytes()
    filename = resolved.name

    # Try gateway TelegramBot instance
    try:
        import gateway
        if hasattr(gateway, "telegram_bot") and gateway.telegram_bot is not None:
            target_chat = chat_id_override or gateway.telegram_bot.chat_id
            ok = gateway.telegram_bot.send_document(
                file_bytes, filename, chat_id=target_chat, caption=caption
            )
            if ok:
                return {
                    "status": "sent",
                    "type": "document",
                    "chat_id": target_chat,
                    "filename": filename,
                    "bytes": len(file_bytes),
                }
            return {"status": "error", "error": "send_document failed (check logs)"}
    except ImportError:
        pass

    # Fallback: direct API call
    token = os.environ.get("LANCELOT_TELEGRAM_TOKEN", "")
    chat_id = chat_id_override or os.environ.get("LANCELOT_TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return {
            "status": "error",
            "error": "Telegram not configured. Set LANCELOT_TELEGRAM_TOKEN and LANCELOT_TELEGRAM_CHAT_ID.",
        }

    import requests
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        resp = requests.post(
            url, data=data,
            files={"document": (filename, file_bytes, "application/octet-stream")},
            timeout=60,
        )
        if resp.ok:
            return {
                "status": "sent",
                "type": "document",
                "chat_id": chat_id,
                "filename": filename,
                "bytes": len(file_bytes),
            }
        return {"status": "error", "error": resp.text[:200]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _send_text(message: str, chat_id_override: str = None) -> Dict[str, Any]:
    """Send a text message via Telegram."""
    # Try gateway TelegramBot instance
    try:
        import gateway
        if hasattr(gateway, "telegram_bot") and gateway.telegram_bot is not None:
            target_chat = chat_id_override or gateway.telegram_bot.chat_id
            gateway.telegram_bot.send_message(message, chat_id=target_chat)
            return {
                "status": "sent",
                "type": "message",
                "chat_id": target_chat,
                "message_length": len(message),
            }
    except ImportError:
        pass

    # Fallback: direct API call
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
                "type": "message",
                "chat_id": chat_id,
                "message_length": len(message),
            }
        return {
            "status": "error",
            "error": result.get("description", "Unknown Telegram API error"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
