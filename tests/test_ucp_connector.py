from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.connectors.commerce import (
    CommerceIntent,
    CommerceOperation,
    UCPApprovalEvidence,
    default_tier_for_operation,
    operation_requires_approval,
)
from src.connectors.connectors.ucp import UCPConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


class _Vault:
    def __init__(self, keys: set[str]) -> None:
        self._keys = keys

    def exists(self, key: str) -> bool:
        return key in self._keys


def _intent(operation: str = "quote.request", **overrides):
    data = {
        "intent_id": "intent-1",
        "domain": "commerce",
        "connector_id": "ucp",
        "operation": operation,
        "requested_by": {
            "actor_type": "agent",
            "agent_id": "agent-1",
            "task_id": "task-1",
        },
        "vendor": {
            "name": "Example Vendor",
            "external_id": "vendor-1",
            "domain": "api.vendor.example",
        },
        "item": {
            "name": "Service Plan",
            "sku": "plan-pro",
            "quantity": 1,
        },
        "financial": {
            "amount": "49.00",
            "currency": "usd",
            "recurring": False,
            "budget_code": "ops.software",
        },
        "commitment": {
            "action_type": "quote",
            "term_summary": "Quote only; no spend commitment.",
            "terms_url": "https://vendor.example/terms",
            "reversible": True,
            "cancellation_window": "P7D",
        },
        "risk": {
            "declared_default_tier": "T2",
            "reason": "Quote request without spend commitment",
        },
        "expires_at": "2026-05-20T18:00:00Z",
        "metadata": {"quote_id": "quote-1"},
    }
    data.update(overrides)
    return data


def test_commerce_intent_normalizes_currency_and_domain() -> None:
    intent = CommerceIntent.model_validate(_intent())

    assert intent.financial.currency == "USD"
    assert intent.vendor.domain == "api.vendor.example"
    assert intent.governance_capability() == "connector.ucp.quote.request"


def test_commerce_intent_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError, match="Input should be"):
        CommerceIntent.model_validate(_intent(operation="wire.money"))


def test_commerce_intent_rejects_tier_below_operation_default() -> None:
    with pytest.raises(ValidationError, match="cannot be lower"):
        CommerceIntent.model_validate(
            _intent(
                operation="purchase",
                risk={
                    "declared_default_tier": "T2",
                    "reason": "Trying to underdeclare spend risk",
                },
            )
        )


def test_recurring_intent_requires_interval() -> None:
    data = _intent(
        financial={
            "amount": "49.00",
            "currency": "USD",
            "recurring": True,
        }
    )

    with pytest.raises(ValidationError, match="recurrence_interval"):
        CommerceIntent.model_validate(data)


def test_raw_payment_details_are_rejected_for_payment_method_metadata() -> None:
    with pytest.raises(ValidationError, match="raw payment instrument"):
        CommerceIntent.model_validate(
            _intent(
                operation="payment_method.attach",
                risk={
                    "declared_default_tier": "T3",
                    "reason": "Payment method change",
                },
                metadata={"card_number": "4111111111111111"},
            )
        )


def test_raw_payment_details_are_rejected_for_purchase_metadata() -> None:
    with pytest.raises(ValidationError, match="raw payment instrument"):
        CommerceIntent.model_validate(
            _intent(
                operation="purchase",
                risk={
                    "declared_default_tier": "T3",
                    "reason": "One-time purchase commits spend",
                },
                metadata={"credit_card": "4111111111111111"},
            )
        )


def test_nested_raw_payment_details_are_rejected() -> None:
    with pytest.raises(ValidationError, match="raw payment instrument"):
        CommerceIntent.model_validate(
            _intent(
                operation="purchase",
                risk={
                    "declared_default_tier": "T3",
                    "reason": "One-time purchase commits spend",
                },
                metadata={"payment": {"Card-Number": "4111111111111111"}},
            )
        )


def test_operation_default_tiers_are_fail_closed_for_spend() -> None:
    assert default_tier_for_operation("quote.request") == RiskTier.T2_CONTROLLED
    assert default_tier_for_operation("purchase") == RiskTier.T3_IRREVERSIBLE
    assert operation_requires_approval("purchase") is True
    assert operation_requires_approval("quote.request") is False


def test_ucp_connector_declares_all_operations() -> None:
    connector = UCPConnector()
    operations = {operation.id: operation for operation in connector.get_operations()}

    assert set(operations) == {operation.value for operation in CommerceOperation}
    assert operations["quote.request"].default_tier == RiskTier.T2_CONTROLLED
    assert operations["purchase"].default_tier == RiskTier.T3_IRREVERSIBLE
    assert operations["purchase"].capability == "connector.write"


def test_quote_request_builds_governed_request_spec() -> None:
    result = UCPConnector(base_url="https://commerce.example").execute(
        "quote.request",
        {"intent": _intent()},
    )

    assert result.method == HTTPMethod.POST
    assert result.url == "https://commerce.example/v1/commerce/quote/request"
    assert result.credential_vault_key == "ucp.api_token"
    assert result.metadata["auth_type"] == "bearer"
    assert result.metadata["commerce"]["amount"] == "49.00"
    assert result.metadata["requires_approval"] is False


def test_spend_committing_operation_requires_approval_evidence() -> None:
    purchase_intent = _intent(
        operation="purchase",
        commitment={
            "action_type": "purchase",
            "term_summary": "One-time purchase.",
            "terms_url": "https://vendor.example/terms",
            "reversible": False,
        },
        risk={
            "declared_default_tier": "T3",
            "reason": "One-time purchase commits spend",
        },
    )

    with pytest.raises(PermissionError, match="approval evidence"):
        UCPConnector().execute("purchase", {"intent": purchase_intent})

    result = UCPConnector().execute(
        "purchase",
        {
            "intent": purchase_intent,
            "_governance_approval": UCPApprovalEvidence(
                approval_id="approval-1",
                approved=True,
                source="governance",
                approved_by="op-1",
            ),
            "approval_id": "approval-1",
        },
    )
    assert result.metadata["approval_id"] == "approval-1"
    assert result.metadata["requires_approval"] is True


def test_ucp_connector_rejects_operation_tampering() -> None:
    with pytest.raises(ValueError, match="does not match"):
        UCPConnector().execute("purchase", {"intent": _intent("quote.request")})


def test_ucp_connector_rejects_unknown_operation_with_stable_error() -> None:
    with pytest.raises(ValueError, match="unknown UCP commerce operation"):
        UCPConnector().execute("wire.money", {"intent": _intent()})


def test_ucp_connector_validates_credentials() -> None:
    assert UCPConnector(vault=_Vault({"ucp.api_token"})).validate_credentials() is True
    assert UCPConnector(vault=_Vault(set())).validate_credentials() is False
    assert UCPConnector().validate_credentials() is False
