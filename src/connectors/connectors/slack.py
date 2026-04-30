"""
Slack Connector — Slack Web API integration.

Produces HTTP request specs for Slack API operations.
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


class SlackConnector(ConnectorBase):
    """Slack Web API connector with governed read and write operations."""

    SLACK_API_BASE = "https://slack.com/api"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="slack",
            name="Slack Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Slack Web API for reading and posting messages",
            target_domains=["slack.com"],
            required_credentials=[
                CredentialSpec(
                    name="slack_bot_token",
                    type="oauth_token",
                    vault_key="slack.bot_token",
                    scopes=[
                        "channels:read", "channels:history",
                        "chat:write", "reactions:write", "files:write",
                    ],
                ),
            ],
            data_reads=["Slack messages (text, user, timestamp)", "Channel metadata"],
            data_writes=["New messages", "Emoji reactions"],
            does_not_access=["DMs unless approved", "User profiles", "Admin settings"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "slack"
        return [
            # Read operations
            ConnectorOperation(
                id="read_channels",
                connector_id=cid,
                capability="connector.read",
                name="Read Channels",
                description="List all channels",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
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
                    ParameterSpec(name="channel", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                    ParameterSpec(name="oldest", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="read_threads",
                connector_id=cid,
                capability="connector.read",
                name="Read Threads",
                description="Read thread replies",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="channel", type="str", required=True),
                    ParameterSpec(name="thread_ts", type="str", required=True),
                ],
            ),
            # Write operations
            ConnectorOperation(
                id="post_message",
                connector_id=cid,
                capability="connector.write",
                name="Post Message",
                description="Post a message to a channel",
                default_tier=RiskTier.T2_CONTROLLED,
                reversible=True,
                rollback_operation_id="delete_message",
                parameters=[
                    ParameterSpec(name="channel", type="str", required=True),
                    ParameterSpec(name="text", type="str", required=True),
                    ParameterSpec(name="thread_ts", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="add_reaction",
                connector_id=cid,
                capability="connector.write",
                name="Add Reaction",
                description="Add emoji reaction to a message",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                reversible=True,
                parameters=[
                    ParameterSpec(name="channel", type="str", required=True),
                    ParameterSpec(name="timestamp", type="str", required=True),
                    ParameterSpec(name="name", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="upload_file",
                connector_id=cid,
                capability="connector.write",
                name="Upload File",
                description="Upload file content to a channel",
                default_tier=RiskTier.T2_CONTROLLED,
                reversible=False,
                parameters=[
                    ParameterSpec(name="channels", type="str", required=True),
                    ParameterSpec(name="content", type="str", required=True),
                    ParameterSpec(name="filename", type="str", required=False, default="upload.txt"),
                    ParameterSpec(name="title", type="str", required=False, default=""),
                ],
            ),
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
                    ParameterSpec(name="channel", type="str", required=True),
                    ParameterSpec(name="ts", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.SLACK_API_BASE
        cred_key = "slack.bot_token"
        headers = {"Content-Type": "application/json"}

        if operation_id == "read_channels":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.GET,
                url=f"{base}/conversations.list",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_messages":
            channel = params["channel"]
            limit = params.get("limit", 50)
            url = f"{base}/conversations.history?channel={channel}&limit={limit}"
            if params.get("oldest"):
                url += f"&oldest={params['oldest']}"
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.GET,
                url=url,
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_threads":
            channel = params["channel"]
            ts = params["thread_ts"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.GET,
                url=f"{base}/conversations.replies?channel={channel}&ts={ts}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "post_message":
            body = {"channel": params["channel"], "text": params["text"]}
            if params.get("thread_ts"):
                body["thread_ts"] = params["thread_ts"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.POST,
                url=f"{base}/chat.postMessage",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
            )

        elif operation_id == "add_reaction":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.POST,
                url=f"{base}/reactions.add",
                headers=headers,
                body={
                    "channel": params["channel"],
                    "timestamp": params["timestamp"],
                    "name": params["name"],
                },
                credential_vault_key=cred_key,
            )

        elif operation_id == "upload_file":
            body = {
                "channels": params["channels"],
                "content": params["content"],
                "filename": params.get("filename", "upload.txt"),
            }
            if params.get("title"):
                body["title"] = params["title"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.POST,
                url=f"{base}/files.upload",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
            )

        elif operation_id == "delete_message":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="slack",
                method=HTTPMethod.POST,
                url=f"{base}/chat.delete",
                headers=headers,
                body={"channel": params["channel"], "ts": params["ts"]},
                credential_vault_key=cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("slack.bot_token")
