"""
Tests for WhatsAppConnector — Meta Cloud API integration.

Tests HTTP request spec production. No actual WhatsApp API calls.
"""

import pytest

from src.connectors.connectors.whatsapp import WhatsAppConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def whatsapp():
    return WhatsAppConnector(phone_number_id="123456789")


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, whatsapp):
        whatsapp.manifest.validate()

    def test_target_domains(self, whatsapp):
        assert whatsapp.manifest.target_domains == ["graph.facebook.com"]

    def test_has_credentials(self, whatsapp):
        assert len(whatsapp.manifest.required_credentials) == 1
        assert whatsapp.manifest.required_credentials[0].vault_key == "whatsapp.access_token"

    def test_does_not_access(self, whatsapp):
        dna = whatsapp.manifest.does_not_access
        assert "Payment information" in dna


# ── Operation Enumeration ─────────────────────────────────────────

class TestOperations:
    def test_total_operations(self, whatsapp):
        assert len(whatsapp.get_operations()) == 8

    def test_write_operations(self, whatsapp):
        ops = whatsapp.get_operations()
        write_ops = [o for o in ops if o.capability == "connector.write"]
        assert len(write_ops) == 6  # 4 send + mark_read + upload_media

    def test_read_operations(self, whatsapp):
        ops = whatsapp.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        assert len(read_ops) == 2  # get_media + get_business_profile

    def test_send_text_is_t3(self, whatsapp):
        ops = {o.id: o for o in whatsapp.get_operations()}
        assert ops["send_text_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_send_template_is_t2(self, whatsapp):
        ops = {o.id: o for o in whatsapp.get_operations()}
        assert ops["send_template_message"].default_tier == RiskTier.T2_CONTROLLED

    def test_mark_read_is_t0(self, whatsapp):
        ops = {o.id: o for o in whatsapp.get_operations()}
        assert ops["mark_read"].default_tier == RiskTier.T0_INERT

    def test_get_business_profile_is_t0(self, whatsapp):
        ops = {o.id: o for o in whatsapp.get_operations()}
        assert ops["get_business_profile"].default_tier == RiskTier.T0_INERT

    def test_upload_media_is_t2(self, whatsapp):
        ops = {o.id: o for o in whatsapp.get_operations()}
        assert ops["upload_media"].default_tier == RiskTier.T2_CONTROLLED


# ── Execute Operations ────────────────────────────────────────────

class TestExecution:
    def test_send_text_message_body(self, whatsapp):
        result = whatsapp.execute("send_text_message", {
            "to": "+1234567890", "text": "Hello!",
        })
        assert result.method == HTTPMethod.POST
        assert "/123456789/messages" in result.url
        assert result.body["messaging_product"] == "whatsapp"
        assert result.body["type"] == "text"
        assert result.body["text"]["body"] == "Hello!"
        assert result.body["to"] == "+1234567890"

    def test_send_text_has_template_metadata(self, whatsapp):
        result = whatsapp.execute("send_text_message", {
            "to": "+1234567890", "text": "Hello!",
        })
        assert result.metadata.get("requires_template_outside_window") is True

    def test_send_template_message_body(self, whatsapp):
        result = whatsapp.execute("send_template_message", {
            "to": "+1234567890", "template_name": "hello_world",
        })
        assert result.body["type"] == "template"
        assert result.body["template"]["name"] == "hello_world"
        assert result.body["template"]["language"]["code"] == "en_US"

    def test_send_media_message_body(self, whatsapp):
        result = whatsapp.execute("send_media_message", {
            "to": "+1234567890", "media_type": "image", "media_id": "media123",
        })
        assert result.body["type"] == "image"
        assert result.body["image"]["id"] == "media123"

    def test_send_media_with_caption(self, whatsapp):
        result = whatsapp.execute("send_media_message", {
            "to": "+1234567890", "media_type": "image",
            "media_id": "media123", "caption": "My photo",
        })
        assert result.body["image"]["caption"] == "My photo"

    def test_mark_read_body(self, whatsapp):
        result = whatsapp.execute("mark_read", {"message_id": "wamid.123"})
        assert result.body["status"] == "read"
        assert result.body["message_id"] == "wamid.123"

    def test_get_media_url(self, whatsapp):
        result = whatsapp.execute("get_media", {"media_id": "media123"})
        assert result.method == HTTPMethod.GET
        assert "/media123" in result.url

    def test_upload_media_url(self, whatsapp):
        result = whatsapp.execute("upload_media", {
            "file_path": "/tmp/photo.jpg", "mime_type": "image/jpeg",
        })
        assert result.method == HTTPMethod.POST
        assert "/123456789/media" in result.url

    def test_get_business_profile_url(self, whatsapp):
        result = whatsapp.execute("get_business_profile", {})
        assert result.method == HTTPMethod.GET
        assert "/123456789/whatsapp_business_profile" in result.url

    def test_all_results_have_credential_key(self, whatsapp):
        for op_id, params in [
            ("send_text_message", {"to": "+1", "text": "hi"}),
            ("mark_read", {"message_id": "x"}),
            ("get_media", {"media_id": "x"}),
            ("get_business_profile", {}),
        ]:
            result = whatsapp.execute(op_id, params)
            assert result.credential_vault_key == "whatsapp.access_token"

    def test_unknown_operation_raises(self, whatsapp):
        with pytest.raises(KeyError):
            whatsapp.execute("unknown_op", {})


# ── Credential Validation ─────────────────────────────────────────

class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, whatsapp):
        assert whatsapp.validate_credentials() is False
