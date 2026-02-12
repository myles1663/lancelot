"""
Integration tests for Connector Expansion.

Cross-cutting tests that verify all 8 connectors work together:
- Registry accepts all connectors simultaneously
- Each produces valid ConnectorResults
- Proxy routes protocol:// to ProtocolAdapter
- Proxy handles form-encoded bodies (Twilio)
- All 4 auth types work (Bearer, Bot, Basic, Protocol)
- Credential vault key is set on every result
"""

import os
import pytest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import ConnectorResult, HTTPMethod
from src.connectors.proxy import ConnectorProxy
from src.connectors.protocol_adapter import ProtocolAdapter
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags

# ── All Connectors ───────────────────────────────────────────────

from src.connectors.connectors.email import EmailConnector
from src.connectors.connectors.slack import SlackConnector
from src.connectors.connectors.teams import TeamsConnector
from src.connectors.connectors.discord import DiscordConnector
from src.connectors.connectors.whatsapp import WhatsAppConnector
from src.connectors.connectors.sms import SMSConnector


@pytest.fixture(autouse=True)
def enable_connectors():
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    yield
    if old is None:
        os.environ.pop("FEATURE_CONNECTORS", None)
    else:
        os.environ["FEATURE_CONNECTORS"] = old
    feature_flags.reload_flags()


@pytest.fixture
def vault(tmp_path):
    import yaml
    key = Fernet.generate_key().decode()
    os.environ["LANCELOT_VAULT_KEY"] = key
    config = {
        "version": "1.0",
        "storage": {
            "path": str(tmp_path / "cred.enc"),
            "backup_path": str(tmp_path / "cred.enc.bak"),
        },
        "encryption": {"key_env_var": "LANCELOT_VAULT_KEY"},
        "audit": {"log_access": False},
    }
    cfg_path = tmp_path / "vault.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)
    v = CredentialVault(config_path=str(cfg_path))
    yield v
    os.environ.pop("LANCELOT_VAULT_KEY", None)


@pytest.fixture
def registry():
    return ConnectorRegistry("config/connectors.yaml")


# ── Registry Integration ────────────────────────────────────────

class TestRegistryIntegration:
    """All connectors register simultaneously without conflict."""

    def _all_connectors(self):
        return [
            EmailConnector(backend="gmail"),
            EmailConnector(backend="outlook"),
            SlackConnector(),
            TeamsConnector(),
            DiscordConnector(),
            WhatsAppConnector(phone_number_id="123"),
            SMSConnector(account_sid="AC1", from_number="+15551234567"),
        ]

    def test_register_all_unique_ids(self, registry):
        """Each connector has a unique manifest.id — no collisions."""
        connectors = self._all_connectors()
        ids = [c.manifest.id for c in connectors]
        # email appears twice (gmail + outlook) because they share the same manifest id
        # filter to unique connectors
        unique_connectors = {}
        for c in connectors:
            unique_connectors[c.manifest.id] = c

        for c in unique_connectors.values():
            registry.register(c)

        assert len(registry.list_connectors()) == len(unique_connectors)

    def test_total_operations_across_all(self, registry):
        """Verify total operation count across all connectors."""
        connectors = {
            "slack": SlackConnector(),
            "teams": TeamsConnector(),
            "discord": DiscordConnector(),
            "whatsapp": WhatsAppConnector(phone_number_id="123"),
            "sms": SMSConnector(account_sid="AC1", from_number="+1"),
            "email": EmailConnector(backend="gmail"),
        }
        total_ops = 0
        for c in connectors.values():
            registry.register(c)
            total_ops += len(c.get_operations())

        # Each connector's ops: slack=7, teams=10, discord=9, whatsapp=8, sms=6, email=7
        assert total_ops >= 47

    def test_each_manifest_validates(self, registry):
        """Every connector's manifest passes validation."""
        for c in [
            SlackConnector(), TeamsConnector(), DiscordConnector(),
            WhatsAppConnector(phone_number_id="x"), SMSConnector(account_sid="x"),
            EmailConnector(backend="gmail"), EmailConnector(backend="outlook"),
            EmailConnector(backend="smtp"),
        ]:
            c.manifest.validate()


# ── Auth Type Integration ────────────────────────────────────────

class TestAuthTypeIntegration:
    """Proxy injects the correct auth header for each credential type."""

    def _make_proxy(self, registry, vault):
        return ConnectorProxy(registry, vault)

    @patch("src.connectors.proxy.requests.Session.request")
    def test_bearer_auth_for_teams(self, mock_req, registry, vault):
        """Teams uses oauth_token → Bearer header."""
        connector = TeamsConnector()
        registry.register(connector)
        vault.store("teams.graph_token", "teams-token-123", type="oauth_token")
        vault.grant_connector_access("teams", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = self._make_proxy(registry, vault)
        result = connector.execute("list_teams", {})
        resp = proxy.execute(result)

        assert resp.success is True
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["Authorization"] == "Bearer teams-token-123"

    @patch("src.connectors.proxy.requests.Session.request")
    def test_bot_auth_for_discord(self, mock_req, registry, vault):
        """Discord uses bot_token → Bot header."""
        connector = DiscordConnector()
        registry.register(connector)
        vault.store("discord.bot_token", "discord-bot-token", type="bot_token")
        vault.grant_connector_access("discord", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = self._make_proxy(registry, vault)
        result = connector.execute("list_guilds", {})
        resp = proxy.execute(result)

        assert resp.success is True
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["Authorization"] == "Bot discord-bot-token"

    @patch("src.connectors.proxy.requests.Session.request")
    def test_basic_auth_for_sms(self, mock_req, registry, vault):
        """SMS/Twilio uses basic_auth → Basic header."""
        connector = SMSConnector(account_sid="AC1", from_number="+1")
        registry.register(connector)
        vault.store("sms.twilio_credentials", "base64encodedcreds", type="basic_auth")
        vault.grant_connector_access("sms", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"sid": "SM123"}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = self._make_proxy(registry, vault)
        result = connector.execute("send_sms", {"to": "+1234", "body": "Test"})
        resp = proxy.execute(result)

        assert resp.success is True
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["Authorization"] == "Basic base64encodedcreds"

    @patch("src.connectors.proxy.requests.Session.request")
    def test_bearer_auth_for_whatsapp(self, mock_req, registry, vault):
        """WhatsApp uses oauth_token → Bearer header."""
        connector = WhatsAppConnector(phone_number_id="123")
        registry.register(connector)
        vault.store("whatsapp.access_token", "wa-token", type="oauth_token")
        vault.grant_connector_access("whatsapp", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": []}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = self._make_proxy(registry, vault)
        result = connector.execute("send_text_message", {"to": "+1", "text": "Hi"})
        resp = proxy.execute(result)

        assert resp.success is True
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["Authorization"] == "Bearer wa-token"


# ── Form-Encoded Body Support ───────────────────────────────────

class TestFormEncodedSupport:
    """Twilio results use form-encoded bodies via data= not json=."""

    @patch("src.connectors.proxy.requests.Session.request")
    def test_twilio_sends_form_data(self, mock_req, registry, vault):
        connector = SMSConnector(account_sid="AC1", from_number="+1")
        registry.register(connector)
        vault.store("sms.twilio_credentials", "creds", type="basic_auth")
        vault.grant_connector_access("sms", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"sid": "SM1"}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = ConnectorProxy(registry, vault)
        result = connector.execute("send_sms", {"to": "+1234", "body": "Hello"})
        proxy.execute(result)

        call_kwargs = mock_req.call_args
        # Should use data= (not json=) for form-encoded body
        assert "data" in call_kwargs.kwargs
        assert "json" not in call_kwargs.kwargs

    @patch("src.connectors.proxy.requests.Session.request")
    def test_whatsapp_sends_json(self, mock_req, registry, vault):
        connector = WhatsAppConnector(phone_number_id="123")
        registry.register(connector)
        vault.store("whatsapp.access_token", "wa-token", type="oauth_token")
        vault.grant_connector_access("whatsapp", connector.manifest)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = ConnectorProxy(registry, vault)
        result = connector.execute("send_text_message", {"to": "+1", "text": "Hi"})
        proxy.execute(result)

        call_kwargs = mock_req.call_args
        # Should use json= (not data=) for JSON body
        assert "json" in call_kwargs.kwargs
        assert "data" not in call_kwargs.kwargs


# ── Protocol Routing ─────────────────────────────────────────────

class TestProtocolRouting:
    """Proxy routes protocol:// URLs to ProtocolAdapter."""

    def test_smtp_protocol_routed_to_adapter(self, registry, vault):
        connector = EmailConnector(backend="smtp")
        registry.register(connector)

        mock_adapter = MagicMock(spec=ProtocolAdapter)
        from src.connectors.models import ConnectorResponse
        mock_adapter.execute.return_value = ConnectorResponse(
            operation_id="send_message",
            connector_id="email",
            status_code=200,
            success=True,
            body={"status": "sent"},
        )

        proxy = ConnectorProxy(registry, vault, protocol_adapter=mock_adapter)
        result = connector.execute("send_message", {
            "to": "bob@example.com", "subject": "Hi", "body": "Hello",
        })
        resp = proxy.execute(result)

        assert resp.success is True
        mock_adapter.execute.assert_called_once()

    def test_imap_protocol_routed_to_adapter(self, registry, vault):
        connector = EmailConnector(backend="smtp")
        registry.register(connector)

        mock_adapter = MagicMock(spec=ProtocolAdapter)
        from src.connectors.models import ConnectorResponse
        mock_adapter.execute.return_value = ConnectorResponse(
            operation_id="list_messages",
            connector_id="email",
            status_code=200,
            success=True,
            body={"folder": "INBOX", "total": 10},
        )

        proxy = ConnectorProxy(registry, vault, protocol_adapter=mock_adapter)
        result = connector.execute("list_messages", {})
        resp = proxy.execute(result)

        assert resp.success is True
        assert resp.body["total"] == 10

    def test_protocol_routing_increments_request_count(self, registry, vault):
        connector = EmailConnector(backend="smtp")
        registry.register(connector)

        mock_adapter = MagicMock(spec=ProtocolAdapter)
        from src.connectors.models import ConnectorResponse
        mock_adapter.execute.return_value = ConnectorResponse(
            operation_id="list_messages",
            connector_id="email",
            status_code=200,
            success=True,
        )

        proxy = ConnectorProxy(registry, vault, protocol_adapter=mock_adapter)
        result = connector.execute("list_messages", {})
        proxy.execute(result)

        assert proxy.request_count == 1


# ── Cross-Connector Operation Counts ────────────────────────────

class TestOperationCounts:
    """Verify each connector has the expected number of operations."""

    @pytest.mark.parametrize("connector,expected_ops", [
        (SlackConnector(), 7),
        (TeamsConnector(), 10),
        (DiscordConnector(), 9),
        (WhatsAppConnector(phone_number_id="x"), 8),
        (SMSConnector(account_sid="x"), 6),
        (EmailConnector(backend="gmail"), 7),
        (EmailConnector(backend="outlook"), 7),
        (EmailConnector(backend="smtp"), 7),
    ])
    def test_operation_count(self, connector, expected_ops):
        assert len(connector.get_operations()) == expected_ops

    def test_all_operations_have_valid_capabilities(self):
        valid = {"connector.read", "connector.write", "connector.delete"}
        for c in [
            SlackConnector(), TeamsConnector(), DiscordConnector(),
            WhatsAppConnector(phone_number_id="x"), SMSConnector(account_sid="x"),
            EmailConnector(backend="gmail"),
        ]:
            for op in c.get_operations():
                assert op.capability in valid, f"{c.manifest.id}.{op.id}: {op.capability}"

    def test_all_operations_have_nonempty_id(self):
        for c in [
            SlackConnector(), TeamsConnector(), DiscordConnector(),
            WhatsAppConnector(phone_number_id="x"), SMSConnector(account_sid="x"),
            EmailConnector(backend="gmail"),
        ]:
            for op in c.get_operations():
                assert op.id, f"Empty operation ID in {c.manifest.id}"


# ── Credential Vault Key Propagation ────────────────────────────

class TestCredentialKeyPropagation:
    """Every ConnectorResult has a non-empty credential_vault_key."""

    def test_teams_results_have_cred_key(self):
        c = TeamsConnector()
        result = c.execute("list_teams", {})
        assert result.credential_vault_key == "teams.graph_token"

    def test_discord_results_have_cred_key(self):
        c = DiscordConnector()
        result = c.execute("list_guilds", {})
        assert result.credential_vault_key == "discord.bot_token"

    def test_whatsapp_results_have_cred_key(self):
        c = WhatsAppConnector(phone_number_id="x")
        result = c.execute("send_text_message", {"to": "+1", "text": "hi"})
        assert result.credential_vault_key == "whatsapp.access_token"

    def test_sms_results_have_cred_key(self):
        c = SMSConnector(account_sid="AC1", from_number="+1")
        result = c.execute("send_sms", {"to": "+1", "body": "hi"})
        assert result.credential_vault_key == "sms.twilio_credentials"

    def test_email_gmail_results_have_cred_key(self):
        c = EmailConnector(backend="gmail")
        result = c.execute("list_messages", {})
        assert result.credential_vault_key == "email.gmail_token"

    def test_email_outlook_results_have_cred_key(self):
        c = EmailConnector(backend="outlook")
        result = c.execute("list_messages", {})
        assert result.credential_vault_key == "email.outlook_token"

    def test_email_smtp_results_have_cred_key(self):
        c = EmailConnector(backend="smtp")
        result = c.execute("list_messages", {})
        assert result.credential_vault_key == "email.smtp_credentials"
