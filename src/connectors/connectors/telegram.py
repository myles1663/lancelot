"""
Telegram Connector — Telegram Bot API integration.

Produces HTTP request specs for Telegram Bot API operations.
Never makes network calls directly.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class TelegramConnector(ConnectorBase):
    """Telegram Bot API connector with governed read and write operations."""

    TG_API_BASE = "https://api.telegram.org/bot"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="telegram",
            name="Telegram Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Telegram Bot API for sending and receiving messages, voice notes, photos, and documents",
            target_domains=["api.telegram.org"],
            required_credentials=[
                CredentialSpec(
                    name="telegram_bot_token",
                    type="api_key",
                    vault_key="telegram.bot_token",
                ),
                CredentialSpec(
                    name="telegram_chat_id",
                    type="config",
                    vault_key="telegram.chat_id",
                    required=False,
                ),
            ],
            data_reads=["Messages (text, voice, photo, document)", "Chat info", "File downloads"],
            data_writes=["Send messages", "Send voice notes", "Send photos"],
            does_not_access=["Other chats unless configured", "User profile data", "Admin operations"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "telegram"
        return [
            # Read operations
            ConnectorOperation(
                id="get_updates",
                connector_id=cid,
                capability="connector.read",
                name="Get Updates",
                description="Long-poll for new messages via getUpdates",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="offset", type="int", required=False, default=0),
                    ParameterSpec(name="timeout", type="int", required=False, default=30),
                    ParameterSpec(name="limit", type="int", required=False, default=100),
                ],
            ),
            ConnectorOperation(
                id="get_me",
                connector_id=cid,
                capability="connector.read",
                name="Get Bot Info",
                description="Get information about the bot",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
            ),
            ConnectorOperation(
                id="get_chat",
                connector_id=cid,
                capability="connector.read",
                name="Get Chat Info",
                description="Get information about a chat",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="get_file",
                connector_id=cid,
                capability="connector.read",
                name="Get File",
                description="Get file path for downloading",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="file_id", type="str", required=True),
                ],
            ),
            # Write operations
            ConnectorOperation(
                id="send_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Message",
                description="Send a text message to a chat",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=False,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                    ParameterSpec(name="parse_mode", type="str", required=False, default="Markdown"),
                ],
            ),
            ConnectorOperation(
                id="send_voice",
                connector_id=cid,
                capability="connector.write",
                name="Send Voice",
                description="Send a voice note to a chat",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=False,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="voice_url", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="send_photo",
                connector_id=cid,
                capability="connector.write",
                name="Send Photo",
                description="Send a photo to a chat",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=False,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="photo_url", type="str", required=True),
                    ParameterSpec(name="caption", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="delete_message",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Message",
                description="Delete a message from a chat",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="int", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        cid = "telegram"
        cred_key = "telegram.bot_token"
        headers = {"Content-Type": "application/json"}

        if operation_id == "get_updates":
            offset = params.get("offset", 0)
            timeout = params.get("timeout", 30)
            limit = params.get("limit", 100)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.GET,
                url=f"{self.TG_API_BASE}{{token}}/getUpdates?offset={offset}&timeout={timeout}&limit={limit}",
                headers=headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "get_me":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.GET,
                url=f"{self.TG_API_BASE}{{token}}/getMe",
                headers=headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "get_chat":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.TG_API_BASE}{{token}}/getChat",
                headers=headers,
                body={"chat_id": params["chat_id"]},
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "get_file":
            file_id = params["file_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.GET,
                url=f"{self.TG_API_BASE}{{token}}/getFile?file_id={file_id}",
                headers=headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "send_message":
            body: Dict[str, Any] = {
                "chat_id": params["chat_id"],
                "text": params["text"],
            }
            if params.get("parse_mode"):
                body["parse_mode"] = params["parse_mode"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.TG_API_BASE}{{token}}/sendMessage",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "send_voice":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.TG_API_BASE}{{token}}/sendVoice",
                headers=headers,
                body={
                    "chat_id": params["chat_id"],
                    "voice": params["voice_url"],
                },
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "send_photo":
            body = {
                "chat_id": params["chat_id"],
                "photo": params["photo_url"],
            }
            if params.get("caption"):
                body["caption"] = params["caption"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.TG_API_BASE}{{token}}/sendPhoto",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        if operation_id == "delete_message":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.TG_API_BASE}{{token}}/deleteMessage",
                headers=headers,
                body={
                    "chat_id": params["chat_id"],
                    "message_id": params["message_id"],
                },
                credential_vault_key=cred_key,
                metadata={"auth_type": "url_token"},
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("telegram.bot_token")
