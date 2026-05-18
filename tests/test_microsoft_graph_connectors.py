from __future__ import annotations

import pytest

from src.connectors.connectors.onedrive import OneDriveConnector
from src.connectors.connectors.sharepoint import SharePointConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


class _Vault:
    def __init__(self, keys: set[str]) -> None:
        self._keys = keys

    def exists(self, key: str) -> bool:
        return key in self._keys


def test_onedrive_manifest_declares_graph_scope_and_boundaries() -> None:
    connector = OneDriveConnector()

    assert connector.manifest.id == "onedrive"
    assert connector.manifest.target_domains == ["graph.microsoft.com"]
    assert connector.manifest.required_credentials[0].vault_key == "microsoft.graph_token"
    assert "Files.ReadWrite" in connector.manifest.required_credentials[0].scopes
    assert "Email" in connector.manifest.does_not_access


def test_onedrive_operation_tiers() -> None:
    operations = {operation.id: operation for operation in OneDriveConnector().get_operations()}

    assert operations["list_root"].default_tier == RiskTier.T0_INERT
    assert operations["upload_small_file"].default_tier == RiskTier.T2_CONTROLLED
    assert operations["upload_small_file"].rollback_operation_id == "delete_item"
    assert operations["delete_item"].default_tier == RiskTier.T3_IRREVERSIBLE


def test_onedrive_request_specs() -> None:
    connector = OneDriveConnector()
    listed = connector.execute("list_root", {"limit": 12})
    uploaded = connector.execute("upload_small_file", {"path": "Reports/a.txt", "content": "hello"})
    deleted = connector.execute("delete_item", {"item_id": "item1"})

    assert listed.method == HTTPMethod.GET
    assert listed.url == "https://graph.microsoft.com/v1.0/me/drive/root/children?%24top=12"
    assert uploaded.method == HTTPMethod.PUT
    assert uploaded.url.endswith("/me/drive/root:/Reports/a.txt:/content")
    assert uploaded.credential_vault_key == "microsoft.graph_token"
    assert deleted.method == HTTPMethod.DELETE


def test_onedrive_rejects_unknown_operation() -> None:
    with pytest.raises(KeyError, match="Unknown operation"):
        OneDriveConnector().execute("missing", {})


def test_sharepoint_manifest_declares_graph_scope_and_boundaries() -> None:
    connector = SharePointConnector()

    assert connector.manifest.id == "sharepoint"
    assert connector.manifest.target_domains == ["graph.microsoft.com"]
    assert connector.manifest.required_credentials[0].vault_key == "microsoft.graph_token"
    assert "Sites.ReadWrite.All" in connector.manifest.required_credentials[0].scopes
    assert "Calendar" in connector.manifest.does_not_access


def test_sharepoint_request_specs() -> None:
    connector = SharePointConnector()
    search = connector.execute("search_sites", {"query": "Finance"})
    items = connector.execute("get_list_items", {"site_id": "site1", "list_id": "list1", "limit": 20})
    created = connector.execute("create_list_item", {
        "site_id": "site1",
        "list_id": "list1",
        "fields": {"Title": "Review"},
    })
    updated = connector.execute("update_list_item", {
        "site_id": "site1",
        "list_id": "list1",
        "item_id": "item1",
        "fields": {"Status": "Done"},
        "etag": '"123"',
    })

    assert search.method == HTTPMethod.GET
    assert search.url == "https://graph.microsoft.com/v1.0/sites?search=Finance"
    assert items.url.endswith("/sites/site1/lists/list1/items?%24expand=fields&%24top=20")
    assert created.method == HTTPMethod.POST
    assert created.body == {"fields": {"Title": "Review"}}
    assert updated.method == HTTPMethod.PATCH
    assert updated.headers["if-match"] == '"123"'


def test_sharepoint_delete_and_unknown_operation() -> None:
    connector = SharePointConnector()
    deleted = connector.execute("delete_list_item", {
        "site_id": "site1",
        "list_id": "list1",
        "item_id": "item1",
    })

    assert deleted.method == HTTPMethod.DELETE
    assert deleted.url.endswith("/sites/site1/lists/list1/items/item1")
    with pytest.raises(KeyError, match="Unknown operation"):
        connector.execute("missing", {})


def test_graph_connectors_validate_shared_token() -> None:
    assert OneDriveConnector(_Vault({"microsoft.graph_token"})).validate_credentials() is True
    assert SharePointConnector(_Vault({"microsoft.graph_token"})).validate_credentials() is True
    assert OneDriveConnector(_Vault(set())).validate_credentials() is False
    assert SharePointConnector().validate_credentials() is False
