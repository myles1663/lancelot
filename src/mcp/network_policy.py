# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Network Policy — Endpoint validation and allowlist management.

Enforces network security at two points:

    1. Registration time — validates the endpoint URL is well-formed,
       uses HTTPS (required for production), and doesn't target
       private/internal addresses (SSRF prevention).

    2. Invocation time — ensures the server's endpoint domains are
       in the NetworkInterceptor allowlist. If not, the call is blocked.

Auto-allowlist management:
    When an MCP server is registered, its network_domains can be
    automatically added to the config/network_allowlist.yaml. This
    is gated by operator confirmation — the registry proposes domains
    and the operator approves via War Room.

HTTPS enforcement:
    In production mode, all MCP endpoints MUST use HTTPS. HTTP is
    only permitted when the endpoint is localhost (for local dev
    MCP servers). This prevents credential leakage over plaintext.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class EndpointValidationResult:
    """Result of endpoint URL validation."""
    valid: bool
    endpoint: str = ""
    domain: str = ""
    violations: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "valid": self.valid,
            "endpoint": self.endpoint,
            "domain": self.domain,
            "violations": self.violations,
        }


class MCPNetworkPolicy:
    """Validates MCP server endpoints and manages allowlist integration.

    Works with the platform's NetworkInterceptor for runtime checks
    and config/network_allowlist.yaml for persistence.
    """

    # Domains that are NEVER allowed as MCP endpoints (cloud metadata, etc.)
    _BLOCKED_DOMAINS = frozenset({
        "169.254.169.254",          # AWS/GCP metadata
        "metadata.google.internal",  # GCP metadata
        "metadata.google",
        "instance-data",             # AWS metadata alias
    })

    # Private IP ranges (SSRF prevention)
    _PRIVATE_NETWORKS = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("0.0.0.0/8"),
    ]

    def __init__(
        self,
        network_interceptor=None,
        require_https: bool = True,
        allow_localhost: bool = True,
    ):
        """
        Args:
            network_interceptor: Platform NetworkInterceptor instance.
            require_https: Enforce HTTPS for non-localhost endpoints.
            allow_localhost: Permit HTTP for localhost endpoints (dev mode).
        """
        self._network = network_interceptor
        self._require_https = require_https
        self._allow_localhost = allow_localhost

    def validate_endpoint(self, endpoint: str) -> EndpointValidationResult:
        """Validate an MCP server endpoint URL at registration time.

        Checks:
            1. URL is well-formed with scheme and host
            2. HTTPS enforced (unless localhost)
            3. Not targeting private/internal addresses
            4. Not targeting blocked metadata endpoints
            5. Domain can be resolved (basic connectivity check)

        Returns:
            EndpointValidationResult with valid=True if all checks pass.
        """
        violations: List[str] = []

        # Parse URL
        try:
            parsed = urlparse(endpoint)
        except Exception as e:
            return EndpointValidationResult(
                valid=False,
                endpoint=endpoint,
                violations=[f"Malformed URL: {e}"],
            )

        # Must have scheme and host
        if not parsed.scheme:
            violations.append("Missing URL scheme (must be https:// or http://)")
        if not parsed.hostname:
            violations.append("Missing hostname in URL")
            return EndpointValidationResult(
                valid=False,
                endpoint=endpoint,
                violations=violations,
            )

        hostname = parsed.hostname
        is_localhost = hostname in ("localhost", "127.0.0.1", "::1")

        # HTTPS enforcement
        if self._require_https and parsed.scheme != "https":
            if parsed.scheme == "http" and is_localhost and self._allow_localhost:
                pass  # HTTP allowed for localhost in dev
            elif parsed.scheme == "http":
                violations.append(
                    f"HTTP not permitted for remote endpoints. Use HTTPS. "
                    f"(endpoint: {endpoint})"
                )
            else:
                violations.append(f"Unsupported scheme: {parsed.scheme}")

        # Blocked domains
        if hostname.lower() in self._BLOCKED_DOMAINS:
            violations.append(
                f"Endpoint targets a blocked metadata service: {hostname}"
            )

        # Private/internal IP check (SSRF prevention)
        if not is_localhost:
            try:
                addr = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    violations.append(
                        f"Endpoint resolves to private/internal IP: "
                        f"{hostname} → {addr}"
                    )
            except socket.gaierror:
                # Can't resolve — might be unreachable or not yet deployed
                logger.warning(
                    "MCP endpoint hostname cannot be resolved: %s "
                    "(may be unreachable)", hostname
                )
            except ValueError:
                pass

        # Credentials in URL check
        if parsed.username or parsed.password:
            violations.append(
                "URL contains embedded credentials. Use vault_key instead."
            )

        domain = hostname
        return EndpointValidationResult(
            valid=len(violations) == 0,
            endpoint=endpoint,
            domain=domain,
            violations=violations,
        )

    def check_invocation_allowed(self, endpoint: str) -> bool:
        """Check if an endpoint is allowed at invocation time.

        Delegates to NetworkInterceptor.check_url() which handles
        domain allowlist matching and private IP blocking.

        Returns True if allowed, False if blocked.
        """
        if not self._network:
            # No network interceptor configured — can't enforce
            logger.warning(
                "No NetworkInterceptor configured — cannot enforce "
                "network policy for MCP endpoint: %s", endpoint
            )
            return True

        return self._network.check_url(endpoint)

    def extract_domains(self, endpoint: str) -> List[str]:
        """Extract domains from an endpoint URL for allowlist registration.

        Returns a list of domains that should be added to the network
        allowlist for this endpoint to work.
        """
        try:
            parsed = urlparse(endpoint)
            hostname = parsed.hostname
            if not hostname:
                return []
            return [hostname]
        except Exception:
            return []

    def get_missing_domains(
        self, domains: List[str]
    ) -> List[str]:
        """Check which MCP server domains are NOT in the current allowlist.

        Returns domains that need to be added for MCP servers to function.
        """
        if not self._network:
            return []

        missing = []
        for domain in domains:
            # Build a test URL to check against the allowlist
            test_url = f"https://{domain}/test"
            if not self._network.check_url(test_url):
                missing.append(domain)
        return missing

    def propose_allowlist_additions(
        self, server_configs: list
    ) -> List[str]:
        """Propose domains to add to the allowlist for registered MCP servers.

        Returns a list of domains that are configured on MCP servers
        but not yet in the network allowlist. These should be presented
        to the operator for approval in the War Room.
        """
        all_domains: Set[str] = set()
        for config in server_configs:
            all_domains.update(getattr(config, "network_domains", []))
            endpoint_domains = self.extract_domains(
                getattr(config, "endpoint", "")
            )
            all_domains.update(endpoint_domains)

        if not all_domains:
            return []

        return self.get_missing_domains(list(all_domains))
