# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Config — Pydantic configuration model + YAML loader.

Follows the HIVE config pattern: Pydantic model with YAML file loading.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DeploymentMode(str, Enum):
    """Federation deployment mode — derived from topology shape."""
    STANDALONE = "standalone"
    HIERARCHICAL = "hierarchical"
    FEDERATED = "federated"


class FederationConfig(BaseModel):
    """Configuration for the Federation subsystem."""

    # Heartbeat
    heartbeat_interval_s: float = Field(default=2.0, ge=0.5, le=30.0)
    staleness_warning_s: float = Field(default=10.0, ge=2.0)
    staleness_critical_s: float = Field(default=20.0, ge=5.0)
    staleness_lost_s: float = Field(default=30.0, ge=10.0)

    # Networking
    tls_required: bool = Field(default=False)
    tls_cert_path: Optional[str] = Field(default=None)
    tls_key_path: Optional[str] = Field(default=None)

    # Topology
    topology_path: str = Field(default="config/federation_topology.json")

    # Limits
    max_peers: int = Field(default=50, ge=1, le=200)
    command_timeout_s: float = Field(default=5.0, ge=1.0, le=60.0)
    handoff_timeout_s: float = Field(default=30.0, ge=5.0, le=300.0)

    # Budget governance thresholds (percentage of ceiling)
    budget_warning_pct: float = Field(default=80.0, ge=50.0, le=100.0)
    budget_critical_pct: float = Field(default=95.0, ge=70.0, le=100.0)

    # Transport
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_backoff_s: float = Field(default=1.0, ge=0.1, le=10.0)
    connect_timeout_s: float = Field(default=5.0, ge=1.0, le=30.0)
    read_timeout_s: float = Field(default=30.0, ge=5.0, le=120.0)

    # Circuit breaker
    circuit_breaker_threshold: int = Field(default=5, ge=2, le=50)
    circuit_breaker_recovery_s: float = Field(default=60.0, ge=10.0, le=600.0)

    # Auth
    auth_timestamp_window_s: float = Field(default=30.0, ge=5.0, le=120.0)
    nonce_cache_size: int = Field(default=10_000, ge=1000, le=100_000)

    # Persistent storage
    peer_db_path: str = Field(default="")  # Empty = data_dir/federation_peers.sqlite

    # Cost reporting
    cost_report_interval_s: float = Field(default=30.0, ge=5.0, le=300.0)
    daily_budget_ceiling_usd: float = Field(default=10.0, ge=0.01)

    # This instance's externally-reachable address (for peer registration)
    self_address: str = Field(default="")


def get_federation_config_path(config_dir: Optional[str] = None) -> Path:
    """Return the canonical federation config path."""
    if config_dir is None:
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )
    return Path(config_dir) / "federation.yaml"


def load_federation_config(
    config_dir: Optional[str] = None,
) -> FederationConfig:
    """Load Federation configuration from YAML file.

    Falls back to defaults if the config file doesn't exist.
    """
    config_path = get_federation_config_path(config_dir)

    if not config_path.exists():
        logger.info("No federation.yaml found at %s, using defaults", config_path)
        return FederationConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = FederationConfig(**data)
        logger.info(
            "Federation config loaded: heartbeat=%.1fs, tls=%s, max_peers=%d",
            config.heartbeat_interval_s,
            config.tls_required,
            config.max_peers,
        )
        return config
    except Exception as exc:
        logger.warning("Failed to load federation.yaml: %s — using defaults", exc)
        return FederationConfig()


def save_federation_config(
    config: FederationConfig,
    config_dir: Optional[str] = None,
) -> Path:
    """Persist federation configuration to YAML."""
    config_path = get_federation_config_path(config_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            config.model_dump(mode="python"),
            f,
            sort_keys=False,
            default_flow_style=False,
        )
    logger.info("Federation config saved to %s", config_path)
    return config_path
