"""UCP governed commerce connector.

The connector only produces request specs. Approval remains owned by the
governance layer; spend-committing operations fail closed unless approval
evidence is present in the approved execution payload.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.commerce import (
    CommerceIntent,
    CommerceOperation,
    UCPApprovalEvidence,
    default_tier_for_operation,
    operation_requires_approval,
    parse_commerce_operation,
)
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)


class UCPConnector(ConnectorBase):
    """First-party connector for governed commerce operations."""

    DEFAULT_BASE_URL = "https://api.ucp.example"
    CREDENTIAL_KEY = "ucp.api_token"

    def __init__(self, base_url: str | None = None, vault=None) -> None:
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        parsed = urlparse(self._base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("UCPConnector base_url must be an https URL")
        self._domain = parsed.hostname
        self._vault = vault

        manifest = ConnectorManifest(
            id="ucp",
            name="UCP Governed Commerce",
            version="0.1.0",
            author="lancelot",
            source="first-party",
            description=(
                "Governed commerce connector for quotes, purchases, "
                "subscriptions, bookings, procurement, refunds, and vendor actions"
            ),
            target_domains=[self._domain],
            required_credentials=[
                CredentialSpec(
                    name="ucp_api_token",
                    type="api_key",
                    vault_key=self.CREDENTIAL_KEY,
                    scopes=[
                        "quote:read",
                        "order:write",
                        "subscription:write",
                    ],
                ),
            ],
            data_reads=[
                "commerce.catalog",
                "commerce.quotes",
                "commerce.vendor_status",
            ],
            data_writes=[
                "commerce.proposals",
                "commerce.orders",
                "commerce.subscriptions",
                "commerce.cancellations",
            ],
            does_not_access=[
                "raw_payment_card_data",
                "unscoped_memory",
                "soul",
            ],
        )
        super().__init__(manifest)

    def get_operations(self) -> list[ConnectorOperation]:
        return [
            ConnectorOperation(
                id=operation.value,
                connector_id="ucp",
                capability="connector.write"
                if operation_requires_approval(operation)
                else "connector.read",
                name=operation.value.replace(".", " ").title(),
                description=f"UCP commerce operation: {operation.value}",
                default_tier=default_tier_for_operation(operation),
                idempotent=operation
                in {
                    CommerceOperation.VENDOR_SEARCH,
                    CommerceOperation.QUOTE_REQUEST,
                    CommerceOperation.QUOTE_REFRESH,
                    CommerceOperation.SUBSCRIPTION_CANCEL,
                    CommerceOperation.REFUND_REQUEST,
                },
                reversible=operation
                in {
                    CommerceOperation.SUBSCRIPTION_CANCEL,
                    CommerceOperation.REFUND_REQUEST,
                },
                parameters=[
                    ParameterSpec(
                        name="intent",
                        type="CommerceIntent",
                        required=True,
                        description="Structured UCP commerce intent",
                    ),
                    ParameterSpec(
                        name="approval_id",
                        type="str",
                        required=False,
                        description="Governance approval id for spend-committing operations",
                    ),
                ],
            )
            for operation in CommerceOperation
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        operation = parse_commerce_operation(operation_id)
        intent = CommerceIntent.model_validate(params.get("intent"))
        if intent.operation != operation:
            raise ValueError(
                f"Commerce intent operation {intent.operation.value} does not match {operation_id}"
            )

        approval_evidence = params.get("_governance_approval")
        approval_id = ""
        if operation_requires_approval(operation):
            if not isinstance(approval_evidence, UCPApprovalEvidence) or not approval_evidence.approved:
                raise PermissionError(
                    "UCP spend-committing operations require verified governance approval evidence"
                )
            approval_id = approval_evidence.approval_id

        path = operation.value.replace(".", "/")
        body: dict[str, Any] = {
            "intent": intent.model_dump(mode="json"),
            "approval_id": approval_id or None,
        }

        return ConnectorResult(
            operation_id=operation_id,
            connector_id="ucp",
            method=HTTPMethod.POST,
            url=f"{self._base_url}/v1/commerce/{path}",
            headers={"Content-Type": "application/json"},
            body=body,
            credential_vault_key=self.CREDENTIAL_KEY,
            metadata={
                "auth_type": "bearer",
                "domain": "commerce",
                "commerce": intent.commerce_summary(),
                "requires_approval": operation_requires_approval(operation),
                "approval_id": approval_id or None,
                "approval_source": approval_evidence.source
                if isinstance(approval_evidence, UCPApprovalEvidence)
                else None,
            },
        )

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return bool(self._vault.exists(self.CREDENTIAL_KEY))
