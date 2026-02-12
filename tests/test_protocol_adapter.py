"""
Tests for ProtocolAdapter — SMTP/IMAP translation layer.

Uses mocked smtplib/imaplib connections. No actual mail server calls.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.connectors.models import ConnectorResult, HTTPMethod
from src.connectors.protocol_adapter import ProtocolAdapter, SMTPConfig, IMAPConfig


@pytest.fixture
def adapter():
    return ProtocolAdapter()


# ── Routing ──────────────────────────────────────────────────────

class TestRouting:
    def test_unknown_protocol_returns_error(self, adapter):
        result = ConnectorResult(
            operation_id="test",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://ftp",
            body={"action": "list"},
        )
        resp = adapter.execute(result)
        assert resp.success is False
        assert "Unknown protocol" in resp.error

    def test_smtp_route(self, adapter):
        result = ConnectorResult(
            operation_id="send_message",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://smtp",
            body={"protocol": "smtp", "action": "send", "to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )
        with patch.object(adapter, "_get_smtp_connection") as mock_conn:
            mock_smtp = MagicMock()
            mock_conn.return_value = mock_smtp
            resp = adapter.execute(result)
        assert resp.success is True
        assert resp.body["status"] == "sent"

    def test_imap_route(self, adapter):
        result = ConnectorResult(
            operation_id="list_messages",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://imap",
            body={"protocol": "imap", "action": "list"},
        )
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_imap.search.return_value = ("OK", [b"1 2 3"])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)
        assert resp.success is True
        assert resp.body["total"] == 3


# ── SMTP Operations ─────────────────────────────────────────────

class TestSMTPOperations:
    def test_send_builds_message(self, adapter):
        result = ConnectorResult(
            operation_id="send_message",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://smtp",
            body={
                "protocol": "smtp", "action": "send",
                "to": "bob@example.com", "subject": "Test", "body": "Hello",
                "mime_type": "text/plain",
            },
        )
        with patch.object(adapter, "_get_smtp_connection") as mock_conn:
            mock_smtp = MagicMock()
            mock_conn.return_value = mock_smtp
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["to"] == "bob@example.com"
        mock_smtp.send_message.assert_called_once()

    def test_reply_sets_in_reply_to(self, adapter):
        result = ConnectorResult(
            operation_id="reply_message",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://smtp",
            body={
                "protocol": "smtp", "action": "reply",
                "to": "bob@example.com", "subject": "Re: Test", "body": "Reply",
                "headers": {"In-Reply-To": "msg1", "References": "msg1"},
            },
        )
        with patch.object(adapter, "_get_smtp_connection") as mock_conn:
            mock_smtp = MagicMock()
            mock_conn.return_value = mock_smtp
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["in_reply_to"] == "msg1"

    def test_unknown_smtp_action_fails(self, adapter):
        result = ConnectorResult(
            operation_id="test",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://smtp",
            body={"protocol": "smtp", "action": "unknown"},
        )
        resp = adapter.execute(result)
        assert resp.success is False
        assert "Unknown SMTP action" in resp.error


# ── IMAP Operations ──────────────────────────────────────────────

class TestIMAPOperations:
    def _make_imap_result(self, action, **extra):
        body = {"protocol": "imap", "action": action}
        body.update(extra)
        return ConnectorResult(
            operation_id=action,
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://imap",
            body=body,
        )

    def test_list_returns_message_ids(self, adapter):
        result = self._make_imap_result("list")
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"5"])
            mock_imap.search.return_value = ("OK", [b"1 2 3 4 5"])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["total"] == 5
        assert len(resp.body["message_ids"]) == 5

    def test_fetch_returns_raw(self, adapter):
        result = self._make_imap_result("fetch", message_id="42")
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_imap.fetch.return_value = ("OK", [(b"42", b"From: a@b.com\nSubject: Hi\n\nHello")])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["message_id"] == "42"
        assert "From: a@b.com" in resp.body["raw"]

    def test_search_returns_matches(self, adapter):
        result = self._make_imap_result("search", query="invoice")
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_imap.search.return_value = ("OK", [b"10 20"])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["query"] == "invoice"
        assert len(resp.body["message_ids"]) == 2

    def test_delete_expunges(self, adapter):
        result = self._make_imap_result("delete", message_id="99")
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["deleted"] == "99"
        mock_imap.store.assert_called_once()
        mock_imap.expunge.assert_called_once()

    def test_move_copies_then_deletes(self, adapter):
        result = self._make_imap_result("move", message_id="5", destination="Archive")
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.success is True
        assert resp.body["destination"] == "Archive"
        mock_imap.copy.assert_called_once_with("5", "Archive")
        mock_imap.expunge.assert_called_once()

    def test_unknown_imap_action_fails(self, adapter):
        result = self._make_imap_result("unknown")
        resp = adapter.execute(result)
        assert resp.success is False
        assert "Unknown IMAP action" in resp.error


# ── Response Metadata ────────────────────────────────────────────

class TestResponseMetadata:
    def test_elapsed_ms_is_positive(self, adapter):
        result = ConnectorResult(
            operation_id="list_messages",
            connector_id="email",
            method=HTTPMethod.POST,
            url="protocol://imap",
            body={"protocol": "imap", "action": "list"},
        )
        with patch.object(adapter, "_get_imap_connection") as mock_conn:
            mock_imap = MagicMock()
            mock_imap.select.return_value = ("OK", [b"0"])
            mock_imap.search.return_value = ("OK", [b""])
            mock_conn.return_value = mock_imap
            resp = adapter.execute(result)

        assert resp.elapsed_ms >= 0
        assert resp.status_code == 200
