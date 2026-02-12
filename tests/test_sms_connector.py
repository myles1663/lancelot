"""
Tests for SMSConnector — Twilio REST API integration.

Tests HTTP request spec production. No actual Twilio API calls.
"""

import pytest

from src.connectors.connectors.sms import SMSConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def sms():
    return SMSConnector(
        account_sid="AC123",
        from_number="+15551234567",
    )


@pytest.fixture
def sms_service():
    """SMS connector using a Messaging Service SID."""
    return SMSConnector(
        account_sid="AC123",
        messaging_service_sid="MG456",
    )


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, sms):
        sms.manifest.validate()

    def test_target_domains(self, sms):
        assert sms.manifest.target_domains == ["api.twilio.com"]

    def test_has_credentials(self, sms):
        assert len(sms.manifest.required_credentials) == 1
        assert sms.manifest.required_credentials[0].vault_key == "sms.twilio_credentials"

    def test_credential_type(self, sms):
        assert sms.manifest.required_credentials[0].type == "basic_auth"

    def test_does_not_access(self, sms):
        dna = sms.manifest.does_not_access
        assert "Voice calls" in dna
        assert "Account billing" in dna


# ── Operation Enumeration ─────────────────────────────────────────

class TestOperations:
    def test_total_operations(self, sms):
        assert len(sms.get_operations()) == 6

    def test_write_operations(self, sms):
        ops = sms.get_operations()
        write_ops = [o for o in ops if o.capability == "connector.write"]
        assert len(write_ops) == 2  # send_sms, send_mms

    def test_read_operations(self, sms):
        ops = sms.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        assert len(read_ops) == 3  # get_message, list_messages, get_media

    def test_delete_operations(self, sms):
        ops = sms.get_operations()
        delete_ops = [o for o in ops if o.capability == "connector.delete"]
        assert len(delete_ops) == 1

    def test_send_sms_is_t3(self, sms):
        ops = {o.id: o for o in sms.get_operations()}
        assert ops["send_sms"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_list_messages_is_t1(self, sms):
        ops = {o.id: o for o in sms.get_operations()}
        assert ops["list_messages"].default_tier == RiskTier.T1_REVERSIBLE


# ── Execute Write Operations ──────────────────────────────────────

class TestWriteExecution:
    def test_send_sms_url(self, sms):
        result = sms.execute("send_sms", {"to": "+1987654", "body": "Hello"})
        assert result.method == HTTPMethod.POST
        assert "/Accounts/AC123/Messages.json" in result.url

    def test_send_sms_form_encoded_header(self, sms):
        result = sms.execute("send_sms", {"to": "+1987654", "body": "Hello"})
        assert result.headers.get("Content-Type") == "application/x-www-form-urlencoded"

    def test_send_sms_body_contains_fields(self, sms):
        result = sms.execute("send_sms", {"to": "+1987654", "body": "Hello"})
        assert "To=%2B1987654" in result.body
        assert "Body=Hello" in result.body
        assert "From=%2B15551234567" in result.body

    def test_send_sms_with_messaging_service(self, sms_service):
        result = sms_service.execute("send_sms", {"to": "+1987654", "body": "Hello"})
        assert "MessagingServiceSid=MG456" in result.body
        assert "From=" not in result.body

    def test_send_sms_has_billable_metadata(self, sms):
        result = sms.execute("send_sms", {"to": "+1987654", "body": "Hello"})
        assert result.metadata.get("billable") is True

    def test_send_mms_body(self, sms):
        result = sms.execute("send_mms", {
            "to": "+1987654", "body": "Photo", "media_url": "https://example.com/photo.jpg",
        })
        assert "MediaUrl=" in result.body
        assert result.metadata.get("billable") is True


# ── Execute Read Operations ───────────────────────────────────────

class TestReadExecution:
    def test_get_message_url(self, sms):
        result = sms.execute("get_message", {"message_sid": "SM123"})
        assert result.method == HTTPMethod.GET
        assert "/Messages/SM123.json" in result.url

    def test_list_messages_url(self, sms):
        result = sms.execute("list_messages", {})
        assert result.method == HTTPMethod.GET
        assert "/Messages.json" in result.url

    def test_list_messages_with_filters(self, sms):
        result = sms.execute("list_messages", {"to": "+1234"})
        assert "To=%2B1234" in result.url

    def test_get_media_url(self, sms):
        result = sms.execute("get_media", {
            "message_sid": "SM123", "media_sid": "ME456",
        })
        assert "/Messages/SM123/Media/ME456.json" in result.url

    def test_delete_message_url(self, sms):
        result = sms.execute("delete_message", {"message_sid": "SM123"})
        assert result.method == HTTPMethod.DELETE
        assert "/Messages/SM123.json" in result.url

    def test_all_read_results_have_credential_key(self, sms):
        for op_id, params in [
            ("get_message", {"message_sid": "SM1"}),
            ("list_messages", {}),
            ("get_media", {"message_sid": "SM1", "media_sid": "ME1"}),
        ]:
            result = sms.execute(op_id, params)
            assert result.credential_vault_key == "sms.twilio_credentials"

    def test_unknown_operation_raises(self, sms):
        with pytest.raises(KeyError):
            sms.execute("unknown_op", {})


# ── Credential Validation ─────────────────────────────────────────

class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, sms):
        assert sms.validate_credentials() is False
