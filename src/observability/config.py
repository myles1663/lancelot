# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Observability Configuration — runtime settings for OTel, Webhooks, Metrics API.

Configuration is loaded from environment variables and persisted War Room
settings. All credentials are stored in the Credential Vault.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lancelot.observability.config")

_CONFIG_FILE = "/home/lancelot/data/observability_config.json"


@dataclass
class OTelConfig:
    """OpenTelemetry exporter configuration."""
    enabled: bool = False
    endpoint: str = ""  # OTLP/HTTP endpoint URL
    auth_header: str = ""  # e.g., "Authorization: Bearer <token>" — credential vault key
    export_interval_s: int = 5  # Batch flush interval (1-60)
    sampling_rate_t0_t1: float = 0.1  # 10% default for T0/T1 spans
    # T2/T3 always exported at 100% — not configurable
    resource_attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class WebhookEndpoint:
    """A registered webhook endpoint."""
    id: str = ""
    url: str = ""  # HTTPS only
    categories: List[str] = field(default_factory=list)
    secret_vault_key: str = ""  # Credential Vault key for HMAC secret
    cost_thresholds: List[float] = field(default_factory=lambda: [0.75, 0.90, 1.0])
    enabled: bool = True


@dataclass
class WebhookConfig:
    """Webhook subsystem configuration."""
    enabled: bool = False
    endpoints: List[WebhookEndpoint] = field(default_factory=list)
    delivery_timeout_s: int = 10
    max_retries: int = 6


@dataclass
class MetricsApiConfig:
    """War Room Metrics API configuration."""
    enabled: bool = False
    rate_limit_per_minute: int = 60
    receipt_queries: bool = False  # Generate METRICS_API_QUERY receipts for detail lookups


@dataclass
class ObservabilityConfig:
    """Top-level observability configuration."""
    otel: OTelConfig = field(default_factory=OTelConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    metrics_api: MetricsApiConfig = field(default_factory=MetricsApiConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "otel": {
                "enabled": self.otel.enabled,
                "endpoint": self.otel.endpoint,
                "auth_header": self.otel.auth_header,
                "export_interval_s": self.otel.export_interval_s,
                "sampling_rate_t0_t1": self.otel.sampling_rate_t0_t1,
                "resource_attributes": self.otel.resource_attributes,
            },
            "webhooks": {
                "enabled": self.webhooks.enabled,
                "endpoints": [
                    {
                        "id": ep.id,
                        "url": ep.url,
                        "categories": ep.categories,
                        "secret_vault_key": ep.secret_vault_key,
                        "cost_thresholds": ep.cost_thresholds,
                        "enabled": ep.enabled,
                    }
                    for ep in self.webhooks.endpoints
                ],
                "delivery_timeout_s": self.webhooks.delivery_timeout_s,
                "max_retries": self.webhooks.max_retries,
            },
            "metrics_api": {
                "enabled": self.metrics_api.enabled,
                "rate_limit_per_minute": self.metrics_api.rate_limit_per_minute,
                "receipt_queries": self.metrics_api.receipt_queries,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ObservabilityConfig:
        """Deserialize from dict."""
        otel_data = data.get("otel", {})
        wh_data = data.get("webhooks", {})
        ma_data = data.get("metrics_api", {})

        endpoints = [
            WebhookEndpoint(**ep) for ep in wh_data.get("endpoints", [])
        ]

        return cls(
            otel=OTelConfig(
                enabled=otel_data.get("enabled", False),
                endpoint=otel_data.get("endpoint", ""),
                auth_header=otel_data.get("auth_header", ""),
                export_interval_s=otel_data.get("export_interval_s", 5),
                sampling_rate_t0_t1=otel_data.get("sampling_rate_t0_t1", 0.1),
                resource_attributes=otel_data.get("resource_attributes", {}),
            ),
            webhooks=WebhookConfig(
                enabled=wh_data.get("enabled", False),
                endpoints=endpoints,
                delivery_timeout_s=wh_data.get("delivery_timeout_s", 10),
                max_retries=wh_data.get("max_retries", 6),
            ),
            metrics_api=MetricsApiConfig(
                enabled=ma_data.get("enabled", False),
                rate_limit_per_minute=ma_data.get("rate_limit_per_minute", 60),
                receipt_queries=ma_data.get("receipt_queries", False),
            ),
        )


def load_config(config_path: str = _CONFIG_FILE) -> ObservabilityConfig:
    """Load observability config from disk. Returns defaults if not found."""
    try:
        path = Path(config_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return ObservabilityConfig.from_dict(data)
    except Exception as exc:
        logger.warning("Failed to load observability config: %s", exc)
    return ObservabilityConfig()


def save_config(config: ObservabilityConfig, config_path: str = _CONFIG_FILE) -> None:
    """Persist observability config to disk."""
    try:
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(config.to_dict(), indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("Failed to save observability config: %s", exc)
