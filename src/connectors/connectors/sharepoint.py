"""
SharePoint Connector - Microsoft Graph site, drive, and list operations.

Produces governed Microsoft Graph request specs. Never makes network calls directly.
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote, urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod, ParameterSpec
from src.core.governance.models import RiskTier


class SharePointConnector(ConnectorBase):
    """Microsoft SharePoint connector via Graph API."""

    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    CRED_KEY = "microsoft.graph_token"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="sharepoint",
            name="Microsoft SharePoint Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Microsoft Graph API for SharePoint sites, document libraries, and lists",
            target_domains=["graph.microsoft.com"],
            required_credentials=[
                CredentialSpec(
                    name="microsoft_graph_token",
                    type="oauth_token",
                    vault_key=self.CRED_KEY,
                    scopes=["Sites.Read.All", "Sites.ReadWrite.All"],
                ),
            ],
            data_reads=["SharePoint site metadata", "Document libraries", "List items and fields"],
            data_writes=["SharePoint list item creation and field updates", "List item deletion"],
            does_not_access=["Email", "Calendar", "Teams messages outside SharePoint-backed files"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "sharepoint"
        return [
            ConnectorOperation(
                id="search_sites",
                connector_id=cid,
                capability="connector.read",
                name="Search Sites",
                description="Search SharePoint sites by keyword",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[ParameterSpec(name="query", type="str", required=True)],
            ),
            ConnectorOperation(
                id="list_site_drives",
                connector_id=cid,
                capability="connector.read",
                name="List Site Drives",
                description="List document libraries for a SharePoint site",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[ParameterSpec(name="site_id", type="str", required=True)],
            ),
            ConnectorOperation(
                id="list_drive_items",
                connector_id=cid,
                capability="connector.read",
                name="List Drive Items",
                description="List root items in a SharePoint document library",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="drive_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="get_list_items",
                connector_id=cid,
                capability="connector.read",
                name="Get List Items",
                description="Read SharePoint list items with fields expanded",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="site_id", type="str", required=True),
                    ParameterSpec(name="list_id", type="str", required=True),
                    ParameterSpec(name="limit", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="create_list_item",
                connector_id=cid,
                capability="connector.write",
                name="Create List Item",
                description="Create a SharePoint list item",
                default_tier=RiskTier.T2_CONTROLLED,
                reversible=True,
                rollback_operation_id="delete_list_item",
                parameters=[
                    ParameterSpec(name="site_id", type="str", required=True),
                    ParameterSpec(name="list_id", type="str", required=True),
                    ParameterSpec(name="fields", type="dict", required=True),
                ],
            ),
            ConnectorOperation(
                id="update_list_item",
                connector_id=cid,
                capability="connector.write",
                name="Update List Item",
                description="Update fields on a SharePoint list item",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=True,
                reversible=True,
                parameters=[
                    ParameterSpec(name="site_id", type="str", required=True),
                    ParameterSpec(name="list_id", type="str", required=True),
                    ParameterSpec(name="item_id", type="str", required=True),
                    ParameterSpec(name="fields", type="dict", required=True),
                    ParameterSpec(name="etag", type="str", required=False),
                ],
            ),
            ConnectorOperation(
                id="delete_list_item",
                connector_id=cid,
                capability="connector.delete",
                name="Delete List Item",
                description="Delete a SharePoint list item",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="site_id", type="str", required=True),
                    ParameterSpec(name="list_id", type="str", required=True),
                    ParameterSpec(name="item_id", type="str", required=True),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GRAPH_API_BASE
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if operation_id == "search_sites":
            return self._get("search_sites", f"{base}/sites?{urlencode({'search': params['query']})}")

        if operation_id == "list_site_drives":
            site_id = quote(params["site_id"], safe="")
            return self._get("list_site_drives", f"{base}/sites/{site_id}/drives")

        if operation_id == "list_drive_items":
            drive_id = quote(params["drive_id"], safe="")
            return self._get(
                "list_drive_items",
                f"{base}/drives/{drive_id}/root/children?{urlencode({'$top': params.get('limit', 50)})}",
            )

        if operation_id == "get_list_items":
            site_id = quote(params["site_id"], safe="")
            list_id = quote(params["list_id"], safe="")
            qs = urlencode({"$expand": "fields", "$top": params.get("limit", 50)})
            return self._get("get_list_items", f"{base}/sites/{site_id}/lists/{list_id}/items?{qs}")

        if operation_id == "create_list_item":
            site_id = quote(params["site_id"], safe="")
            list_id = quote(params["list_id"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sharepoint",
                method=HTTPMethod.POST,
                url=f"{base}/sites/{site_id}/lists/{list_id}/items",
                headers=headers,
                body={"fields": params["fields"]},
                credential_vault_key=self.CRED_KEY,
            )

        if operation_id == "update_list_item":
            site_id = quote(params["site_id"], safe="")
            list_id = quote(params["list_id"], safe="")
            item_id = quote(params["item_id"], safe="")
            request_headers = dict(headers)
            if params.get("etag"):
                request_headers["if-match"] = params["etag"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sharepoint",
                method=HTTPMethod.PATCH,
                url=f"{base}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields",
                headers=request_headers,
                body=params["fields"],
                credential_vault_key=self.CRED_KEY,
            )

        if operation_id == "delete_list_item":
            site_id = quote(params["site_id"], safe="")
            list_id = quote(params["list_id"], safe="")
            item_id = quote(params["item_id"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="sharepoint",
                method=HTTPMethod.DELETE,
                url=f"{base}/sites/{site_id}/lists/{list_id}/items/{item_id}",
                headers={"Accept": "application/json"},
                credential_vault_key=self.CRED_KEY,
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def _get(self, operation_id: str, url: str) -> ConnectorResult:
        return ConnectorResult(
            operation_id=operation_id,
            connector_id="sharepoint",
            method=HTTPMethod.GET,
            url=url,
            headers={"Accept": "application/json"},
            credential_vault_key=self.CRED_KEY,
        )

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists(self.CRED_KEY)
