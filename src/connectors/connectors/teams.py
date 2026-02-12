"""
Microsoft Teams Connector — Microsoft Graph API integration.

Produces HTTP request specs for Teams channel and chat operations.
Never makes network calls directly.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class TeamsConnector(ConnectorBase):
    """Microsoft Teams connector via Graph API with governed operations."""

    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="teams",
            name="Microsoft Teams Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Microsoft Graph API for Teams channels and chats",
            target_domains=["graph.microsoft.com"],
            required_credentials=[
                CredentialSpec(
                    name="teams_access_token",
                    type="oauth_token",
                    vault_key="teams.graph_token",
                    scopes=[
                        "ChannelMessage.Read.All",
                        "ChannelMessage.Send",
                        "Chat.Read",
                        "Chat.ReadWrite",
                        "Team.ReadBasic.All",
                        "Channel.ReadBasic.All",
                    ],
                ),
            ],
            data_reads=[
                "Channel messages", "Chat messages", "Team/channel metadata",
            ],
            data_writes=[
                "New channel messages", "New chat messages", "Message replies",
            ],
            does_not_access=[
                "Email", "Calendar", "OneDrive files",
                "User profiles beyond display name", "Admin settings",
            ],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "teams"
        return [
            # ── Read operations ──────────────────────────────────
            ConnectorOperation(
                id="list_teams",
                connector_id=cid,
                capability="connector.read",
                name="List Joined Teams",
                description="List teams the authenticated user has joined",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
            ),
            ConnectorOperation(
                id="list_channels",
                connector_id=cid,
                capability="connector.read",
                name="List Channels",
                description="List channels in a team",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="read_messages",
                connector_id=cid,
                capability="connector.read",
                name="Read Channel Messages",
                description="Read messages from a team channel",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="get_message",
                connector_id=cid,
                capability="connector.read",
                name="Get Channel Message",
                description="Get a single message from a team channel",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="read_replies",
                connector_id=cid,
                capability="connector.read",
                name="Read Message Replies",
                description="Read replies to a channel message",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="read_chat_messages",
                connector_id=cid,
                capability="connector.read",
                name="Read Chat Messages",
                description="Read messages from a 1:1 or group chat",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            # ── Write operations ─────────────────────────────────
            ConnectorOperation(
                id="post_channel_message",
                connector_id=cid,
                capability="connector.write",
                name="Post Channel Message",
                description="Post a new message to a team channel",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=False,
                reversible=True,
                rollback_operation_id="delete_message",
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                    ParameterSpec(name="content_type", type="str", required=False, default="text"),
                ],
            ),
            ConnectorOperation(
                id="reply_to_message",
                connector_id=cid,
                capability="connector.write",
                name="Reply to Message",
                description="Reply to a channel message",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                    ParameterSpec(name="content_type", type="str", required=False, default="text"),
                ],
            ),
            ConnectorOperation(
                id="send_chat_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Chat Message",
                description="Send a message to a 1:1 or group chat",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="chat_id", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                    ParameterSpec(name="content_type", type="str", required=False, default="text"),
                ],
            ),
            # ── Delete operations ────────────────────────────────
            ConnectorOperation(
                id="delete_message",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Channel Message",
                description="Soft-delete a message from a team channel",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="team_id", type="str", required=True),
                    ParameterSpec(name="channel_id", type="str", required=True),
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GRAPH_API_BASE
        cred_key = "teams.graph_token"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "list_teams":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/me/joinedTeams",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "list_channels":
            tid = params["team_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/teams/{tid}/channels",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_messages":
            tid = params["team_id"]
            cid = params["channel_id"]
            limit = params.get("limit", 50)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/teams/{tid}/channels/{cid}/messages?$top={limit}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "get_message":
            tid = params["team_id"]
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/teams/{tid}/channels/{cid}/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_replies":
            tid = params["team_id"]
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/teams/{tid}/channels/{cid}/messages/{mid}/replies",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_chat_messages":
            chat_id = params["chat_id"]
            limit = params.get("limit", 50)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.GET,
                url=f"{base}/chats/{chat_id}/messages?$top={limit}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "post_channel_message":
            tid = params["team_id"]
            cid = params["channel_id"]
            ct = params.get("content_type", "text")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.POST,
                url=f"{base}/teams/{tid}/channels/{cid}/messages",
                headers=headers,
                body={"body": {"content": params["text"], "contentType": ct}},
                credential_vault_key=cred_key,
            )

        elif operation_id == "reply_to_message":
            tid = params["team_id"]
            cid = params["channel_id"]
            mid = params["message_id"]
            ct = params.get("content_type", "text")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.POST,
                url=f"{base}/teams/{tid}/channels/{cid}/messages/{mid}/replies",
                headers=headers,
                body={"body": {"content": params["text"], "contentType": ct}},
                credential_vault_key=cred_key,
            )

        elif operation_id == "send_chat_message":
            chat_id = params["chat_id"]
            ct = params.get("content_type", "text")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.POST,
                url=f"{base}/chats/{chat_id}/messages",
                headers=headers,
                body={"body": {"content": params["text"], "contentType": ct}},
                credential_vault_key=cred_key,
            )

        elif operation_id == "delete_message":
            tid = params["team_id"]
            cid = params["channel_id"]
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="teams",
                method=HTTPMethod.DELETE,
                url=f"{base}/teams/{tid}/channels/{cid}/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("teams.graph_token")
