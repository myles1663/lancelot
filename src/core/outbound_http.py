"""Shared outbound network guardrails for direct HTTP clients.

This module does not replace every legacy HTTP client in the repo on its own.
It provides one canonical allowlist check that transport layers can call
before opening outbound connections.
"""

from __future__ import annotations

import ipaddress
import threading
from urllib.parse import urlparse

from security import NetworkInterceptor


class OutboundNetworkError(RuntimeError):
    """Raised when an outbound request violates the network allowlist."""


class LocalControlPlaneError(RuntimeError):
    """Raised when a local control-plane URL points outside the allowed boundary."""


_INTERCEPTOR_LOCK = threading.Lock()
_INTERCEPTOR: NetworkInterceptor | None = None

_DEFAULT_LOCAL_CONTROL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _normalize_hostname(hostname: str) -> str:
    return (hostname or "").strip().lower().rstrip(".")


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def get_network_interceptor() -> NetworkInterceptor:
    global _INTERCEPTOR
    with _INTERCEPTOR_LOCK:
        if _INTERCEPTOR is None:
            _INTERCEPTOR = NetworkInterceptor()
        return _INTERCEPTOR


def assert_url_allowed(
    url: str,
    *,
    component: str = "Outbound request",
    network_interceptor: NetworkInterceptor | None = None,
) -> str:
    """Fail closed unless the URL is allowed by the canonical network policy."""
    interceptor = network_interceptor or get_network_interceptor()
    if not interceptor.check_url(url):
        raise OutboundNetworkError(f"{component} blocked by network allowlist: {url}")
    return url


def assert_local_control_url(
    url: str,
    *,
    component: str = "Local control-plane request",
    allowed_hostnames: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
    allow_single_label_hostnames: bool = False,
) -> str:
    """Fail closed unless the URL stays within the documented local control-plane boundary."""
    parsed = urlparse(url)
    hostname = _normalize_hostname(parsed.hostname or "")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise LocalControlPlaneError(f"{component} blocked by local-only URL policy: {url}")

    normalized_allowed = {
        _normalize_hostname(host)
        for host in (allowed_hostnames or _DEFAULT_LOCAL_CONTROL_HOSTS)
        if _normalize_hostname(host)
    }
    if hostname in normalized_allowed:
        return url

    if allow_single_label_hostnames and "." not in hostname and not _is_ip_literal(hostname):
        return url

    raise LocalControlPlaneError(f"{component} blocked by local-only URL policy: {url}")
