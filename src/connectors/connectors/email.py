"""
Email Connector — Multi-backend email integration.

Supports three backends:
- ``gmail``   — Google Gmail REST API
- ``outlook`` — Microsoft Graph API (Office 365)
- ``smtp``    — Standard SMTP/IMAP protocols via ProtocolAdapter

Produces HTTP request specs for all operations.
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


# ── Backend Configurations ────────────────────────────────────────

_BACKEND_CONFIG = {
    "gmail": {
        "api_base": "https://gmail.googleapis.com/gmail/v1",
        "target_domains": ["gmail.googleapis.com"],
        "description": "Gmail API integration for reading and sending email",
        "credential": CredentialSpec(
            name="gmail_access_token",
            type="oauth_token",
            vault_key="email.gmail_token",
            scopes=["gmail.readonly", "gmail.send"],
        ),
        "does_not_access": ["Email drafts", "Email settings", "Contact lists"],
    },
    "outlook": {
        "api_base": "https://graph.microsoft.com/v1.0",
        "target_domains": ["graph.microsoft.com"],
        "description": "Microsoft Graph API for Outlook email",
        "credential": CredentialSpec(
            name="outlook_access_token",
            type="oauth_token",
            vault_key="email.outlook_token",
            scopes=["Mail.Read", "Mail.Send", "Mail.ReadWrite"],
        ),
        "does_not_access": [
            "Calendar data", "Teams messages", "OneDrive files",
            "User profile details",
        ],
    },
    "smtp": {
        "api_base": "protocol://smtp",
        "target_domains": ["protocol.smtp", "protocol.imap"],
        "description": "SMTP/IMAP email via standard protocols",
        "credentials": [
            CredentialSpec(
                name="smtp_host",
                type="config",
                vault_key="email.smtp_host",
                required=True,
            ),
            CredentialSpec(
                name="smtp_port",
                type="config",
                vault_key="email.smtp_port",
                required=True,
            ),
            CredentialSpec(
                name="smtp_username",
                type="config",
                vault_key="email.smtp_username",
                required=True,
            ),
            CredentialSpec(
                name="smtp_password",
                type="api_key",
                vault_key="email.smtp_password",
                required=True,
            ),
            CredentialSpec(
                name="smtp_from_address",
                type="config",
                vault_key="email.smtp_from_address",
                required=True,
            ),
            CredentialSpec(
                name="smtp_use_tls",
                type="config",
                vault_key="email.smtp_use_tls",
                required=False,
            ),
            CredentialSpec(
                name="imap_host",
                type="config",
                vault_key="email.imap_host",
                required=False,
            ),
            CredentialSpec(
                name="imap_port",
                type="config",
                vault_key="email.imap_port",
                required=False,
            ),
        ],
        "does_not_access": ["Contact lists", "Calendar", "Email settings"],
    },
}


class EmailConnector(ConnectorBase):
    """Multi-backend email connector with governed read and write operations."""

    def __init__(self, backend: str = "gmail", vault=None) -> None:
        if backend not in _BACKEND_CONFIG:
            raise ValueError(
                f"Unknown email backend: {backend!r}. "
                f"Supported: {list(_BACKEND_CONFIG.keys())}"
            )
        self._backend = backend
        cfg = _BACKEND_CONFIG[backend]

        # SMTP uses multiple credential specs; Gmail/Outlook use a single one
        if "credentials" in cfg:
            cred_list = cfg["credentials"]
        else:
            cred_list = [cfg["credential"]]

        manifest = ConnectorManifest(
            id="email",
            name="Email Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description=cfg["description"],
            target_domains=cfg["target_domains"],
            required_credentials=cred_list,
            data_reads=["Email subjects, bodies, senders, timestamps"],
            data_writes=["New emails, replies"],
            does_not_access=cfg["does_not_access"],
        )
        super().__init__(manifest)
        self._vault = vault
        self._api_base = cfg["api_base"]
        if "credential" in cfg:
            self._cred_key = cfg["credential"].vault_key
        else:
            self._cred_key = "email.smtp_password"

    @property
    def backend(self) -> str:
        return self._backend

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "email"
        return [
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
                description="Search messages with query syntax",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="query", type="str", required=True),
                    ParameterSpec(name="max_results", type="int", required=False, default=20),
                ],
            ),
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
                description="Move a message to a folder/label",
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
        if self._backend == "gmail":
            return self._execute_gmail(operation_id, params)
        elif self._backend == "outlook":
            return self._execute_outlook(operation_id, params)
        elif self._backend == "smtp":
            return self._execute_smtp(operation_id, params)
        else:
            raise ValueError(f"Unknown backend: {self._backend}")

    # ── Gmail Backend ─────────────────────────────────────────────

    def _execute_gmail(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self._api_base
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
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "get_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f"{base}/users/me/messages/{mid}?format=full",
                headers=headers,
                credential_vault_key=self._cred_key,
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
                credential_vault_key=self._cred_key,
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
                credential_vault_key=self._cred_key,
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
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "delete_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.DELETE,
                url=f"{base}/users/me/messages/{mid}",
                headers=headers,
                credential_vault_key=self._cred_key,
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
                credential_vault_key=self._cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    # ── Outlook Backend ───────────────────────────────────────────

    def _execute_outlook(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self._api_base
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "list_messages":
            max_results = params.get("max_results", 20)
            url = f"{base}/me/messages?$top={max_results}"
            if params.get("query"):
                url += f"&$filter=contains(subject,'{params['query']}')"
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=url,
                headers={"Accept": "application/json"},
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "get_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f"{base}/me/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "search_messages":
            max_results = params.get("max_results", 20)
            query = params["query"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.GET,
                url=f'{base}/me/messages?$search="{query}"&$top={max_results}',
                headers={"Accept": "application/json"},
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "send_message":
            body = {
                "message": {
                    "subject": params["subject"],
                    "body": {
                        "contentType": "Text",
                        "content": params["body"],
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": params["to"]}},
                    ],
                },
            }
            if params.get("cc"):
                body["message"]["ccRecipients"] = [
                    {"emailAddress": {"address": params["cc"]}},
                ]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/me/sendMail",
                headers=headers,
                body=body,
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "reply_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/me/messages/{mid}/reply",
                headers=headers,
                body={"comment": params["body"]},
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "delete_message":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.DELETE,
                url=f"{base}/me/messages/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=self._cred_key,
            )

        elif operation_id == "move_to_folder":
            mid = params["message_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url=f"{base}/me/messages/{mid}/move",
                headers=headers,
                body={"destinationId": params["label_id"]},
                credential_vault_key=self._cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    # ── SMTP/IMAP Backend ────────────────────────────────────────

    def _execute_smtp(self, operation_id: str, params: dict) -> ConnectorResult:
        # Read operations → IMAP protocol adapter
        if operation_id in ("list_messages", "get_message", "search_messages",
                            "delete_message", "move_to_folder"):
            body = {"protocol": "imap"}

            if operation_id == "list_messages":
                body["action"] = "list"
                body["query"] = params.get("query", "")
                body["max_results"] = params.get("max_results", 20)
            elif operation_id == "get_message":
                body["action"] = "fetch"
                body["message_id"] = params["message_id"]
            elif operation_id == "search_messages":
                body["action"] = "search"
                body["query"] = params["query"]
                body["max_results"] = params.get("max_results", 20)
            elif operation_id == "delete_message":
                body["action"] = "delete"
                body["message_id"] = params["message_id"]
            elif operation_id == "move_to_folder":
                body["action"] = "move"
                body["message_id"] = params["message_id"]
                body["destination"] = params["label_id"]

            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url="protocol://imap",
                body=body,
                credential_vault_key=self._cred_key,
                metadata={"protocol_adapter": True},
            )

        # Write operations → SMTP protocol adapter
        elif operation_id in ("send_message", "reply_message"):
            body = {
                "protocol": "smtp",
                "action": "send",
                "to": params.get("to", ""),
                "subject": params.get("subject", ""),
                "body": params.get("body", ""),
                "mime_type": "text/plain",
            }
            if operation_id == "send_message" and params.get("cc"):
                body["cc"] = params["cc"]
            if operation_id == "reply_message":
                body["headers"] = {"In-Reply-To": params.get("message_id", "")}
                body["thread_id"] = params.get("thread_id", "")

            return ConnectorResult(
                operation_id=operation_id,
                connector_id="email",
                method=HTTPMethod.POST,
                url="protocol://smtp",
                body=body,
                credential_vault_key=self._cred_key,
                metadata={"protocol_adapter": True},
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        if self._backend == "smtp":
            return (
                self._vault.exists("email.smtp_host")
                and self._vault.exists("email.smtp_port")
                and self._vault.exists("email.smtp_username")
                and self._vault.exists("email.smtp_password")
                and self._vault.exists("email.smtp_from_address")
            )
        return self._vault.exists(self._cred_key)
