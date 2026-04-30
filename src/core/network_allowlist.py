"""
Canonical network allowlist subsystem.

This module owns:
- config path resolution
- built-in infrastructure domains
- YAML load/save helpers
- hostname normalization and suffix matching
"""

from __future__ import annotations

import os
from typing import Any

import yaml

NETWORK_ALLOWLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "network_allowlist.yaml",
)

CORE_NETWORK_ALLOWLIST_DOMAINS = (
    "localhost",
    "127.0.0.1",
    "api.projectlancelot.dev",
    "ghcr.io",
)


class NetworkAllowlistService:
    """Canonical loader and evaluator for outbound domain allowlist policy."""

    def __init__(
        self,
        path: str = NETWORK_ALLOWLIST_PATH,
        core_domains: tuple[str, ...] = CORE_NETWORK_ALLOWLIST_DOMAINS,
    ):
        self._path = path
        self._core_domains = tuple(self._normalize_domain(d) for d in core_domains)

    @property
    def path(self) -> str:
        return self._path

    @property
    def core_domains(self) -> list[str]:
        return list(self._core_domains)

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        return (domain or "").strip().lower().rstrip(".")

    def normalize_domains(self, domains: list[str]) -> list[str]:
        normalized = {
            self._normalize_domain(domain)
            for domain in domains
            if self._normalize_domain(domain)
        }
        return sorted(normalized)

    def load_config(self) -> dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except FileNotFoundError:
            return {
                "domains": [],
                "notes": "No allowlist config found. Create config/network_allowlist.yaml.",
            }

    def save_config(self, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as handle:
            yaml.dump(data, handle, default_flow_style=False, sort_keys=False)

    def load_domains(self, include_core: bool = True) -> list[str]:
        data = self.load_config()
        configured = self.normalize_domains(data.get("domains", []))
        if not include_core:
            return configured
        merged = set(self._core_domains)
        merged.update(configured)
        return sorted(merged)

    def set_domains(self, domains: list[str]) -> list[str]:
        data = self.load_config()
        data["domains"] = self.normalize_domains(domains)
        self.save_config(data)
        return data["domains"]

    def domain_matches(self, hostname: str, allowed_domain: str) -> bool:
        normalized_host = self._normalize_domain(hostname)
        normalized_allowed = self._normalize_domain(allowed_domain)
        if not normalized_host or not normalized_allowed:
            return False
        return (
            normalized_host == normalized_allowed
            or normalized_host.endswith("." + normalized_allowed)
        )

    def is_hostname_allowed(
        self,
        hostname: str,
        *,
        include_core: bool = True,
        domains: list[str] | None = None,
    ) -> bool:
        normalized_host = self._normalize_domain(hostname)
        if not normalized_host:
            return False
        allowed_domains = (
            self.load_domains(include_core=include_core)
            if domains is None
            else self.normalize_domains(domains)
        )
        return any(
            self.domain_matches(normalized_host, allowed_domain)
            for allowed_domain in allowed_domains
        )
