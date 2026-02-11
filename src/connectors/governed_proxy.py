"""
Governed Connector Proxy — Risk-tiered governance wrapper for ConnectorProxy.

Wraps every connector operation with:
1. Risk classification via RiskClassifier
2. Policy evaluation via PolicyEngine
3. Receipt emission for audit trail
4. Trust ledger integration (when available)

All methods are SYNCHRONOUS.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.connectors.models import ConnectorResponse, ConnectorResult
from src.connectors.proxy import ConnectorProxy
from src.connectors.registry import ConnectorRegistry
from src.core.governance.models import RiskTier

logger = logging.getLogger(__name__)


class GovernedConnectorProxy:
    """Governance-enforcing wrapper around ConnectorProxy.

    Every connector operation goes through:
    risk classification → policy evaluation → execution → receipt emission.
    """

    def __init__(
        self,
        proxy: ConnectorProxy,
        registry: ConnectorRegistry,
        risk_classifier: Any,
        policy_engine: Any = None,
        receipt_store: Optional[List] = None,
        batch_buffer: Optional[List] = None,
        trust_ledger: Any = None,
    ) -> None:
        self._proxy = proxy
        self._registry = registry
        self._classifier = risk_classifier
        self._policy_engine = policy_engine
        self._receipt_store = receipt_store if receipt_store is not None else []
        self._batch_buffer = batch_buffer if batch_buffer is not None else []
        self._trust_ledger = trust_ledger

    def register_connector_tiers(self, connector_id: str) -> None:
        """Register all operations for a connector in the risk classifier.

        Each operation's full_capability_id is mapped to its default_tier.
        """
        operations = self._registry.get_operations(connector_id)
        for op in operations:
            cap_id = op.full_capability_id
            self._classifier._defaults[cap_id] = op.default_tier
            logger.debug(
                "Registered tier %s for %s", op.default_tier.name, cap_id
            )

    def get_operation_tier(self, connector_id: str, operation_id: str) -> RiskTier:
        """Look up the risk tier for a specific operation."""
        op = self._registry.get_operation(connector_id, operation_id)
        cap_id = op.full_capability_id
        return self._classifier._defaults.get(cap_id, RiskTier.T3_IRREVERSIBLE)

    def execute_governed(
        self,
        connector_id: str,
        operation_id: str,
        params: Dict[str, Any],
    ) -> ConnectorResponse:
        """Execute a connector operation with full governance.

        Steps:
        1. Get connector and operation from registry
        2. Classify risk via RiskClassifier
        3. Evaluate policy (if policy engine available)
        4. Execute connector → ConnectorResult → ConnectorProxy
        5. Emit receipt
        6. Return response
        """
        # 1. Get operation
        try:
            entry = self._registry.get(connector_id)
            if entry is None:
                return ConnectorResponse(
                    operation_id=operation_id,
                    connector_id=connector_id,
                    status_code=0,
                    success=False,
                    error=f"Connector '{connector_id}' not found",
                )
            op = self._registry.get_operation(connector_id, operation_id)
        except KeyError as e:
            return ConnectorResponse(
                operation_id=operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error=str(e),
            )

        # 2. Classify risk
        cap_id = op.full_capability_id
        risk_profile = self._classifier.classify(
            capability=cap_id,
            scope="external",
        )

        # 3. Policy evaluation
        if self._policy_engine and hasattr(self._policy_engine, "evaluate_intent"):
            from src.tools.contracts import ToolIntent, Capability, RiskLevel

            # Map connector capability to closest ToolIntent
            risk_map = {
                RiskTier.T0_INERT: RiskLevel.LOW,
                RiskTier.T1_REVERSIBLE: RiskLevel.LOW,
                RiskTier.T2_CONTROLLED: RiskLevel.MEDIUM,
                RiskTier.T3_IRREVERSIBLE: RiskLevel.HIGH,
            }
            intent = ToolIntent(
                capability=Capability.CONNECTOR_READ
                if "read" in op.capability
                else Capability.CONNECTOR_WRITE
                if "write" in op.capability
                else Capability.CONNECTOR_DELETE,
                action=cap_id,
                risk=risk_map.get(risk_profile.tier, RiskLevel.HIGH),
            )
            decision = self._policy_engine.evaluate_intent(intent)
            if not decision.allowed:
                return ConnectorResponse(
                    operation_id=operation_id,
                    connector_id=connector_id,
                    status_code=0,
                    success=False,
                    error=f"Policy denied: {'; '.join(decision.reasons) if hasattr(decision, 'reasons') else 'Denied'}",
                )

        # 4. Execute
        connector = entry.connector
        result = connector.execute(operation_id, params)

        if isinstance(result, ConnectorResult):
            response = self._proxy.execute(result)
        elif isinstance(result, dict):
            # Connector returned a raw dict — wrap it
            response = ConnectorResponse(
                operation_id=operation_id,
                connector_id=connector_id,
                status_code=200,
                body=result,
                success=True,
            )
        else:
            response = ConnectorResponse(
                operation_id=operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error="Connector returned unexpected type",
            )

        # 5. Update trust ledger
        if self._trust_ledger is not None:
            try:
                if response.success:
                    self._trust_ledger.record_success(cap_id, "external")
                else:
                    self._trust_ledger.record_failure(cap_id, "external")
            except KeyError:
                pass  # No trust record for this capability yet

        # 6. Emit receipt
        receipt = {
            "receipt_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connector_id": connector_id,
            "operation_id": operation_id,
            "capability": cap_id,
            "tier": risk_profile.tier.name,
            "status_code": response.status_code,
            "success": response.success,
        }
        response.receipt_id = receipt["receipt_id"]

        # T0 receipts go to batch buffer, T1+ to receipt store
        if risk_profile.tier == RiskTier.T0_INERT and self._batch_buffer is not None:
            self._batch_buffer.append(receipt)
        else:
            self._receipt_store.append(receipt)

        return response

    def handle_rollback(
        self, connector_id: str, operation_id: str, scope: str = "external"
    ) -> None:
        """Record a rollback failure in the trust ledger."""
        if self._trust_ledger is None:
            return
        try:
            op = self._registry.get_operation(connector_id, operation_id)
            cap_id = op.full_capability_id
            self._trust_ledger.record_failure(cap_id, scope, is_rollback=True)
        except KeyError:
            pass
