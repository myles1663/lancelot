"""
WhatsApp Business Connector — Meta Cloud API integration.

Produces HTTP request specs for WhatsApp Business messaging.
Never makes network calls directly.

Key constraint: free-form text messages can only be sent within
24 hours of the last customer-initiated message. Outside this window,
only pre-approved template messages are allowed.
"""

from __future__ import annotations

from typing import List

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class WhatsAppConnector(ConnectorBase):
    """WhatsApp Business Cloud API connector with governed operations."""

    DEFAULT_API_VERSION = "v21.0"

    def __init__(
        self,
        phone_number_id: str = "",
        api_version: str = DEFAULT_API_VERSION,
        vault=None,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._api_base = f"https://graph.facebook.com/{api_version}"

        manifest = ConnectorManifest(
            id="whatsapp",
            name="WhatsApp Business Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="WhatsApp Business Cloud API for messaging",
            target_domains=["graph.facebook.com"],
            required_credentials=[
                CredentialSpec(
                    name="whatsapp_access_token",
                    type="oauth_token",
                    vault_key="whatsapp.access_token",
                    scopes=["whatsapp_business_messaging"],
                ),
            ],
            data_reads=[
                "Message status", "Business profile", "Media downloads",
            ],
            data_writes=[
                "Text messages", "Template messages",
                "Media messages", "Interactive messages",
            ],
            does_not_access=[
                "Contact lists beyond conversation", "User profile photos",
                "Group admin controls", "Payment information",
            ],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "whatsapp"
        return [
            # ── Write operations ─────────────────────────────────
            ConnectorOperation(
                id="send_text_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Text Message",
                description="Send a free-form text message (24hr window required)",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True,
                                  description="Recipient phone number in E.164 format"),
                    ParameterSpec(name="text", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="send_template_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Template Message",
                description="Send a pre-approved template message (can initiate conversation)",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True),
                    ParameterSpec(name="template_name", type="str", required=True),
                    ParameterSpec(name="language_code", type="str", required=False, default="en_US"),
                    ParameterSpec(name="components", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="send_media_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Media Message",
                description="Send an image, video, or document message",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True),
                    ParameterSpec(name="media_type", type="str", required=True,
                                  description="image, video, audio, or document"),
                    ParameterSpec(name="media_id", type="str", required=True),
                    ParameterSpec(name="caption", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="send_interactive_message",
                connector_id=cid,
                capability="connector.write",
                name="Send Interactive Message",
                description="Send a message with buttons or list options",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True),
                    ParameterSpec(name="interactive_type", type="str", required=True,
                                  description="button or list"),
                    ParameterSpec(name="body_text", type="str", required=True),
                    ParameterSpec(name="action", type="str", required=True,
                                  description="JSON string of action payload"),
                ],
            ),
            # ── Low-risk operations ──────────────────────────────
            ConnectorOperation(
                id="mark_read",
                connector_id=cid,
                capability="connector.write",
                name="Mark as Read",
                description="Mark a message as read",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="message_id", type="str", required=True),
                ],
            ),
            # ── Read operations ──────────────────────────────────
            ConnectorOperation(
                id="get_media",
                connector_id=cid,
                capability="connector.read",
                name="Get Media",
                description="Retrieve media file URL",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="media_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="upload_media",
                connector_id=cid,
                capability="connector.write",
                name="Upload Media",
                description="Upload media for later sending",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="file_path", type="str", required=True),
                    ParameterSpec(name="mime_type", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="get_business_profile",
                connector_id=cid,
                capability="connector.read",
                name="Get Business Profile",
                description="Get the WhatsApp Business profile details",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self._api_base
        pnid = self._phone_number_id
        cred_key = "whatsapp.access_token"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "send_text_message":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/messages",
                headers=headers,
                body={
                    "messaging_product": "whatsapp",
                    "to": params["to"],
                    "type": "text",
                    "text": {"body": params["text"]},
                },
                credential_vault_key=cred_key,
                metadata={"requires_template_outside_window": True},
            )

        elif operation_id == "send_template_message":
            template = {
                "name": params["template_name"],
                "language": {"code": params.get("language_code", "en_US")},
            }
            if params.get("components"):
                import json
                try:
                    template["components"] = json.loads(params["components"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/messages",
                headers=headers,
                body={
                    "messaging_product": "whatsapp",
                    "to": params["to"],
                    "type": "template",
                    "template": template,
                },
                credential_vault_key=cred_key,
            )

        elif operation_id == "send_media_message":
            media_type = params["media_type"]
            media_body = {"id": params["media_id"]}
            if params.get("caption"):
                media_body["caption"] = params["caption"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/messages",
                headers=headers,
                body={
                    "messaging_product": "whatsapp",
                    "to": params["to"],
                    "type": media_type,
                    media_type: media_body,
                },
                credential_vault_key=cred_key,
            )

        elif operation_id == "send_interactive_message":
            import json
            try:
                action = json.loads(params["action"])
            except (json.JSONDecodeError, TypeError):
                action = {}
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/messages",
                headers=headers,
                body={
                    "messaging_product": "whatsapp",
                    "to": params["to"],
                    "type": "interactive",
                    "interactive": {
                        "type": params["interactive_type"],
                        "body": {"text": params["body_text"]},
                        "action": action,
                    },
                },
                credential_vault_key=cred_key,
            )

        elif operation_id == "mark_read":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/messages",
                headers=headers,
                body={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": params["message_id"],
                },
                credential_vault_key=cred_key,
            )

        elif operation_id == "get_media":
            mid = params["media_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.GET,
                url=f"{base}/{mid}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        elif operation_id == "upload_media":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.POST,
                url=f"{base}/{pnid}/media",
                headers={"Content-Type": params["mime_type"]},
                body={"file": params["file_path"]},
                credential_vault_key=cred_key,
            )

        elif operation_id == "get_business_profile":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="whatsapp",
                method=HTTPMethod.GET,
                url=f"{base}/{pnid}/whatsapp_business_profile",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("whatsapp.access_token")
