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

_TELEGRAM_TEXT_LIMIT = 4096
_TELEGRAM_SAFE_CHUNK = 3800

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


def _secret_or_env(key: str) -> str:
    """Resolve Telegram credentials the same way the gateway bot does."""
    for module_name in ("secret_cache", "src.core.secret_cache"):
        try:
            secret_cache = __import__(module_name, fromlist=["get"])
            value = secret_cache.get(key, "")
            if value:
                return value
        except Exception as exc:
            logger.debug("telegram_send: secret cache lookup failed for %s via %s: %s", key, module_name, exc)

    value = os.environ.get(key, "")
    if value:
        return value

    vault_key = {
        "LANCELOT_TELEGRAM_TOKEN": "system.telegram_token",
        "LANCELOT_TELEGRAM_CHAT_ID": "system.telegram_chat_id",
    }.get(key)
    if not vault_key:
        return ""

    for vault_module, kwargs in (
        ("connectors.vault", {"config_path": "config/vault.yaml"}),
        ("src.connectors.vault", {}),
    ):
        try:
            module = __import__(vault_module, fromlist=["CredentialVault"])
            vault = module.CredentialVault(**kwargs)
            if vault.exists(vault_key):
                return vault.retrieve(vault_key)
        except Exception as exc:
            logger.debug("telegram_send: vault lookup failed for %s via %s: %s", key, vault_module, exc)
    return ""


def _split_telegram_text(message: str, limit: int = _TELEGRAM_SAFE_CHUNK) -> list[str]:
    """Split long Telegram messages on paragraph/line boundaries when possible."""
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    remaining = message
    while len(remaining) > limit:
        split_at = max(
            remaining.rfind("\n\n", 0, limit),
            remaining.rfind("\n", 0, limit),
            remaining.rfind(" ", 0, limit),
        )
        if split_at < max(1, int(limit * 0.5)):
            split_at = limit
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


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
    return send_text(message, chat_id_override)


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
    except ImportError as exc:
        logger.debug("telegram_send: gateway TelegramBot document helper unavailable: %s", exc)

    # Fallback: direct API call
    token = _secret_or_env("LANCELOT_TELEGRAM_TOKEN")
    chat_id = chat_id_override or _secret_or_env("LANCELOT_TELEGRAM_CHAT_ID")

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


def send_text(message: str, chat_id_override: str = None) -> Dict[str, Any]:
    """Send a text message via Telegram."""
    # Apply Telegram sanitization (table conversion, JSON stripping, markdown fixes)
    try:
        from integrations.telegram_bot import TelegramBot
        message = TelegramBot.sanitize_for_telegram(message)
    except ImportError as exc:
        logger.debug("telegram_send: TelegramBot sanitizer unavailable: %s", exc)

    # Try gateway TelegramBot instance
    try:
        import gateway
        if hasattr(gateway, "telegram_bot") and gateway.telegram_bot is not None:
            target_chat = chat_id_override or gateway.telegram_bot.chat_id
            gateway.telegram_bot.send_message(message, chat_id=target_chat)
            # Flag that we already sent via telegram_send (prevents duplicate in telegram_bot)
            if (
                hasattr(gateway, 'main_orchestrator')
                and gateway.main_orchestrator
                and hasattr(gateway.main_orchestrator, "mark_telegram_delivery_handled")
            ):
                gateway.main_orchestrator.mark_telegram_delivery_handled()
            return {
                "status": "sent",
                "type": "message",
                "chat_id": target_chat,
                "message_length": len(message),
            }
    except ImportError as exc:
        logger.debug("telegram_send: gateway TelegramBot instance unavailable: %s", exc)

    # Fallback: direct API call
    token = _secret_or_env("LANCELOT_TELEGRAM_TOKEN")
    chat_id = chat_id_override or _secret_or_env("LANCELOT_TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return {
            "status": "error",
            "error": "Telegram not configured. Set LANCELOT_TELEGRAM_TOKEN and LANCELOT_TELEGRAM_CHAT_ID.",
        }

    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        response = urlopen(req, timeout=15)
        return json.loads(response.read().decode("utf-8"))

    try:
        chunks = _split_telegram_text(message)
        for chunk in chunks:
            payload_obj = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }
            try:
                result = _post(payload_obj)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code == 400 and "parse" in body.lower():
                    fallback_payload = dict(payload_obj)
                    fallback_payload.pop("parse_mode", None)
                    result = _post(fallback_payload)
                else:
                    return {"status": "error", "error": body or f"HTTP Error {exc.code}: {exc.reason}"}
            if not result.get("ok"):
                return {
                    "status": "error",
                    "error": result.get("description", "Unknown Telegram API error"),
                }
        if chunks:
            return {
                "status": "sent",
                "type": "message",
                "chat_id": chat_id,
                "message_length": len(message),
                "chunk_count": len(chunks),
            }
        return {"status": "error", "error": "Empty Telegram message"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


_send_text = send_text
