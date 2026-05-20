"""Commerce intent models for governed connector execution."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.governance.models import RiskTier


RAW_PAYMENT_METADATA_KEYS = frozenset(
    {
        "account_number",
        "bank_account",
        "card_number",
        "cvc",
        "cvv",
        "debit_card",
        "credit_card",
        "payment_card_number",
        "routing_number",
    }
)


class CommerceOperation(str, Enum):
    """Supported UCP commerce operation identifiers."""

    VENDOR_SEARCH = "vendor.search"
    QUOTE_REQUEST = "quote.request"
    QUOTE_REFRESH = "quote.refresh"
    PURCHASE = "purchase"
    SUBSCRIPTION_CREATE = "subscription.create"
    SUBSCRIPTION_CHANGE = "subscription.change"
    SUBSCRIPTION_CANCEL = "subscription.cancel"
    BOOKING_CREATE = "booking.create"
    PROCUREMENT_REQUEST = "procurement.request"
    REFUND_REQUEST = "refund.request"
    VENDOR_ONBOARD = "vendor.onboard"
    PAYMENT_METHOD_ATTACH = "payment_method.attach"


DEFAULT_OPERATION_TIERS: dict[CommerceOperation, RiskTier] = {
    CommerceOperation.VENDOR_SEARCH: RiskTier.T1_REVERSIBLE,
    CommerceOperation.QUOTE_REQUEST: RiskTier.T2_CONTROLLED,
    CommerceOperation.QUOTE_REFRESH: RiskTier.T2_CONTROLLED,
    CommerceOperation.PURCHASE: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.SUBSCRIPTION_CREATE: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.SUBSCRIPTION_CHANGE: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.SUBSCRIPTION_CANCEL: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.BOOKING_CREATE: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.PROCUREMENT_REQUEST: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.REFUND_REQUEST: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.VENDOR_ONBOARD: RiskTier.T3_IRREVERSIBLE,
    CommerceOperation.PAYMENT_METHOD_ATTACH: RiskTier.T3_IRREVERSIBLE,
}

SPEND_COMMITTING_OPERATIONS = frozenset(
    {
        CommerceOperation.PURCHASE,
        CommerceOperation.SUBSCRIPTION_CREATE,
        CommerceOperation.SUBSCRIPTION_CHANGE,
        CommerceOperation.SUBSCRIPTION_CANCEL,
        CommerceOperation.BOOKING_CREATE,
        CommerceOperation.PROCUREMENT_REQUEST,
        CommerceOperation.REFUND_REQUEST,
        CommerceOperation.VENDOR_ONBOARD,
        CommerceOperation.PAYMENT_METHOD_ATTACH,
    }
)


def parse_commerce_operation(value: str | CommerceOperation) -> CommerceOperation:
    """Parse a commerce operation or raise a stable validation error."""
    if isinstance(value, CommerceOperation):
        return value
    try:
        return CommerceOperation(value)
    except ValueError as exc:
        raise ValueError(f"unknown UCP commerce operation: {value}") from exc


def default_tier_for_operation(value: str | CommerceOperation) -> RiskTier:
    """Return the default governance tier for a UCP commerce operation."""
    operation = parse_commerce_operation(value)
    return DEFAULT_OPERATION_TIERS.get(operation, RiskTier.T3_IRREVERSIBLE)


def operation_requires_approval(value: str | CommerceOperation) -> bool:
    """Return true when an operation commits spend or external terms."""
    operation = parse_commerce_operation(value)
    return operation in SPEND_COMMITTING_OPERATIONS


class CommerceActor(BaseModel):
    """Actor provenance for a commerce intent."""

    model_config = ConfigDict(extra="forbid")

    actor_type: Literal["agent", "operator", "system"]
    agent_id: str | None = None
    task_id: str | None = None
    operator_id: str | None = None

    @model_validator(mode="after")
    def _validate_provenance(self) -> CommerceActor:
        if self.actor_type == "agent" and not self.agent_id:
            raise ValueError("agent commerce intents require agent_id")
        if self.actor_type == "operator" and not self.operator_id:
            raise ValueError("operator commerce intents require operator_id")
        return self


class CommerceVendor(BaseModel):
    """Vendor identity and allowed execution domain."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    external_id: str | None = None
    domain: str = Field(min_length=1)

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, value: str) -> str:
        domain = value.strip().lower()
        if "://" in domain:
            parsed = urlparse(domain)
            domain = parsed.hostname or ""
        if not domain or "*" in domain or "/" in domain:
            raise ValueError("vendor.domain must be an exact hostname")
        return domain


class CommerceItem(BaseModel):
    """Item or service being quoted or requested."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    sku: str | None = None
    quantity: int = Field(default=1, ge=1)


class CommerceFinancial(BaseModel):
    """Financial attributes for a commerce intent."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3)
    recurring: bool = False
    recurrence_interval: str | None = None
    budget_code: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("financial.amount must be a decimal value") from exc

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if not currency.isalpha() or len(currency) != 3:
            raise ValueError("financial.currency must be an ISO 4217 code")
        return currency

    @model_validator(mode="after")
    def _validate_recurrence(self) -> CommerceFinancial:
        if self.recurring and not self.recurrence_interval:
            raise ValueError("recurring commerce intents require recurrence_interval")
        return self


class CommerceCommitment(BaseModel):
    """Legal or operational commitment attributes."""

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1)
    term_summary: str = Field(min_length=1)
    terms_url: str | None = None
    reversible: bool = False
    cancellation_window: str | None = None

    @field_validator("terms_url")
    @classmethod
    def _validate_terms_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("commitment.terms_url must be an https URL")
        return value


class CommerceRisk(BaseModel):
    """Declared risk context supplied with a commerce intent."""

    model_config = ConfigDict(extra="forbid")

    declared_default_tier: str = "T3"
    reason: str = Field(min_length=1)

    @field_validator("declared_default_tier")
    @classmethod
    def _validate_declared_default_tier(cls, value: str) -> str:
        text = value.strip().upper()
        if text not in {"T0", "T1", "T2", "T3"}:
            raise ValueError("risk.declared_default_tier must be T0, T1, T2, or T3")
        return text


class CommerceIntent(BaseModel):
    """Structured commerce intent submitted to UCP."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    domain: Literal["commerce"] = "commerce"
    connector_id: Literal["ucp"] = "ucp"
    operation: CommerceOperation
    requested_by: CommerceActor
    vendor: CommerceVendor
    item: CommerceItem
    financial: CommerceFinancial
    commitment: CommerceCommitment
    risk: CommerceRisk
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_operation_risk(self) -> CommerceIntent:
        default_tier = default_tier_for_operation(self.operation)
        declared_tier = RiskTier(int(self.risk.declared_default_tier[1:]))
        if declared_tier < default_tier:
            raise ValueError(
                "risk.declared_default_tier cannot be lower than the operation default"
            )
        if RAW_PAYMENT_METADATA_KEYS.intersection(self.metadata):
            raise ValueError("raw payment instrument fields are not allowed in metadata")
        return self

    def governance_capability(self) -> str:
        """Return the fully-qualified connector capability for this intent."""
        return f"connector.ucp.{self.operation.value}"

    def commerce_summary(self) -> dict[str, Any]:
        """Return receipt/approval-safe commerce metadata."""
        return {
            "intent_id": self.intent_id,
            "operation": self.operation.value,
            "vendor_name": self.vendor.name,
            "vendor_domain": self.vendor.domain,
            "item_name": self.item.name,
            "sku": self.item.sku,
            "quantity": self.item.quantity,
            "amount": str(self.financial.amount),
            "currency": self.financial.currency,
            "recurring": self.financial.recurring,
            "recurrence_interval": self.financial.recurrence_interval,
            "budget_code": self.financial.budget_code,
            "reversible": self.commitment.reversible,
            "cancellation_window": self.commitment.cancellation_window,
            "expires_at": self.expires_at,
            "requested_by": self.requested_by.model_dump(exclude_none=True),
            "requires_approval": operation_requires_approval(self.operation),
        }
