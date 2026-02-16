"""
Connector Proxy — HTTP execution layer for connectors.

ConnectorProxy is the ONLY component that makes outbound HTTP requests
for connectors. It validates domains against manifests, injects credentials
from the vault, enforces rate limits, and uses a pooled requests.Session.

Supports credential injection modes (via ``metadata.auth_type``):
- ``bearer`` / ``oauth_token`` → ``Authorization: Bearer {token}``
- ``api_key`` → ``X-API-Key: {value}``
- ``basic_auth`` → ``Authorization: Basic {base64}``  (single vault key)
- ``basic_auth_composed`` → composes Basic auth from two vault keys
- ``bot_token`` → ``Authorization: Bot {token}``  (Discord)
- ``url_token`` → substitutes ``{token}`` in the URL  (Telegram)
- ``oauth1`` → OAuth 1.0a signature  (X/Twitter)

Supports three body encodings:
- JSON (default) → ``json={body}``
- Form-encoded → ``data={body}`` when Content-Type is
  ``application/x-www-form-urlencoded``
- Protocol → routed to ProtocolAdapter for SMTP/IMAP

All execution is SYNCHRONOUS (requests library, not aiohttp).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from src.connectors.models import ConnectorResponse, ConnectorResult, HTTPMethod
from src.connectors.protocol_adapter import ProtocolAdapter
from src.connectors.rate_limiter import RateLimiterRegistry
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault

logger = logging.getLogger(__name__)


# ── OAuth 1.0a Signing ──────────────────────────────────────────

def _percent_encode(s: str) -> str:
    """RFC 5849 percent-encode a string."""
    return urllib.parse.quote(str(s), safe="")


def _build_oauth1_header(
    method: str,
    url: str,
    body: Any,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
) -> str:
    """Generate an OAuth 1.0a Authorization header value.

    Implements RFC 5849 HMAC-SHA1 signature for X (Twitter) API v2.
    """
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)

    # OAuth parameters
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp,
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    # Collect all parameters for signature base string
    # (OAuth params + query string params; POST body only if form-encoded)
    all_params = dict(oauth_params)

    # Parse query string params from URL
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        for k, v in urllib.parse.parse_qsl(parsed.query):
            all_params[k] = v

    # Sort and encode parameters
    sorted_params = sorted(all_params.items())
    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params
    )

    # Build signature base string: METHOD&URL&PARAMS
    base_string = (
        f"{method.upper()}&{_percent_encode(base_url)}&{_percent_encode(param_string)}"
    )

    # Build signing key: consumer_secret&token_secret
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"

    # HMAC-SHA1
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    # Build Authorization header
    oauth_params["oauth_signature"] = signature
    auth_header = "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return auth_header


# ── Domain Validator ──────────────────────────────────────────────

class DomainValidator:
    """Validates URLs against connector manifest domain declarations."""

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract the hostname from a URL."""
        parsed = urlparse(url)
        return parsed.hostname or ""

    @staticmethod
    def is_domain_allowed(url: str, allowed_domains: list) -> bool:
        """Check if URL domain exactly matches one of the allowed domains.

        Exact match only — no wildcards, no subdomain matching.
        """
        domain = DomainValidator.extract_domain(url)
        return domain in allowed_domains


# ── Connector Proxy ───────────────────────────────────────────────

class ConnectorProxy:
    """Synchronous HTTP proxy for connector operations.

    All outbound HTTP traffic from connectors flows through this proxy.
    It enforces domain allowlists, rate limits, and credential injection.
    """

    def __init__(
        self,
        registry: ConnectorRegistry,
        vault: CredentialVault,
        rate_limiter_registry: Optional[RateLimiterRegistry] = None,
        protocol_adapter: Optional[ProtocolAdapter] = None,
    ) -> None:
        self._registry = registry
        self._vault = vault
        self._rate_limiter = rate_limiter_registry
        self._protocol_adapter = protocol_adapter or ProtocolAdapter()
        self._session = requests.Session()
        self._request_count = 0

    def execute(self, result: ConnectorResult) -> ConnectorResponse:
        """Execute an HTTP request spec produced by a connector.

        Steps:
        1. Look up connector and manifest from registry
        2. Check rate limit
        3. Validate domain against manifest
        4. Inject credentials from vault
        5. Make HTTP request
        6. Return ConnectorResponse
        """
        connector_id = result.connector_id

        # 1. Get connector entry
        entry = self._registry.get(connector_id)
        if entry is None:
            return ConnectorResponse(
                operation_id=result.operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error=f"Connector '{connector_id}' not found in registry",
            )
        manifest = entry.manifest

        # 2. Rate limit check
        if self._rate_limiter and not self._rate_limiter.check(connector_id):
            return ConnectorResponse(
                operation_id=result.operation_id,
                connector_id=connector_id,
                status_code=429,
                success=False,
                error="Rate limited",
            )

        # 3. Protocol routing — SMTP/IMAP bypass HTTP entirely
        if result.url.startswith("protocol://"):
            self._request_count += 1
            return self._protocol_adapter.execute(result)

        # 4. Domain validation (check before URL template substitution)
        url = result.url
        # For URL-token auth, validate domain before substituting the token
        check_url = url.replace("{token}", "PLACEHOLDER") if "{token}" in url else url
        if not DomainValidator.is_domain_allowed(check_url, manifest.target_domains):
            domain = DomainValidator.extract_domain(check_url)
            return ConnectorResponse(
                operation_id=result.operation_id,
                connector_id=connector_id,
                status_code=0,
                success=False,
                error=(
                    f"Domain '{domain}' not in allowed domains "
                    f"{manifest.target_domains} for connector '{connector_id}'"
                ),
            )

        # 5. Credential injection
        headers = dict(result.headers)
        auth_type = result.metadata.get("auth_type", "")

        if result.credential_vault_key:
            try:
                if auth_type == "url_token":
                    # Telegram-style: substitute {token} in URL path
                    cred_value = self._vault.retrieve(
                        result.credential_vault_key, accessor_id=connector_id,
                    )
                    url = url.replace("{token}", cred_value)

                elif auth_type == "oauth1":
                    # OAuth 1.0a signing (X/Twitter) — 4 vault keys
                    consumer_key = self._vault.retrieve(
                        result.metadata["oauth_consumer_key"], accessor_id=connector_id,
                    )
                    consumer_secret = self._vault.retrieve(
                        result.metadata["oauth_consumer_secret"], accessor_id=connector_id,
                    )
                    oauth_token = self._vault.retrieve(
                        result.metadata["oauth_token_key"], accessor_id=connector_id,
                    )
                    oauth_token_secret = self._vault.retrieve(
                        result.metadata["oauth_token_secret"], accessor_id=connector_id,
                    )
                    headers["Authorization"] = _build_oauth1_header(
                        method=result.method.value,
                        url=url,
                        body=result.body,
                        consumer_key=consumer_key,
                        consumer_secret=consumer_secret,
                        token=oauth_token,
                        token_secret=oauth_token_secret,
                    )

                elif auth_type == "basic_auth_composed":
                    # Composed Basic auth from two vault keys (e.g. Twilio)
                    username_key = result.metadata.get("basic_auth_username_key", "")
                    username = self._vault.retrieve(
                        username_key, accessor_id=connector_id,
                    )
                    password = self._vault.retrieve(
                        result.credential_vault_key, accessor_id=connector_id,
                    )
                    encoded = base64.b64encode(
                        f"{username}:{password}".encode()
                    ).decode()
                    headers["Authorization"] = f"Basic {encoded}"

                else:
                    # Default: determine injection from vault entry type
                    cred_value = self._vault.retrieve(
                        result.credential_vault_key, accessor_id=connector_id,
                    )
                    vault_entry = self._vault._entries.get(result.credential_vault_key)
                    cred_type = vault_entry.type if vault_entry else "api_key"

                    if cred_type in ("oauth_token", "bearer"):
                        headers["Authorization"] = f"Bearer {cred_value}"
                    elif cred_type == "api_key":
                        headers["X-API-Key"] = cred_value
                    elif cred_type == "basic_auth":
                        headers["Authorization"] = f"Basic {cred_value}"
                    elif cred_type == "bot_token":
                        headers["Authorization"] = f"Bot {cred_value}"
                    else:
                        headers["Authorization"] = f"Bearer {cred_value}"

            except (KeyError, PermissionError) as e:
                return ConnectorResponse(
                    operation_id=result.operation_id,
                    connector_id=connector_id,
                    status_code=0,
                    success=False,
                    error=f"Credential error: {e}",
                )

        # 6. Make HTTP request
        status_code, resp_headers, resp_body, elapsed_ms = self._make_request(
            method=result.method.value,
            url=url,
            headers=headers,
            body=result.body,
            timeout=result.timeout_seconds,
        )
        self._request_count += 1

        # 7. Build response
        success = status_code > 0 and status_code < 400
        return ConnectorResponse(
            operation_id=result.operation_id,
            connector_id=connector_id,
            status_code=status_code,
            headers=resp_headers,
            body=resp_body,
            elapsed_ms=elapsed_ms,
            success=success,
            error="" if success else f"HTTP {status_code}",
        )

    def _make_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Any,
        timeout: int,
    ) -> Tuple[int, Dict[str, str], Any, float]:
        """Execute HTTP request via requests.Session.

        Returns (status_code, headers, body, elapsed_ms).
        On error: (0, {}, None, elapsed_ms).
        """
        start = time.time()
        try:
            kwargs: Dict[str, Any] = {
                "method": method,
                "url": url,
                "headers": headers,
                "timeout": timeout,
            }
            if body is not None:
                # Form-encoded bodies (e.g. Twilio) use data=, not json=
                if headers.get("Content-Type") == "application/x-www-form-urlencoded":
                    kwargs["data"] = body
                else:
                    kwargs["json"] = body

            resp = self._session.request(**kwargs)
            elapsed_ms = (time.time() - start) * 1000

            # Try to parse JSON body
            try:
                resp_body = resp.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                resp_body = resp.text

            return (
                resp.status_code,
                dict(resp.headers),
                resp_body,
                elapsed_ms,
            )
        except requests.RequestException as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.warning("ConnectorProxy request failed: %s", e)
            return (0, {}, None, elapsed_ms)

    def close(self) -> None:
        """Close the underlying requests.Session."""
        self._session.close()

    @property
    def request_count(self) -> int:
        """Total number of HTTP requests made."""
        return self._request_count
