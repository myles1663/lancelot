"""
Connector Runtime - production execution wrapper for governed connectors.

Builds the live connector execution stack:
registry -> ConnectorProxy -> GovernedConnectorProxy.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.connectors.governed_proxy import GovernedConnectorProxy
from src.connectors.proxy import ConnectorProxy
from src.connectors.rate_limiter import RateLimiterRegistry
from src.core.governance.config import RiskClassificationConfig
from src.core.governance.risk_classifier import RiskClassifier


class ConnectorRuntime:
    """Execute connector capabilities through the governed connector stack."""

    def __init__(
        self,
        registry: Any,
        vault: Any,
        risk_classifier: Any,
        policy_engine: Any = None,
        receipt_service: Any = None,
        trust_ledger: Any = None,
    ) -> None:
        self.registry = registry
        self.vault = vault
        self.rate_limits = RateLimiterRegistry(getattr(registry, "rate_limits", {}))
        self.proxy = ConnectorProxy(registry, vault, rate_limiter_registry=self.rate_limits)
        if risk_classifier is None:
            # Fail safe: connector operations still need a classifier object so
            # their declared default tiers can be registered at runtime.
            risk_classifier = RiskClassifier(
                RiskClassificationConfig(defaults={}),
                trust_ledger=trust_ledger,
            )
        self.governed_proxy = GovernedConnectorProxy(
            proxy=self.proxy,
            registry=registry,
            risk_classifier=risk_classifier,
            policy_engine=policy_engine,
            receipt_service=receipt_service,
            trust_ledger=trust_ledger,
        )

    def register_connector(self, connector_id: str) -> None:
        """Register classifier defaults for a connector's operations."""
        self.governed_proxy.register_connector_tiers(connector_id)

    def execute_operation(
        self,
        connector_id: str,
        operation_id: str,
        params: Dict[str, Any],
        *,
        operator_id: str = "",
        session_id: str = "",
        quest_id: str = "",
        parent_receipt_id: str = "",
    ):
        """Execute a specific connector operation."""
        return self.governed_proxy.execute_governed(
            connector_id,
            operation_id,
            params,
            operator_id=operator_id or None,
            session_id=session_id or None,
            quest_id=quest_id or None,
            parent_receipt_id=parent_receipt_id or None,
        )

    def execute_capability(
        self,
        capability: str,
        params: Dict[str, Any],
        *,
        operator_id: str = "",
        session_id: str = "",
        quest_id: str = "",
        parent_receipt_id: str = "",
    ):
        """Execute a fully-qualified connector capability."""
        connector_id, operation_id = self.parse_capability(capability)
        return self.execute_operation(
            connector_id,
            operation_id,
            params,
            operator_id=operator_id,
            session_id=session_id,
            quest_id=quest_id,
            parent_receipt_id=parent_receipt_id,
        )

    @staticmethod
    def parse_capability(capability: str) -> tuple[str, str]:
        """Parse connector.{connector_id}.{operation_id} into its components."""
        parts = capability.split(".", 2)
        if len(parts) != 3 or parts[0] != "connector" or not parts[1] or not parts[2]:
            raise ValueError(
                f"Invalid connector capability '{capability}'. Expected connector.<id>.<operation>"
            )
        return parts[1], parts[2]
