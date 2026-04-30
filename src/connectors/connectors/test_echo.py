"""
Echo Test Connector — Integration testing connector targeting httpbin.org.

Used for end-to-end testing of the connector pipeline.
No credentials required.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.connectors.base import ConnectorBase, ConnectorManifest
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class EchoConnector(ConnectorBase):
    """Test connector targeting httpbin.org for integration testing."""

    def __init__(self) -> None:
        manifest = ConnectorManifest(
            id="echo",
            name="Echo Test Connector",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Integration test connector — echoes requests via httpbin.org",
            target_domains=["httpbin.org"],
            required_credentials=[],
        )
        super().__init__(manifest)

    def get_operations(self) -> List[ConnectorOperation]:
        return [
            ConnectorOperation(
                id="get_anything",
                connector_id="echo",
                capability="connector.read",
                name="Get Anything",
                description="Echo back any request data",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
            ),
            ConnectorOperation(
                id="post_data",
                connector_id="echo",
                capability="connector.write",
                name="Post Data",
                description="Post data and get echo response",
                default_tier=RiskTier.T2_CONTROLLED,
                parameters=[
                    ParameterSpec(name="data", type="dict", required=False),
                ],
            ),
            ConnectorOperation(
                id="get_status",
                connector_id="echo",
                capability="connector.read",
                name="Get Status",
                description="Get specific HTTP status code response",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="code", type="int", required=True, default=200),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        if operation_id == "get_anything":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="echo",
                method=HTTPMethod.GET,
                url="https://httpbin.org/anything",
            )
        elif operation_id == "post_data":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="echo",
                method=HTTPMethod.POST,
                url="https://httpbin.org/post",
                body=params.get("data", {}),
            )
        elif operation_id == "get_status":
            code = params.get("code", 200)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="echo",
                method=HTTPMethod.GET,
                url=f"https://httpbin.org/status/{code}",
            )
        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        return True  # No credentials needed
