"""
Email Connector — Gmail API integration.

Produces HTTP request specs for Gmail REST API operations.
Never makes network calls directly.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any, Dict, List
from urllib.parse import urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class EmailConnector(ConnectorBase):
    """Gmail API connector with governed read and write operations."""

    GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="email",
            name="Email Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Gmail API integration for reading and sending email",
            target_domains=["gmail.googleapis.com"],
            required_credentials=[
                CredentialSpec(
                    name="gmail_access_token",
                    type="oauth_token",
                    vault_key="email.gmail_token",
                    scopes=["gmail.readonly", "gmail.send"],
                ),
            ],
            data_reads=["Email subjects, bodies, senders, timestamps"],
            data_writes=["New emails, replies"],
            does_not_access=["Email drafts", "Email settings", "Contact lists"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "email"
        return [
            # Read operations
            ConnectorOperation(
                id="list_messages",
                connector_id=cid,
                capability="connector.read",
                name="List Messages",
                description="List messages matching optional query",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="query", type="str", required=False, default=""),
                    ParameterSpec(name="max_results", type="int", required=False, default=20),
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
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="search_messages",
                connector_id=cid,
                capability="connector.read",
                name="Search Messages",
                description="Search messages with Gmail query syntax",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="query", type="str", required=True),
                    ParameterSpec(name="max_results", type="int", required=False, default=20),
                ],
            ),
            # Write operations
            ConnectorOperation(
                id="send_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Message",
                description="Send a new email",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True),
                    ParameterSpec(name="subject", type="str", required=True),
                    ParameterSpec(name="body", type="str", required=True),
                    ParameterSpec(name="cc", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="reply_message",
                connector_id=cid,
                capability="connector.write",
                name="Reply to Message",
                description="Reply to an existing email thread",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="thread_id", type="str", required=True),
                    ParameterSpec(name="body", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="delete_message",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Message",
                description="Permanently delete a message",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="move_to_folder",
                connector_id=cid,
                capability="connector.write",
                name="Move to Folder",
                description="Add a label to a message (move to folder)",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=True,
                reversible=True,
                rollback_operation_id="move_to_folder",
                parameters=[
                    ParameterSpec(name="message_id", type="str", required=True),
                    ParameterSpec(name="label_id", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GMAIL_API_BASE
        cred_key = "email.gmail_token"
        headers = {"Accept": "application/json"}

        if operation_id == "list_messages":
            query_params = {}
            if params.get("query"):
                query_params["q"] = params["query"]
            query_params["maxResults"] = params.get("max_results", 20)
            qs = urlencode(query_params)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f"{base}/users/me/messages?{qs}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "get_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f"{base}/users/me/messages/{mid}?format=full",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "search_messages":
            query_params = {"q": params["query"]}
            query_params["maxResults"] = params.get("max_results", 20)
            qs = urlencode(query_params)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f"{base}/users/me/messages?{qs}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "send_message":
            msg = MIMEText(params["body"])
            msg["to"] = params["to"]
            msg["subject"] = params["subject"]
            if params.get("cc"):
                msg["cc"] = params["cc"]
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/users/me/messages",
                headers=headers,
                body={"raw": raw},
                credential_vault_key=cred_key,
            )

        elif operation_id == "reply_message":
            msg = MIMEText(params["body"])
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/users/me/messages",
                headers=headers,
                body={"raw": raw, "threadId": params["thread_id"]},
                credential_vault_key=cred_key,
            )

        elif operation_id == "delete_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.DELETE,
                url=f"{base}/users/me/messages/{mid}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "move_to_folder":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/users/me/messages/{mid}/modify",
                headers=headers,
                body={"addLabelIds": [params["label_id"]]},
                credential_vault_key=cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("email.gmail_token")
