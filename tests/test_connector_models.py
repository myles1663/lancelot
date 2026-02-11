"""
Tests for Prompt 26: ConnectorOperation + ConnectorResult Models.
"""

import pytest

from src.connectors.models import (
    ConnectorOperation,
    ConnectorResponse,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


# ── ConnectorOperation ────────────────────────────────────────────

class TestConnectorOperation:
    def test_valid_data_passes_validate(self):
        op = ConnectorOperation(
            id="read_messages",
            connector_id="slack",
            capability="connector.read",
            name="Read Messages",
        )
        op.validate()  # should not raise

    def test_full_capability_id(self):
        op = ConnectorOperation(
            id="read_messages",
            connector_id="slack",
            capability="connector.read",
            name="Read Messages",
        )
        assert op.full_capability_id == "connector.slack.read_messages"

    def test_validate_raises_for_empty_id(self):
        op = ConnectorOperation(
            id="",
            connector_id="slack",
            capability="connector.read",
            name="Read",
        )
        with pytest.raises(ValueError, match="id must not be empty"):
            op.validate()

    def test_validate_raises_for_empty_connector_id(self):
        op = ConnectorOperation(
            id="read",
            connector_id="",
            capability="connector.read",
            name="Read",
        )
        with pytest.raises(ValueError, match="connector_id must not be empty"):
            op.validate()

    def test_validate_raises_for_invalid_capability(self):
        op = ConnectorOperation(
            id="read",
            connector_id="slack",
            capability="connector.execute",
            name="Read",
        )
        with pytest.raises(ValueError, match="capability must be one of"):
            op.validate()

    def test_is_frozen(self):
        op = ConnectorOperation(
            id="read",
            connector_id="slack",
            capability="connector.read",
            name="Read",
        )
        with pytest.raises(AttributeError):
            op.id = "changed"  # type: ignore

    def test_default_tier_is_t2(self):
        op = ConnectorOperation(
            id="read",
            connector_id="slack",
            capability="connector.read",
            name="Read",
        )
        assert op.default_tier == RiskTier.T2_CONTROLLED


# ── ParameterSpec ─────────────────────────────────────────────────

class TestParameterSpec:
    def test_is_frozen(self):
        spec = ParameterSpec(name="channel", type="str")
        with pytest.raises(AttributeError):
            spec.name = "changed"  # type: ignore

    def test_stores_fields(self):
        spec = ParameterSpec(
            name="channel",
            type="str",
            required=False,
            description="Channel name",
            default="#general",
        )
        assert spec.name == "channel"
        assert spec.type == "str"
        assert spec.required is False
        assert spec.description == "Channel name"
        assert spec.default == "#general"


# ── ConnectorResult ───────────────────────────────────────────────

class TestConnectorResult:
    def test_get_with_no_body_passes(self):
        r = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://slack.com/api/conversations.list",
        )
        r.validate()  # should not raise

    def test_get_with_body_raises(self):
        r = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://slack.com/api/conversations.list",
            body={"x": 1},
        )
        with pytest.raises(ValueError, match="body must be None for GET"):
            r.validate()

    def test_delete_with_body_raises(self):
        r = ConnectorResult(
            operation_id="delete",
            connector_id="slack",
            method=HTTPMethod.DELETE,
            url="https://slack.com/api/conversations.delete",
            body={"id": "123"},
        )
        with pytest.raises(ValueError, match="body must be None for DELETE"):
            r.validate()

    def test_empty_url_raises(self):
        r = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="",
        )
        with pytest.raises(ValueError, match="url must not be empty"):
            r.validate()

    def test_timeout_zero_raises(self):
        r = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://slack.com/api/test",
            timeout_seconds=0,
        )
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            r.validate()

    def test_post_with_body_passes(self):
        r = ConnectorResult(
            operation_id="send",
            connector_id="slack",
            method=HTTPMethod.POST,
            url="https://slack.com/api/chat.postMessage",
            body={"channel": "#general", "text": "hello"},
        )
        r.validate()  # should not raise


# ── ConnectorResponse ────────────────────────────────────────────

class TestConnectorResponse:
    def test_is_error_false_for_200_success(self):
        resp = ConnectorResponse(
            operation_id="read",
            connector_id="slack",
            status_code=200,
            success=True,
        )
        assert resp.is_error is False

    def test_is_error_true_for_500(self):
        resp = ConnectorResponse(
            operation_id="read",
            connector_id="slack",
            status_code=500,
            success=True,
        )
        assert resp.is_error is True

    def test_is_error_true_for_success_false(self):
        resp = ConnectorResponse(
            operation_id="read",
            connector_id="slack",
            status_code=200,
            success=False,
            error="Connection timeout",
        )
        assert resp.is_error is True

    def test_is_error_true_for_404(self):
        resp = ConnectorResponse(
            operation_id="read",
            connector_id="slack",
            status_code=404,
            success=True,
        )
        assert resp.is_error is True


# ── HTTPMethod ────────────────────────────────────────────────────

class TestHTTPMethod:
    def test_has_five_values(self):
        assert len(HTTPMethod) == 5

    def test_values(self):
        assert HTTPMethod.GET == "GET"
        assert HTTPMethod.POST == "POST"
        assert HTTPMethod.PUT == "PUT"
        assert HTTPMethod.PATCH == "PATCH"
        assert HTTPMethod.DELETE == "DELETE"
