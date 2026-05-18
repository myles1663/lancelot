"""
OneDrive Connector - Microsoft Graph file operations.

Produces governed Microsoft Graph request specs. Never makes network calls directly.
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote, urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod, ParameterSpec
from src.core.governance.models import RiskTier


class OneDriveConnector(ConnectorBase):
    """Microsoft OneDrive connector via Graph API."""

    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    CRED_KEY = "microsoft.graph_token"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="onedrive",
            name="Microsoft OneDrive Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Microsoft Graph API for OneDrive files and folders",
            target_domains=["graph.microsoft.com"],
            required_credentials=[
                CredentialSpec(
                    name="microsoft_graph_token",
                    type="oauth_token",
                    vault_key=self.CRED_KEY,
                    scopes=["Files.Read", "Files.ReadWrite"],
                ),
            ],
            data_reads=["OneDrive file metadata", "OneDrive file contents"],
            data_writes=["New files", "Updated file contents", "Deleted drive items"],
            does_not_access=["Email", "Calendar", "Teams messages", "SharePoint lists"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "onedrive"
        return [
            ConnectorOperation(
                id="list_root",
                connector_id=cid,
                capability="connector.read",
                name="List Drive Root",
                description="List files and folders in the signed-in user's OneDrive root",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[ParameterSpec(name="limit", type="int", required=False, default=50)],
            ),
            ConnectorOperation(
                id="list_children",
                connector_id=cid,
                capability="connector.read",
                name="List Folder Children",
                description="List children for a OneDrive folder or drive item",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="item_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="search_files",
                connector_id=cid,
                capability="connector.read",
                name="Search Files",
                description="Search OneDrive files by query string",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="query", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=25),
                ],
            ),
            ConnectorOperation(
                id="get_item",
                connector_id=cid,
                capability="connector.read",
                name="Get Drive Item",
                description="Read OneDrive drive item metadata",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[ParameterSpec(name="item_id", type="str", required=True)],
            ),
            ConnectorOperation(
                id="download_item",
                connector_id=cid,
                capability="connector.read",
                name="Download File",
                description="Read OneDrive file content",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[ParameterSpec(name="item_id", type="str", required=True)],
            ),
            ConnectorOperation(
                id="upload_small_file",
                connector_id=cid,
                capability="connector.write",
                name="Upload Small File",
                description="Create or replace a file up to the Microsoft Graph small-file limit",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=True,
                reversible=True,
                rollback_operation_id="delete_item",
                parameters=[
                    ParameterSpec(name="path", type="str", required=True),
                    ParameterSpec(name="content", type="str", required=True),
                    ParameterSpec(name="content_type", type="str", required=False, default="application/octet-stream"),
                ],
            ),
            ConnectorOperation(
                id="delete_item",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Drive Item",
                description="Delete a OneDrive file or folder",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[ParameterSpec(name="item_id", type="str", required=True)],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GRAPH_API_BASE
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "list_root":
            return self._get("list_root", f"{base}/me/drive/root/children?{urlencode({'$top': params.get('limit', 50)})}")

        if operation_id == "list_children":
            item_id = quote(params["item_id"], safe="")
            return self._get("list_children", f"{base}/me/drive/items/{item_id}/children?{urlencode({'$top': params.get('limit', 50)})}")

        if operation_id == "search_files":
            query = quote(params["query"].replace("'", "''"), safe="")
            qs = urlencode({"$top": params.get("limit", 25)})
            return self._get("search_files", f"{base}/me/drive/root/search(q='{query}')?{qs}")

        if operation_id == "get_item":
            item_id = quote(params["item_id"], safe="")
            return self._get("get_item", f"{base}/me/drive/items/{item_id}")

        if operation_id == "download_item":
            item_id = quote(params["item_id"], safe="")
            return self._get("download_item", f"{base}/me/drive/items/{item_id}/content")

        if operation_id == "upload_small_file":
            path = quote(params["path"].strip("/"), safe="/")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="onedrive",
                method=HTTPMethod.PUT,
                url=f"{base}/me/drive/root:/{path}:/content",
                headers={"Content-Type": params.get("content_type", "application/octet-stream")},
                body=params["content"],
                credential_vault_key=self.CRED_KEY,
            )

        if operation_id == "delete_item":
            item_id = quote(params["item_id"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="onedrive",
                method=HTTPMethod.DELETE,
                url=f"{base}/me/drive/items/{item_id}",
                headers={"Accept": "application/json"},
                credential_vault_key=self.CRED_KEY,
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def _get(self, operation_id: str, url: str) -> ConnectorResult:
        return ConnectorResult(
            operation_id=operation_id,
            connector_id="onedrive",
            method=HTTPMethod.GET,
            url=url,
            headers={"Accept": "application/json"},
            credential_vault_key=self.CRED_KEY,
        )

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists(self.CRED_KEY)
