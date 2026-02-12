"""
Discord Connector — Discord REST API v10 integration.

Produces HTTP request specs for Discord guild and channel operations.
Never makes network calls directly.

Auth uses Bot token format: ``Authorization: Bot {token}``
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class DiscordConnector(ConnectorBase):
    """Discord REST API v10 connector with governed operations."""

    DISCORD_API_BASE = "https://discord.com/api/v10"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="discord",
            name="Discord Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Discord REST API for reading and posting messages",
            target_domains=["discord.com"],
            required_credentials=[
                CredentialSpec(
                    name="discord_bot_token",
                    type="api_key",
                    vault_key="discord.bot_token",
                    scopes=[],  # Discord uses permission integers, not OAuth scopes
                ),
            ],
            data_reads=[
                "Channel messages", "Guild/server metadata", "Channel metadata",
            ],
            data_writes=[
                "New messages", "Reactions", "Message edits",
            ],
            does_not_access=[
                "DMs unless channel ID provided", "User private data",
                "Server settings", "Role management", "Voice channels",
            ],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "discord"
        return [
            # ── Read operations ──────────────────────────────────
            ConnectorOperation(
                id="list_guilds",
                connector_id=cid,
                capability="connector.read",
                name="List Guilds",
                description="List guilds (servers) the bot has joined",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
            ),
            ConnectorOperation(
                id="list_channels",
                connector_id=cid,
                capability="connector.read",
                name="List Channels",
                description="List channels in a guild",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="guild_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="read_messages",
                connector_id=cid,
                capability="connector.read",
                name="Read Messages",
                description="Read message history from a channel",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="get_message",
                connector_id=cid,
                capability="connector.read",
                name="Get Message",
                description="Get a single message by ID",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            # ── Write operations ─────────────────────────────────
            ConnectorOperation(
                id="post_message",
                connector_id=cid,
                capability="connector.write",
                name="Post Message",
                description="Post a new message to a channel",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=False,
                reversible=True,
                rollback_operation_id="delete_message",
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="edit_message",
                connector_id=cid,
                capability="connector.write",
                name="Edit Message",
                description="Edit an existing message",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=True,
                reversible=True,
                rollback_operation_id="edit_message",
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="add_reaction",
                connector_id=cid,
                capability="connector.write",
                name="Add Reaction",
                description="Add an emoji reaction to a message",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                reversible=True,
                rollback_operation_id="remove_reaction",
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="emoji", type="str", required=True),
                ],
            ),
            # ── Delete operations ────────────────────────────────
            ConnectorOperation(
                id="delete_message",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Message",
                description="Delete a message from a channel",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="remove_reaction",
                connector_id=cid,
                capability="connector.delete",
                name="Remove Reaction",
                description="Remove own emoji reaction from a message",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="emoji", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.DISCORD_API_BASE
        cred_key = "discord.bot_token"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "list_guilds":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.GET,
                url=f"{base}/users/@me/guilds",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "list_channels":
            gid = params["guild_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.GET,
                url=f"{base}/guilds/{gid}/channels",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_messages":
            cid = params["channel_id"]
            limit = params.get("limit", 50)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.GET,
                url=f"{base}/channels/{cid}/messages?limit={limit}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.messages"},
            )

        elif operation_id == "get_message":
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.GET,
                url=f"{base}/channels/{cid}/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.messages"},
            )

        elif operation_id == "post_message":
            cid = params["channel_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.POST,
                url=f"{base}/channels/{cid}/messages",
                headers=headers,
                body={"content": params["text"]},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.messages"},
            )

        elif operation_id == "edit_message":
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.PATCH,
                url=f"{base}/channels/{cid}/messages/{mid}",
                headers=headers,
                body={"content": params["text"]},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.messages"},
            )

        elif operation_id == "add_reaction":
            cid = params["channel_id"]
            mid = params["message_id"]
            emoji = quote(params["emoji"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.PUT,
                url=f"{base}/channels/{cid}/messages/{mid}/reactions/{emoji}/@me",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.reactions"},
            )

        elif operation_id == "delete_message":
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.DELETE,
                url=f"{base}/channels/{cid}/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.messages"},
            )

        elif operation_id == "remove_reaction":
            cid = params["channel_id"]
            mid = params["message_id"]
            emoji = quote(params["emoji"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="discord",
                method=HTTPMethod.DELETE,
                url=f"{base}/channels/{cid}/messages/{mid}/reactions/{emoji}/@me",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
                metadata={"rate_limit_group": f"discord.channels.{cid}.reactions"},
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("discord.bot_token")
