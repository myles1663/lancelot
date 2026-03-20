"""Tests for governance receipt emission helper (governance_receipts.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import src.core.governance_receipts as gov_receipts
from src.core.governance_receipts import (
    init_governance_receipts,
    emit_governance_receipt,
)
from src.core.operator_identity import OperatorIdentity, resolve_operator_id
from src.shared.receipts import ActionType, Receipt, ReceiptStatus


def _make_mock_request(token="test-token-123", has_client=True):
    """Create a mock FastAPI Request with authorization header."""
    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}
    if has_client:
        client = MagicMock()
        client.host = "127.0.0.1"
        request.client = client
    else:
        request.client = None
    return request


def _make_identity(username="testuser"):
    return OperatorIdentity(
        operator_id=resolve_operator_id(username),
        display_name=username,
        session_id="sess-1",
        session_started_at="2026-01-01T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )


# ── init_governance_receipts ──────────────────────────────────────


class TestInitGovernanceReceipts:

    def setup_method(self):
        gov_receipts._receipt_service = None

    def test_stores_receipt_service(self):
        mock_svc = MagicMock()
        init_governance_receipts(mock_svc)
        assert gov_receipts._receipt_service is mock_svc

    def test_overwrites_previous_service(self):
        svc1 = MagicMock()
        svc2 = MagicMock()
        init_governance_receipts(svc1)
        init_governance_receipts(svc2)
        assert gov_receipts._receipt_service is svc2


# ── emit_governance_receipt ──────────────────────────────────────


class TestEmitGovernanceReceipt:

    def setup_method(self):
        gov_receipts._receipt_service = None

    def test_returns_none_when_service_not_initialised(self):
        request = _make_mock_request()
        result = emit_governance_receipt(
            request, ActionType.KILL_SWITCH_ISSUED, action_name="toggle"
        )
        assert result is None

    @patch("src.core.governance_receipts._resolve_identity")
    def test_creates_receipt_with_correct_fields(self, mock_resolve):
        identity = _make_identity("alice")
        mock_resolve.return_value = identity

        mock_svc = MagicMock()
        mock_receipt = MagicMock()
        mock_svc.create.return_value = mock_receipt
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        result = emit_governance_receipt(
            request,
            ActionType.KILL_SWITCH_ISSUED,
            action_name="toggle_flag",
            inputs={"flag": "GLOBAL_KILL", "value": False},
        )

        assert result is mock_receipt
        mock_svc.create.assert_called_once()
        created_receipt = mock_svc.create.call_args[0][0]
        assert created_receipt.action_type == ActionType.KILL_SWITCH_ISSUED.value
        assert created_receipt.action_name == "toggle_flag"
        assert created_receipt.inputs == {"flag": "GLOBAL_KILL", "value": False}
        assert created_receipt.operator_id == identity.operator_id
        assert created_receipt.session_id == identity.session_id
        assert created_receipt.status == ReceiptStatus.SUCCESS.value

    @patch("src.core.governance_receipts._resolve_identity")
    def test_passes_outputs_and_metadata(self, mock_resolve):
        mock_resolve.return_value = _make_identity()
        mock_svc = MagicMock()
        mock_svc.create.return_value = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        emit_governance_receipt(
            request,
            ActionType.CONNECTOR_ENABLED,
            action_name="enable_slack",
            inputs={"connector": "slack"},
            outputs={"status": "enabled"},
            metadata={"reason": "test"},
        )

        created_receipt = mock_svc.create.call_args[0][0]
        assert created_receipt.outputs == {"status": "enabled"}
        assert created_receipt.metadata == {"reason": "test"}

    @patch("src.core.governance_receipts._resolve_identity")
    def test_passes_quest_id(self, mock_resolve):
        mock_resolve.return_value = _make_identity()
        mock_svc = MagicMock()
        mock_svc.create.return_value = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        emit_governance_receipt(
            request,
            ActionType.T3_APPROVED,
            action_name="approve",
            quest_id="quest-abc",
        )

        created_receipt = mock_svc.create.call_args[0][0]
        assert created_receipt.quest_id == "quest-abc"

    @patch("src.core.governance_receipts._resolve_identity")
    def test_non_throwing_on_service_exception(self, mock_resolve):
        mock_resolve.return_value = _make_identity()
        mock_svc = MagicMock()
        mock_svc.create.side_effect = RuntimeError("DB connection lost")
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        result = emit_governance_receipt(
            request,
            ActionType.KILL_SWITCH_ISSUED,
            action_name="toggle",
        )
        assert result is None

    @patch("src.core.governance_receipts._resolve_identity")
    def test_non_throwing_on_identity_exception(self, mock_resolve):
        mock_resolve.side_effect = Exception("identity resolution failed")
        mock_svc = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        result = emit_governance_receipt(
            request, ActionType.SOUL_UPDATED, action_name="update"
        )
        assert result is None

    @patch("src.core.governance_receipts._resolve_identity")
    def test_default_inputs_outputs_metadata(self, mock_resolve):
        mock_resolve.return_value = _make_identity()
        mock_svc = MagicMock()
        mock_svc.create.return_value = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        emit_governance_receipt(
            request, ActionType.AGENT_DEPLOYED, action_name="deploy"
        )

        created_receipt = mock_svc.create.call_args[0][0]
        assert created_receipt.inputs == {}
        assert created_receipt.outputs == {}
        assert created_receipt.metadata == {}
        assert created_receipt.quest_id is None

    @patch("src.core.governance_receipts._resolve_identity")
    def test_various_action_types(self, mock_resolve):
        mock_resolve.return_value = _make_identity()
        mock_svc = MagicMock()
        mock_svc.create.return_value = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        action_types = [
            ActionType.CREDENTIAL_REGISTERED,
            ActionType.SCHEDULER_TASK_CREATED,
            ActionType.APL_RULE_APPROVED,
            ActionType.CONNECTOR_DISABLED,
        ]
        for at in action_types:
            emit_governance_receipt(request, at, action_name=f"test_{at.value}")

        assert mock_svc.create.call_count == len(action_types)

    @patch("src.core.governance_receipts._resolve_identity")
    def test_operator_id_extracted_from_identity(self, mock_resolve):
        identity = _make_identity("myles")
        mock_resolve.return_value = identity

        mock_svc = MagicMock()
        mock_svc.create.return_value = MagicMock()
        init_governance_receipts(mock_svc)

        request = _make_mock_request()
        emit_governance_receipt(
            request, ActionType.TOOL_ENABLED, action_name="enable"
        )

        created_receipt = mock_svc.create.call_args[0][0]
        assert created_receipt.operator_id == identity.operator_id
        assert created_receipt.operator_id == resolve_operator_id("myles")


# ── _resolve_identity ────────────────────────────────────────────


class TestResolveIdentityInternal:

    @patch("src.core.auth_api.get_api_key_identity")
    @patch("src.core.auth_api.resolve_operator_identity")
    def test_prefers_session_identity(self, mock_session, mock_api_key):
        from src.core.governance_receipts import _resolve_identity

        session_ident = _make_identity("session_user")
        mock_session.return_value = session_ident

        request = _make_mock_request()
        result = _resolve_identity(request)

        assert result is session_ident
        mock_api_key.assert_not_called()

    @patch("src.core.auth_api.get_api_key_identity")
    @patch("src.core.auth_api.resolve_operator_identity")
    def test_falls_back_to_api_key(self, mock_session, mock_api_key):
        from src.core.governance_receipts import _resolve_identity

        mock_session.return_value = None
        api_ident = _make_identity("api_user")
        mock_api_key.return_value = api_ident

        request = _make_mock_request()
        result = _resolve_identity(request)

        assert result is api_ident
        mock_api_key.assert_called_once_with(request)

    @patch("src.core.auth_api.get_api_key_identity")
    @patch("src.core.auth_api.resolve_operator_identity")
    def test_returns_none_when_both_return_none(self, mock_session, mock_api_key):
        from src.core.governance_receipts import _resolve_identity

        mock_session.return_value = None
        mock_api_key.return_value = None

        request = _make_mock_request()
        result = _resolve_identity(request)
        assert result is None
