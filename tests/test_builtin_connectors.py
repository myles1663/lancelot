from __future__ import annotations

import pytest

from src.connectors.connectors.shared_workspace import SharedWorkspaceConnector
from src.connectors.connectors.x import XConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


class _Vault:
    def __init__(self, keys: set[str]) -> None:
        self._keys = keys

    def exists(self, key: str) -> bool:
        return key in self._keys


def test_x_connector_declares_governed_operations() -> None:
    connector = XConnector()

    operations = {operation.id: operation for operation in connector.get_operations()}

    assert set(operations) == {"get_me", "post_tweet", "delete_tweet"}
    assert operations["get_me"].default_tier == RiskTier.T0_INERT
    assert operations["post_tweet"].default_tier == RiskTier.T1_REVERSIBLE
    assert operations["post_tweet"].rollback_operation_id == "delete_tweet"
    assert operations["delete_tweet"].default_tier == RiskTier.T3_IRREVERSIBLE


def test_x_connector_builds_account_request() -> None:
    result = XConnector().execute("get_me", {"user_fields": "id,username"})

    assert result.method == HTTPMethod.GET
    assert result.url == "https://api.x.com/2/users/me?user.fields=id,username"
    assert result.credential_vault_key == "x.api_key"
    assert result.metadata["auth_type"] == "oauth1"


def test_x_connector_builds_post_request_with_reply_and_quote() -> None:
    result = XConnector().execute(
        "post_tweet",
        {
            "text": "status update",
            "reply_to": "123",
            "quote_tweet_id": "456",
        },
    )

    assert result.method == HTTPMethod.POST
    assert result.url == "https://api.x.com/2/tweets"
    assert result.body == {
        "text": "status update",
        "reply": {"in_reply_to_tweet_id": "123"},
        "quote_tweet_id": "456",
    }


def test_x_connector_builds_delete_request() -> None:
    result = XConnector().execute("delete_tweet", {"tweet_id": "789"})

    assert result.method == HTTPMethod.DELETE
    assert result.url == "https://api.x.com/2/tweets/789"
    assert result.credential_vault_key == "x.api_key"


def test_x_connector_rejects_unknown_operation() -> None:
    with pytest.raises(KeyError, match="Unknown operation"):
        XConnector().execute("unknown", {})


def test_x_connector_validates_all_required_credentials() -> None:
    keys = {
        "x.api_key",
        "x.api_key_secret",
        "x.access_token",
        "x.access_token_secret",
    }

    assert XConnector(_Vault(keys)).validate_credentials() is True
    assert XConnector(_Vault(keys - {"x.access_token_secret"})).validate_credentials() is False
    assert XConnector().validate_credentials() is False


def test_shared_workspace_connector_is_config_only() -> None:
    connector = SharedWorkspaceConnector()

    assert connector.manifest.id == "shared_workspace"
    assert connector.get_operations() == []
    with pytest.raises(NotImplementedError, match="no executable operations"):
        connector.execute("anything", {})


def test_shared_workspace_connector_validates_workspace_mount(monkeypatch) -> None:
    class _FakePath:
        def __init__(self, path: str) -> None:
            self.path = path

        def exists(self) -> bool:
            return self.path == "/home/lancelot/workspace"

        def is_dir(self) -> bool:
            return True

    monkeypatch.setattr("src.connectors.connectors.shared_workspace.Path", _FakePath)

    assert SharedWorkspaceConnector().validate_credentials() is True
