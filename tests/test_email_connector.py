"""
Tests for Prompts 35-36: EmailConnector (Read + Write).

Tests HTTP request spec production. No actual Gmail API calls.
"""

import base64
import os
import pytest

from src.connectors.connectors.email import EmailConnector
from src.connectors.models import HTTPMethod
from src.core import feature_flags
from src.core.governance.models import RiskTier


@pytest.fixture
def email():
    return EmailConnector()


@pytest.fixture(autouse=True)
def enable_google_oauth(monkeypatch):
    old = os.environ.get("FEATURE_GOOGLE_OAUTH")
    monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "true")
    feature_flags.reload_flags()
    yield
    if old is None:
        monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
    else:
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
    feature_flags.reload_flags()


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, email):
        email.manifest.validate()

    def test_target_domains(self, email):
        assert email.manifest.target_domains == ["gmail.googleapis.com"]

    def test_has_credentials(self, email):
        assert len(email.manifest.required_credentials) == 1
        assert email.manifest.required_credentials[0].vault_key == "email.gmail_token"


# ── Read Operations (P35) ────────────────────────────────────────

class TestReadOperations:
    def test_has_three_read_ops(self, email):
        ops = email.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        assert len(read_ops) == 3

    def test_all_read_ops_are_t1(self, email):
        ops = email.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        for op in read_ops:
            assert op.default_tier == RiskTier.T1_REVERSIBLE

    def test_list_messages_produces_get(self, email):
        result = email.execute("list_messages", {})
        assert result.method == HTTPMethod.GET
        assert "/users/me/messages" in result.url

    def test_list_messages_with_query(self, email):
        result = email.execute("list_messages", {"query": "from:bob"})
        assert "q=from" in result.url

    def test_get_message_includes_id(self, email):
        result = email.execute("get_message", {"message_id": "abc123"})
        assert "/abc123" in result.url
        assert "format=full" in result.url

    def test_search_messages(self, email):
        result = email.execute("search_messages", {"query": "invoice"})
        assert "q=invoice" in result.url

    def test_all_read_results_have_credential_key(self, email):
        for op_id in ("list_messages", "get_message", "search_messages"):
            params = {"message_id": "x", "query": "test"} if op_id != "list_messages" else {}
            if op_id == "get_message":
                params = {"message_id": "x"}
            elif op_id == "search_messages":
                params = {"query": "test"}
            result = email.execute(op_id, params)
            assert result.credential_vault_key == "email.gmail_token"

    def test_all_read_results_are_get(self, email):
        for op_id, params in [
            ("list_messages", {}),
            ("get_message", {"message_id": "x"}),
            ("search_messages", {"query": "test"}),
        ]:
            result = email.execute(op_id, params)
            assert result.method == HTTPMethod.GET

    def test_unknown_operation_raises(self, email):
        with pytest.raises(KeyError):
            email.execute("unknown_op", {})


# ── Write Operations (P36) ───────────────────────────────────────

class TestWriteOperations:
    def test_total_operations(self, email):
        assert len(email.get_operations()) == 7

    def test_send_message_is_write_t3(self, email):
        ops = {o.id: o for o in email.get_operations()}
        assert ops["send_message"].capability == "connector.write"
        assert ops["send_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_delete_message_is_delete_t3(self, email):
        ops = {o.id: o for o in email.get_operations()}
        assert ops["delete_message"].capability == "connector.delete"
        assert ops["delete_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_move_to_folder_is_write_t2(self, email):
        ops = {o.id: o for o in email.get_operations()}
        assert ops["move_to_folder"].capability == "connector.write"
        assert ops["move_to_folder"].default_tier == RiskTier.T2_CONTROLLED

    def test_send_message_produces_post_with_base64(self, email):
        result = email.execute("send_message", {
            "to": "bob@example.com",
            "subject": "Test",
            "body": "Hello",
        })
        assert result.method == HTTPMethod.POST
        assert "/users/me/messages" in result.url
        assert "raw" in result.body
        # Verify base64 decodes
        decoded = base64.urlsafe_b64decode(result.body["raw"])
        assert b"bob@example.com" in decoded
        assert b"Test" in decoded

    def test_reply_message_has_thread_id(self, email):
        result = email.execute("reply_message", {
            "message_id": "msg1",
            "thread_id": "thread1",
            "body": "Reply text",
        })
        assert result.method == HTTPMethod.POST
        assert result.body["threadId"] == "thread1"

    def test_delete_message_produces_delete(self, email):
        result = email.execute("delete_message", {"message_id": "msg1"})
        assert result.method == HTTPMethod.DELETE
        assert "/msg1" in result.url

    def test_move_to_folder_produces_post_modify(self, email):
        result = email.execute("move_to_folder", {
            "message_id": "msg1",
            "label_id": "INBOX",
        })
        assert result.method == HTTPMethod.POST
        assert "/msg1/modify" in result.url
        assert result.body == {"addLabelIds": ["INBOX"]}

    def test_send_not_idempotent_not_reversible(self, email):
        ops = {o.id: o for o in email.get_operations()}
        assert ops["send_message"].idempotent is False
        assert ops["send_message"].reversible is False

    def test_move_is_idempotent_and_reversible(self, email):
        ops = {o.id: o for o in email.get_operations()}
        assert ops["move_to_folder"].idempotent is True
        assert ops["move_to_folder"].reversible is True


# ── Credential Validation ─────────────────────────────────────────

class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, email):
        assert email.validate_credentials() is False

    def test_gmail_backend_disabled_when_google_oauth_flag_off(self, monkeypatch):
        old = os.environ.get("FEATURE_GOOGLE_OAUTH")
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "false")
        feature_flags.reload_flags()

        class _Vault:
            def exists(self, key):
                return key == "email.gmail_token"

        try:
            email = EmailConnector(vault=_Vault())
            assert email.validate_credentials() is False
            with pytest.raises(RuntimeError, match="Google OAuth is disabled"):
                email.execute("list_messages", {})
        finally:
            if old is None:
                monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
            else:
                monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
            feature_flags.reload_flags()

    def test_outlook_backend_unaffected_when_google_oauth_flag_off(self, monkeypatch):
        old = os.environ.get("FEATURE_GOOGLE_OAUTH")
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "false")
        feature_flags.reload_flags()

        class _Vault:
            def exists(self, key):
                return key == "email.outlook_token"

        try:
            email = EmailConnector(backend="outlook", vault=_Vault())
            assert email.validate_credentials() is True
            result = email.execute("list_messages", {})
            assert result.method == HTTPMethod.GET
        finally:
            if old is None:
                monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
            else:
                monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
            feature_flags.reload_flags()
