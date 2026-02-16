"""
SMS Connector — Twilio REST API integration.

Produces HTTP request specs for Twilio SMS/MMS operations.
Never makes network calls directly.

Auth uses HTTP Basic (Account SID + Auth Token).
Twilio uses ``application/x-www-form-urlencoded`` for write operations.
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


class SMSConnector(ConnectorBase):
    """Twilio SMS/MMS connector with governed operations."""

    TWILIO_API_BASE = "https://api.twilio.com/2010-04-01/Accounts"

    def __init__(
        self,
        account_sid: str = "",
        from_number: str = "",
        messaging_service_sid: str = "",
        vault=None,
    ) -> None:
        self._account_sid = account_sid
        self._from_number = from_number
        self._messaging_service_sid = messaging_service_sid

        manifest = ConnectorManifest(
            id="sms",
            name="SMS Integration (Twilio)",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Twilio REST API for sending and reading SMS/MMS",
            target_domains=["api.twilio.com"],
            required_credentials=[
                CredentialSpec(
                    name="twilio_account_sid",
                    type="config",
                    vault_key="sms.account_sid",
                    required=True,
                ),
                CredentialSpec(
                    name="twilio_auth_token",
                    type="api_key",
                    vault_key="sms.auth_token",
                    required=True,
                ),
                CredentialSpec(
                    name="twilio_from_number",
                    type="config",
                    vault_key="sms.from_number",
                    required=False,
                ),
                CredentialSpec(
                    name="twilio_messaging_service_sid",
                    type="config",
                    vault_key="sms.messaging_service_sid",
                    required=False,
                ),
            ],
            data_reads=[
                "Message history", "Message status", "Media metadata",
            ],
            data_writes=[
                "Outbound SMS", "Outbound MMS",
            ],
            does_not_access=[
                "Voice calls", "Phone number management",
                "Account billing", "Twilio Studio flows",
            ],
        )
        super().__init__(manifest)
        self._vault = vault

    @property
    def _base_url(self) -> str:
        return f"{self.TWILIO_API_BASE}/{self._account_sid}"

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "sms"
        return [
            ConnectorOperation(
                id="send_sms",
                connector_id=cid,
                capability="connector.write",
                name="Send SMS",
                description="Send an SMS text message",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True,
                                  description="Recipient phone in E.164 format"),
                    ParameterSpec(name="body", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="send_mms",
                connector_id=cid,
                capability="connector.write",
                name="Send MMS",
                description="Send an MMS message with media",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=False,
                reversible=False,
                parameters=[
                    ParameterSpec(name="to", type="str", required=True),
                    ParameterSpec(name="body", type="str", required=True),
                    ParameterSpec(name="media_url", type="str", required=True,
                                  description="Publicly accessible media URL"),
                ],
            ),
            ConnectorOperation(
                id="get_message",
                connector_id=cid,
                capability="connector.read",
                name="Get Message",
                description="Get a single message by SID",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="message_sid", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="list_messages",
                connector_id=cid,
                capability="connector.read",
                name="List Messages",
                description="List messages with optional filters",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="to", type="str", required=False, default=""),
                    ParameterSpec(name="from_number", type="str", required=False, default=""),
                    ParameterSpec(name="date_sent", type="str", required=False, default=""),
                ],
            ),
            ConnectorOperation(
                id="get_media",
                connector_id=cid,
                capability="connector.read",
                name="Get Media",
                description="Get media metadata from an MMS message",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="message_sid", type="str", required=True),
                    ParameterSpec(name="media_sid", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="delete_message",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Message",
                description="Delete a message from Twilio logs",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="message_sid", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self._base_url
        cred_key = "sms.auth_token"
        form_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        json_headers = {"Accept": "application/json"}

        if operation_id == "send_sms":
            form_data = {"To": params["to"], "Body": params["body"]}
            if self._messaging_service_sid:
                form_data["MessagingServiceSid"] = self._messaging_service_sid
            elif self._from_number:
                form_data["From"] = self._from_number
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.POST,
                url=f"{base}/Messages.json",
                headers=form_headers,
                body=urlencode(form_data),
                credential_vault_key=cred_key,
                metadata={"billable": True, "auth_type": "basic_auth"},
            )

        elif operation_id == "send_mms":
            form_data = {
                "To": params["to"],
                "Body": params["body"],
                "MediaUrl": params["media_url"],
            }
            if self._messaging_service_sid:
                form_data["MessagingServiceSid"] = self._messaging_service_sid
            elif self._from_number:
                form_data["From"] = self._from_number
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.POST,
                url=f"{base}/Messages.json",
                headers=form_headers,
                body=urlencode(form_data),
                credential_vault_key=cred_key,
                metadata={"billable": True, "auth_type": "basic_auth"},
            )

        elif operation_id == "get_message":
            sid = params["message_sid"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.GET,
                url=f"{base}/Messages/{sid}.json",
                headers=json_headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "basic_auth"},
            )

        elif operation_id == "list_messages":
            query_params = {}
            if params.get("to"):
                query_params["To"] = params["to"]
            if params.get("from_number"):
                query_params["From"] = params["from_number"]
            if params.get("date_sent"):
                query_params["DateSent"] = params["date_sent"]
            qs = f"?{urlencode(query_params)}" if query_params else ""
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.GET,
                url=f"{base}/Messages.json{qs}",
                headers=json_headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "basic_auth"},
            )

        elif operation_id == "get_media":
            msid = params["message_sid"]
            media_sid = params["media_sid"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.GET,
                url=f"{base}/Messages/{msid}/Media/{media_sid}.json",
                headers=json_headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "basic_auth"},
            )

        elif operation_id == "delete_message":
            sid = params["message_sid"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sms",
                method=HTTPMethod.DELETE,
                url=f"{base}/Messages/{sid}.json",
                headers=json_headers,
                credential_vault_key=cred_key,
                metadata={"auth_type": "basic_auth"},
            )

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return (
            self._vault.exists("sms.account_sid")
            and self._vault.exists("sms.auth_token")
        )
