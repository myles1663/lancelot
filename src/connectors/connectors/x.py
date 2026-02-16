"""
X (Twitter) Connector — X API v2 integration.

Produces HTTP request specs for X API v2 operations.
Never makes network calls directly.

Auth: OAuth 1.0a — requires API Key, API Key Secret, Access Token,
and Access Token Secret. The ConnectorProxy handles OAuth signature
generation using these four credentials.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class XConnector(ConnectorBase):
    """X (Twitter) API v2 connector with governed tweet operations."""

    X_API_BASE = "https://api.x.com/2"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="x",
            name="X (Twitter) Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="X API v2 for posting tweets, deleting tweets, and reading account info",
            target_domains=["api.x.com"],
            required_credentials=[
                CredentialSpec(
                    name="x_api_key",
                    type="api_key",
                    vault_key="x.api_key",
                ),
                CredentialSpec(
                    name="x_api_key_secret",
                    type="api_key",
                    vault_key="x.api_key_secret",
                ),
                CredentialSpec(
                    name="x_access_token",
                    type="api_key",
                    vault_key="x.access_token",
                ),
                CredentialSpec(
                    name="x_access_token_secret",
                    type="api_key",
                    vault_key="x.access_token_secret",
                ),
            ],
            data_reads=["Account info (username, display name, ID)"],
            data_writes=["Post tweets", "Delete tweets"],
            does_not_access=[
                "Direct messages",
                "Follower lists",
                "Likes and bookmarks",
                "User search",
                "Spaces",
            ],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "x"
        return [
            # Read operations
            ConnectorOperation(
                id="get_me",
                connector_id=cid,
                capability="connector.read",
                name="Get Account Info",
                description="Get authenticated user's account information",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(
                        name="user_fields",
                        type="str",
                        required=False,
                        default="id,name,username,description,profile_image_url",
                        description="Comma-separated list of user fields to return",
                    ),
                ],
            ),
            # Write operations
            ConnectorOperation(
                id="post_tweet",
                connector_id=cid,
                capability="connector.write",
                name="Post Tweet",
                description="Post a new tweet (max 280 characters)",
                default_tier=RiskTier.T1_REVERSIBLE,
                idempotent=False,
                reversible=True,
                rollback_operation_id="delete_tweet",
                parameters=[
                    ParameterSpec(
                        name="text",
                        type="str",
                        required=True,
                        description="Tweet text (max 280 characters)",
                    ),
                    ParameterSpec(
                        name="reply_to",
                        type="str",
                        required=False,
                        default="",
                        description="Tweet ID to reply to",
                    ),
                    ParameterSpec(
                        name="quote_tweet_id",
                        type="str",
                        required=False,
                        default="",
                        description="Tweet ID to quote",
                    ),
                ],
            ),
            # Delete operations
            ConnectorOperation(
                id="delete_tweet",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Tweet",
                description="Delete a tweet by ID (irreversible)",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(
                        name="tweet_id",
                        type="str",
                        required=True,
                        description="ID of the tweet to delete",
                    ),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        cid = "x"
        cred_key = "x.api_key"

        if operation_id == "get_me":
            user_fields = params.get(
                "user_fields",
                "id,name,username,description,profile_image_url",
            )
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.GET,
                url=f"{self.X_API_BASE}/users/me?user.fields={user_fields}",
                headers={"Content-Type": "application/json"},
                credential_vault_key=cred_key,
                metadata={
                    "auth_type": "oauth1",
                    "oauth_consumer_key": "x.api_key",
                    "oauth_consumer_secret": "x.api_key_secret",
                    "oauth_token_key": "x.access_token",
                    "oauth_token_secret": "x.access_token_secret",
                },
            )

        if operation_id == "post_tweet":
            text = params["text"]
            body: Dict[str, Any] = {"text": text}

            if params.get("reply_to"):
                body["reply"] = {"in_reply_to_tweet_id": params["reply_to"]}
            if params.get("quote_tweet_id"):
                body["quote_tweet_id"] = params["quote_tweet_id"]

            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.POST,
                url=f"{self.X_API_BASE}/tweets",
                headers={"Content-Type": "application/json"},
                body=body,
                credential_vault_key=cred_key,
                metadata={
                    "auth_type": "oauth1",
                    "oauth_consumer_key": "x.api_key",
                    "oauth_consumer_secret": "x.api_key_secret",
                    "oauth_token_key": "x.access_token",
                    "oauth_token_secret": "x.access_token_secret",
                },
            )

        if operation_id == "delete_tweet":
            tweet_id = params["tweet_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id=cid,
                method=HTTPMethod.DELETE,
                url=f"{self.X_API_BASE}/tweets/{tweet_id}",
                headers={"Content-Type": "application/json"},
                credential_vault_key=cred_key,
                metadata={
                    "auth_type": "oauth1",
                    "oauth_consumer_key": "x.api_key",
                    "oauth_consumer_secret": "x.api_key_secret",
                    "oauth_token_key": "x.access_token",
                    "oauth_token_secret": "x.access_token_secret",
                },
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return (
            self._vault.exists("x.api_key")
            and self._vault.exists("x.api_key_secret")
            and self._vault.exists("x.access_token")
            and self._vault.exists("x.access_token_secret")
        )
