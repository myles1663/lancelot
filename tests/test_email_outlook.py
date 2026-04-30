"""
Tests for EmailConnector — Outlook/Microsoft Graph backend.

Tests HTTP request spec production. No actual Graph API calls.
"""

import pytest

from src.connectors.connectors.email import EmailConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def outlook():
    return EmailConnector(backend="outlook")


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, outlook):
        outlook.manifest.validate()

    def test_target_domains(self, outlook):
        assert outlook.manifest.target_domains == ["graph.microsoft.com"]

    def test_credential_key(self, outlook):
        assert outlook.manifest.required_credentials[0].vault_key == "email.outlook_token"

    def test_credential_scopes(self, outlook):
        scopes = outlook.manifest.required_credentials[0].scopes
        assert "Mail.Read" in scopes
        assert "Mail.Send" in scopes

    def test_backend_property(self, outlook):
        assert outlook.backend == "outlook"

    def test_does_not_access_teams(self, outlook):
        assert "Teams messages" in outlook.manifest.does_not_access


# ── Operations ────────────────────────────────────────────────────

class TestOperations:
    def test_total_operations(self, outlook):
        assert len(outlook.get_operations()) == 7

    def test_same_ops_as_gmail(self, outlook):
        gmail = EmailConnector(backend="gmail")
        outlook_ids = {o.id for o in outlook.get_operations()}
        gmail_ids = {o.id for o in gmail.get_operations()}
        assert outlook_ids == gmail_ids


# ── Outlook Read Execution ────────────────────────────────────────

class TestOutlookReadExecution:
    def test_list_messages_url(self, outlook):
        result = outlook.execute("list_messages", {})
        assert result.method == HTTPMethod.GET
        assert "/me/messages" in result.url
        assert "$top=" in result.url

    def test_list_messages_with_query(self, outlook):
        result = outlook.execute("list_messages", {"query": "invoice"})
        assert "$filter=contains" in result.url

    def test_get_message_url(self, outlook):
        result = outlook.execute("get_message", {"message_id": "abc123"})
        assert "/me/messages/abc123" in result.url
        assert result.method == HTTPMethod.GET

    def test_search_messages_uses_search_param(self, outlook):
        result = outlook.execute("search_messages", {"query": "invoice"})
        assert '$search="invoice"' in result.url
        assert "$top=" in result.url

    def test_all_reads_use_outlook_token(self, outlook):
        for op_id, params in [
            ("list_messages", {}),
            ("get_message", {"message_id": "x"}),
            ("search_messages", {"query": "test"}),
        ]:
            result = outlook.execute(op_id, params)
            assert result.credential_vault_key == "email.outlook_token"


# ── Outlook Write Execution ───────────────────────────────────────

class TestOutlookWriteExecution:
    def test_send_message_uses_sendmail_endpoint(self, outlook):
        result = outlook.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello",
        })
        assert result.method == HTTPMethod.POST
        assert "/me/sendMail" in result.url

    def test_send_message_body_structure(self, outlook):
        result = outlook.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hello",
        })
        msg = result.body["message"]
        assert msg["subject"] == "Test"
        assert msg["body"]["contentType"] == "Text"
        assert msg["body"]["content"] == "Hello"
        assert msg["toRecipients"][0]["emailAddress"]["address"] == "bob@example.com"

    def test_send_message_with_cc(self, outlook):
        result = outlook.execute("send_message", {
            "to": "bob@example.com", "subject": "Test", "body": "Hi", "cc": "cc@example.com",
        })
        assert "ccRecipients" in result.body["message"]

    def test_reply_message_uses_reply_endpoint(self, outlook):
        result = outlook.execute("reply_message", {
            "message_id": "msg1", "thread_id": "t1", "body": "Reply text",
        })
        assert result.method == HTTPMethod.POST
        assert "/me/messages/msg1/reply" in result.url
        assert result.body == {"comment": "Reply text"}

    def test_delete_message(self, outlook):
        result = outlook.execute("delete_message", {"message_id": "msg1"})
        assert result.method == HTTPMethod.DELETE
        assert "/me/messages/msg1" in result.url

    def test_move_to_folder_uses_move_endpoint(self, outlook):
        result = outlook.execute("move_to_folder", {
            "message_id": "msg1", "label_id": "Inbox",
        })
        assert result.method == HTTPMethod.POST
        assert "/me/messages/msg1/move" in result.url
        assert result.body == {"destinationId": "Inbox"}

    def test_unknown_operation_raises(self, outlook):
        with pytest.raises(KeyError):
            outlook.execute("unknown_op", {})


# ── Backend Selection ─────────────────────────────────────────────

class TestBackendSelection:
    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown email backend"):
            EmailConnector(backend="invalid")

    def test_gmail_default(self):
        email = EmailConnector()
        assert email.backend == "gmail"
        assert "gmail.googleapis.com" in email.manifest.target_domains

    def test_outlook_backend(self):
        email = EmailConnector(backend="outlook")
        assert email.backend == "outlook"
        assert "graph.microsoft.com" in email.manifest.target_domains

    def test_smtp_backend(self):
        email = EmailConnector(backend="smtp")
        assert email.backend == "smtp"
        assert "protocol.smtp" in email.manifest.target_domains
