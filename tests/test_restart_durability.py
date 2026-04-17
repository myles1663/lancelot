import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "integrations"))

from src.core.google_oauth_manager import GoogleOAuthManager
from src.core.oauth_token_manager import OAuthTokenManager
from src.core.openai_codex_oauth_manager import OpenAICodexOAuthManager
from src.integrations.ucp_connector import UCPConnector
from src.observability.config import WebhookEndpoint
from src.observability.webhook_engine import WebhookEngine


class DummyVault:
    def __init__(self):
        self._entries = {}

    def store(self, key, value, type="config"):
        self._entries[key] = value

    def retrieve(self, key, accessor_id=""):
        return self._entries.get(key, "")

    def exists(self, key):
        return key in self._entries

    def delete(self, key):
        self._entries.pop(key, None)


def _make_endpoint():
    return WebhookEndpoint(
        id="ep-1",
        url="https://example.com/hook",
        categories=["ALL"],
        secret_vault_key="",
        enabled=True,
    )


def test_webhook_pending_deliveries_survive_restart(tmp_path):
    pending_file = tmp_path / "webhook_pending_deliveries.json"

    with patch("src.observability.webhook_engine.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.post.return_value = mock_response

        engine = WebhookEngine([_make_endpoint()], data_dir=str(tmp_path))
        engine.on_receipt(
            {
                "id": "rcpt-1",
                "action_type": "kill_switch_issued",
                "action_name": "kill_switch",
                "status": "success",
                "tier": 3,
                "quest_id": "quest-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "operator_id": "owner:Myles",
                "inputs": {},
                "outputs": {},
            }
        )
        assert len(engine._pending) == 1
        assert pending_file.exists()

        engine2 = WebhookEngine([_make_endpoint()], data_dir=str(tmp_path))
        assert len(engine2._pending) == 1
        assert engine2.get_stats()["ep-1"]["pending_retries"] == 1

        engine.stop()
        engine2.stop()


def test_google_oauth_pending_flow_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    manager = GoogleOAuthManager(vault=DummyVault(), port=8000)
    manager.generate_auth_url("client-id", "client-secret")
    assert len(manager._pending_flows) == 1

    manager2 = GoogleOAuthManager(vault=DummyVault(), port=8000)
    assert manager2._pending_flows.keys() == manager._pending_flows.keys()


def test_anthropic_oauth_pending_flow_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(tmp_path / "anthropic-oauth.json"))
    manager = OAuthTokenManager(vault=DummyVault(), port=8000)
    _, state = manager.generate_auth_url()
    assert state in manager._pending_flows

    manager2 = OAuthTokenManager(vault=DummyVault(), port=8000)
    assert state in manager2._pending_flows


def test_codex_oauth_pending_flow_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    manager = OpenAICodexOAuthManager(vault=DummyVault(), port=1455)
    _, state = manager.generate_auth_url()
    assert state in manager._pending_flows

    manager2 = OpenAICodexOAuthManager(vault=DummyVault(), port=1455)
    assert state in manager2._pending_flows


def test_ucp_pending_transaction_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(tmp_path / "ucp-pending.json"))
    connector = UCPConnector()
    connector._registered_merchants["https://shop.example.com"] = {
        "name": "Shop",
        "endpoints": {"transact": "/buy"},
    }
    txn = connector.initiate_transaction(
        "https://shop.example.com",
        "prod-1",
        {"quantity": 1},
    )
    assert txn["transaction_id"] in connector._pending_transactions

    connector2 = UCPConnector()
    restored = connector2.get_transaction(txn["transaction_id"])
    assert restored is not None
    assert restored["status"] == "pending_confirmation"
