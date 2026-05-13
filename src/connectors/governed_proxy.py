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
import fnmatch
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.connectors.models import ConnectorResponse, ConnectorResult
from src.connectors.proxy import ConnectorProxy
from src.connectors.registry import ConnectorRegistry
from src.core.governance.models import RiskTier
from src.shared.receipts import ActionType, CognitionTier, create_receipt

logger = logging.getLogger(__name__)

_DAILY_SENDS_FILE = "soul_connector_daily_sends.json"


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
        receipt_service: Any = None,
        trust_ledger: Any = None,
        soul: Any = None,
    ) -> None:
        self._proxy = proxy
        self._registry = registry
        self._classifier = risk_classifier
        self._policy_engine = policy_engine
        self._receipt_store = receipt_store if receipt_store is not None else []
        self._batch_buffer = batch_buffer if batch_buffer is not None else []
        self._receipt_service = receipt_service
        self._trust_ledger = trust_ledger
        self._soul = soul
        self._daily_send_lock = threading.Lock()

    def update_soul(self, soul: Any) -> None:
        """Update the Soul policy used for connector-layer enforcement."""
        self._soul = soul

    def register_connector_tiers(self, connector_id: str) -> None:
        """Register all operations for a connector in the risk classifier.

        Each operation's full_capability_id is mapped to its default_tier.
        """
        operations = self._registry.get_operations(connector_id)
        for op in operations:
            cap_id = op.full_capability_id
            self._classifier.set_default_tier(cap_id, op.default_tier)
            logger.debug(
                "Registered tier %s for %s", op.default_tier.name, cap_id
            )

    def get_operation_tier(self, connector_id: str, operation_id: str) -> RiskTier:
        """Look up the risk tier for a specific operation."""
        op = self._registry.get_operation(connector_id, operation_id)
        cap_id = op.full_capability_id
        return self._classifier.get_default_tier(cap_id, RiskTier.T3_IRREVERSIBLE)

    def execute_governed(
        self,
        connector_id: str,
        operation_id: str,
        params: Dict[str, Any],
        *,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
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

        soul_denial = self._evaluate_soul_connector_policy(
            connector_id=connector_id,
            operation_id=operation_id,
            capability=cap_id,
            params=params,
        )
        if soul_denial:
            response = ConnectorResponse(
                operation_id=operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error=f"Soul policy denied: {soul_denial}",
            )
            return self._finalize_response(
                response,
                cap_id,
                risk_profile.tier,
                params,
                operator_id=operator_id,
                session_id=session_id,
                quest_id=quest_id,
                parent_receipt_id=parent_receipt_id,
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
                response = ConnectorResponse(
                    operation_id=operation_id,
                    connector_id=connector_id,
                    status_code=0,
                    success=False,
                    error=f"Policy denied: {'; '.join(decision.reasons) if hasattr(decision, 'reasons') else 'Denied'}",
                )
                return self._finalize_response(
                    response,
                    cap_id,
                    risk_profile.tier,
                    params,
                    operator_id=operator_id,
                    session_id=session_id,
                    quest_id=quest_id,
                    parent_receipt_id=parent_receipt_id,
                )

        # 4. Execute
        connector = entry.connector
        try:
            result = connector.execute(operation_id, params)
        except Exception as e:
            response = ConnectorResponse(
                operation_id=operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error=str(e),
            )
            return self._finalize_response(
                response,
                cap_id,
                risk_profile.tier,
                params,
                operator_id=operator_id,
                session_id=session_id,
                quest_id=quest_id,
                parent_receipt_id=parent_receipt_id,
            )

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

        return self._finalize_response(
            response,
            cap_id,
            risk_profile.tier,
            params,
            operator_id=operator_id,
            session_id=session_id,
            quest_id=quest_id,
            parent_receipt_id=parent_receipt_id,
        )

    def _finalize_response(
        self,
        response: ConnectorResponse,
        capability: str,
        risk_tier: RiskTier,
        params: Dict[str, Any],
        *,
        operator_id: Optional[str],
        session_id: Optional[str],
        quest_id: Optional[str],
        parent_receipt_id: Optional[str],
    ) -> ConnectorResponse:
        """Record trust and receipts for every governed terminal decision."""
        if self._trust_ledger is not None:
            try:
                if response.success:
                    self._trust_ledger.record_success(capability, "external")
                else:
                    self._trust_ledger.record_failure(capability, "external")
            except KeyError as exc:
                logger.debug(
                    "No trust record found for connector capability %s during trust update: %s",
                    capability,
                    exc,
                )

        receipt = {
            "receipt_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connector_id": response.connector_id,
            "operation_id": response.operation_id,
            "capability": capability,
            "tier": risk_tier.name,
            "status_code": response.status_code,
            "success": response.success,
            "error": response.error,
        }
        response.receipt_id = receipt["receipt_id"]

        if risk_tier == RiskTier.T0_INERT and self._batch_buffer is not None:
            self._batch_buffer.append(receipt)
        else:
            self._receipt_store.append(receipt)

        if self._receipt_service is not None:
            try:
                persisted = create_receipt(
                    ActionType.TOOL_CALL,
                    f"connector_{response.connector_id}_{response.operation_id}",
                    inputs={
                        "connector_id": response.connector_id,
                        "operation_id": response.operation_id,
                        "params": params,
                        "capability": capability,
                    },
                    tier=CognitionTier.DETERMINISTIC,
                    parent_id=parent_receipt_id,
                    quest_id=quest_id,
                    metadata={
                        "subsystem": "connectors",
                        "risk_tier": risk_tier.name,
                        "status_code": response.status_code,
                    },
                    operator_id=operator_id,
                    session_id=session_id,
                ).complete(
                    outputs={
                        "success": response.success,
                        "status_code": response.status_code,
                        "error": response.error,
                    },
                    duration_ms=0,
                )
                self._receipt_service.create(persisted)
            except Exception as exc:
                logger.warning("Connector receipt persistence failed: %s", exc)

        return response

    def _active_soul(self) -> Any:
        return self._soul or getattr(self._classifier, "_soul", None)

    def _evaluate_soul_connector_policy(
        self,
        *,
        connector_id: str,
        operation_id: str,
        capability: str,
        params: Dict[str, Any],
    ) -> str:
        """Return a denial reason when structured Soul policy blocks a call."""
        soul = self._active_soul()
        if soul is None:
            return ""

        connector_policy = self._get_connector_policy(soul, connector_id)
        if connector_policy is not None:
            denial = self._evaluate_connector_policy(
                connector_id=connector_id,
                operation_id=operation_id,
                policy=connector_policy,
                params=params,
            )
            if denial:
                return denial

        for rule in self._get_soul_list(soul, "external_transmission_rules"):
            applies_to = rule.get("applies_to") or []
            if applies_to and not any(
                self._matches_capability(pattern, capability)
                or self._matches_capability(pattern, operation_id)
                for pattern in applies_to
            ):
                continue
            denial = self._evaluate_external_transmission_rule(rule, params)
            if denial:
                return denial

        if connector_policy is not None and self._looks_outbound(connector_id, operation_id):
            max_sends = connector_policy.get("max_sends_per_day")
            if max_sends is not None:
                denial = self._reserve_daily_send(connector_id, max_sends)
                if denial:
                    return denial

        return ""

    def _evaluate_connector_policy(
        self,
        *,
        connector_id: str,
        operation_id: str,
        policy: Dict[str, Any],
        params: Dict[str, Any],
    ) -> str:
        looks_outbound = self._looks_outbound(connector_id, operation_id)

        verified_recipients = policy.get("verified_recipients") or []
        recipients = self._extract_values(
            params,
            ("to", "recipient", "recipients", "email", "emails", "destination", "destinations"),
        )
        if verified_recipients:
            if looks_outbound and not recipients:
                return f"connector_policies.{connector_id} requires verified recipients"
            for recipient in recipients:
                if not self._matches_any(str(recipient), verified_recipients):
                    return f"recipient '{recipient}' is not allowed by connector_policies.{connector_id}"

        allowed_channels = policy.get("allowed_channels") or []
        channels = self._extract_values(
            params,
            ("channel", "channel_id", "channels", "account", "account_id"),
        )
        if allowed_channels:
            if looks_outbound and not channels:
                return f"connector_policies.{connector_id} requires allowed channel/account"
            for channel in channels:
                if not self._matches_any(str(channel), allowed_channels):
                    return f"channel/account '{channel}' is not allowed by connector_policies.{connector_id}"

        if policy.get("restrict_dm") and (
            params.get("is_dm") is True
            or any(str(channel).upper().startswith("D") for channel in channels)
        ):
            return f"direct messages are restricted by connector_policies.{connector_id}"

        if looks_outbound and policy.get("require_content_verification"):
            if params.get("content_verified") is not True:
                return f"connector_policies.{connector_id} requires content_verified=true"

        if looks_outbound and policy.get("pii_scrubbing_required"):
            if params.get("pii_scrubbed") is not True:
                return f"connector_policies.{connector_id} requires pii_scrubbed=true"

        if looks_outbound and policy.get("approval_required_for_send"):
            if params.get("approved") is not True and not params.get("approval_id"):
                return f"connector_policies.{connector_id} requires approval evidence"

        return ""

    def _evaluate_external_transmission_rule(
        self,
        rule: Dict[str, Any],
        params: Dict[str, Any],
    ) -> str:
        name = rule.get("name", "external_transmission")
        required_tier = self._tier_value(rule.get("requires_approval_tier", "T3"))
        provided_tier = self._tier_value(params.get("approval_tier"))
        has_approval = params.get("approved") is True or bool(params.get("approval_id"))
        if required_tier is not None:
            if not has_approval:
                return f"external_transmission_rules.{name} requires approval"
            if provided_tier is None or provided_tier < required_tier:
                return f"external_transmission_rules.{name} requires approval tier T{required_tier}"

        if rule.get("pii_scrubbing_required", True) and params.get("pii_scrubbed") is not True:
            return f"external_transmission_rules.{name} requires pii_scrubbed=true"

        allowed_destinations = rule.get("allowed_destinations") or []
        destinations = self._extract_values(
            params,
            ("to", "recipient", "recipients", "email", "emails", "destination", "destinations"),
        )
        if allowed_destinations:
            for destination in destinations:
                if not self._matches_any(str(destination), allowed_destinations):
                    return f"destination '{destination}' is not allowed by external_transmission_rules.{name}"

        return ""

    def _get_connector_policy(self, soul: Any, connector_id: str) -> Optional[Dict[str, Any]]:
        policies = self._get_soul_mapping(soul, "connector_policies")
        policy = policies.get(connector_id)
        if policy is None:
            return None
        if isinstance(policy, dict):
            return policy
        if hasattr(policy, "model_dump"):
            return policy.model_dump()
        return None

    def _get_soul_mapping(self, soul: Any, field_name: str) -> Dict[str, Any]:
        value = None
        if isinstance(soul, dict):
            value = soul.get(field_name)
        elif hasattr(soul, field_name):
            value = getattr(soul, field_name)
        return value if isinstance(value, dict) else {}

    def _get_soul_list(self, soul: Any, field_name: str) -> List[Dict[str, Any]]:
        if isinstance(soul, dict):
            value = soul.get(field_name)
        elif hasattr(soul, field_name):
            value = getattr(soul, field_name)
        else:
            value = None
        if not value:
            return []
        items: List[Dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                items.append(item)
            elif hasattr(item, "model_dump"):
                items.append(item.model_dump())
        return items

    def _matches_capability(self, pattern: str, capability: str) -> bool:
        return pattern == capability or fnmatch.fnmatch(capability, pattern)

    def _matches_any(self, value: str, patterns: List[str]) -> bool:
        return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)

    def _looks_outbound(self, connector_id: str, operation_id: str) -> bool:
        operation_text = f"{connector_id}.{operation_id}".lower()
        return any(
            token in operation_text
            for token in ("send", "post", "publish", "message", "outreach", "proposal")
        )

    def _extract_values(self, params: Dict[str, Any], keys: tuple[str, ...]) -> List[Any]:
        values: List[Any] = []
        for key in keys:
            value = params.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                values.extend(value)
            else:
                values.append(value)
        return values

    def _tier_value(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                text = value.strip().upper()
                if text.startswith("T"):
                    text = text[1:]
                return int(text)
            return int(value)
        except (TypeError, ValueError):
            return None

    def _daily_sends_path(self) -> Path:
        data_dir = Path(os.getenv("LANCELOT_DATA_DIR", "data"))
        path = data_dir / "connectors" / _DAILY_SENDS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_daily_sends(self, path: Path) -> Dict[str, Dict[str, int]]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load connector daily send counters: %s", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        result: Dict[str, Dict[str, int]] = {}
        for day, counts in data.items():
            if not isinstance(day, str) or not isinstance(counts, dict):
                continue
            result[day] = {
                str(connector): int(count)
                for connector, count in counts.items()
                if isinstance(count, int) and count >= 0
            }
        return result

    def _save_daily_sends(self, path: Path, data: Dict[str, Dict[str, int]]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _reserve_daily_send(self, connector_id: str, max_sends: Any) -> str:
        try:
            limit = int(max_sends)
        except (TypeError, ValueError):
            return f"connector_policies.{connector_id} has invalid max_sends_per_day"
        if limit < 0:
            return f"connector_policies.{connector_id} has invalid max_sends_per_day"

        today = datetime.now(timezone.utc).date().isoformat()
        with self._daily_send_lock:
            path = self._daily_sends_path()
            data = self._load_daily_sends(path)
            counts = data.setdefault(today, {})
            current = counts.get(connector_id, 0)
            if current >= limit:
                return f"connector_policies.{connector_id} max_sends_per_day exceeded"
            data = {today: counts}
            counts[connector_id] = current + 1
            self._save_daily_sends(path, data)
        return ""

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
        except KeyError as exc:
            logger.debug(
                "No trust record found for connector rollback %s.%s: %s",
                connector_id,
                operation_id,
                exc,
            )
