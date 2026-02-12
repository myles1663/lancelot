"""
Tests for EmailConnector — SMTP/IMAP backend.

Tests protocol adapter request spec production. No actual SMTP/IMAP calls.
"""

import pytest

from src.connectors.connectors.email import EmailConnector
from src.connectors.models import HTTPMethod


@pytest.fixture
def smtp():
    return EmailConnector(backend="smtp")


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, smtp):
        smtp.manifest.validate()

    def test_target_domains(self, smtp):
        assert "protocol.smtp" in smtp.manifest.target_domains
        assert "protocol.imap" in smtp.manifest.target_domains

    def test_credential_key(self, smtp):
        assert smtp.manifest.required_credentials[0].vault_key == "email.smtp_credentials"

    def test_credential_type(self, smtp):
        assert smtp.manifest.required_credentials[0].type == "basic_auth"

    def test_backend_property(self, smtp):
        assert smtp.backend == "smtp"


# ── IMAP Read Operations ─────────────────────────────────────────

class TestImapReadExecution:
    def test_list_messages_uses_protocol_url(self, smtp):
        result = smtp.execute("list_messages", {})
        assert result.url == "protocol://imap"
        assert result.body["protocol"] == "imap"
        assert result.body["action"] == "list"

    def test_list_messages_has_protocol_metadata(self, smtp):
        result = smtp.execute("list_messages", {})
        assert result.metadata.get("protocol_adapter") is True

    def test_get_message_body(self, smtp):
        result = smtp.execute("get_message", {"message_id": "123"})
        assert result.url == "protocol://imap"
        assert result.body["action"] == "fetch"
        assert result.body["message_id"] == "123"

    def test_search_messages_body(self, smtp):
        result = smtp.execute("search_messages", {"query": "invoice"})
        assert result.body["action"] == "search"
        assert result.body["query"] == "invoice"

    def test_delete_message_body(self, smtp):
        result = smtp.execute("delete_message", {"message_id": "123"})
        assert result.url == "protocol://imap"
        assert result.body["action"] == "delete"
        assert result.body["message_id"] == "123"

    def test_move_to_folder_body(self, smtp):
        result = smtp.execute("move_to_folder", {"message_id": "123", "label_id": "INBOX"})
        assert result.body["action"] == "move"
        assert result.body["destination"] == "INBOX"

    def test_all_imap_ops_use_smtp_credentials(self, smtp):
        for op_id, params in [
            ("list_messages", {}),
            ("get_message", {"message_id": "x"}),
            ("search_messages", {"query": "test"}),
            ("delete_message", {"message_id": "x"}),
            ("move_to_folder", {"message_id": "x", "label_id": "y"}),
        ]:
            result = smtp.execute(op_id, params)
            assert result.credential_vault_key == "email.smtp_credentials"


# ── SMTP Write Operations ────────────────────────────────────────

class TestSmtpWriteExecution:
    def test_send_message_uses_protocol_url(self, smtp):
        result = smtp.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello",
        })
        assert result.url == "protocol://smtp"
        assert result.body["protocol"] == "smtp"
        assert result.body["action"] == "send"

    def test_send_message_body_fields(self, smtp):
        result = smtp.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello",
        })
        assert result.body["to"] == "bob@example.com"
        assert result.body["subject"] == "Test"
        assert result.body["body"] == "Hello"
        assert result.body["mime_type"] == "text/plain"

    def test_send_message_with_cc(self, smtp):
        result = smtp.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello", "cc": "cc@example.com",
        })
        assert result.body["cc"] == "cc@example.com"

    def test_reply_message_has_in_reply_to(self, smtp):
        result = smtp.execute("reply_message", {
            "message_id": "msg1", "thread_id": "t1", "body": "Reply",
        })
        assert result.url == "protocol://smtp"
        assert result.body["headers"]["In-Reply-To"] == "msg1"

    def test_all_smtp_ops_have_protocol_metadata(self, smtp):
        result = smtp.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello",
        })
        assert result.metadata.get("protocol_adapter") is True

    def test_unknown_operation_raises(self, smtp):
        with pytest.raises(KeyError):
            smtp.execute("unknown_op", {})
