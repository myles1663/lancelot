# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation config loading."""

import os
import tempfile

import pytest
from src.federation.config import FederationConfig, load_federation_config, save_federation_config


class TestFederationConfig:
    """Test FederationConfig model and defaults."""

    def test_default_values(self):
        config = FederationConfig()
        assert config.heartbeat_interval_s == 2.0
        assert config.staleness_warning_s == 10.0
        assert config.staleness_critical_s == 20.0
        assert config.staleness_lost_s == 30.0
        assert config.tls_required is False
        assert config.max_peers == 50
        assert config.command_timeout_s == 5.0
        assert config.handoff_timeout_s == 30.0
        assert config.budget_warning_pct == 80.0
        assert config.budget_critical_pct == 95.0

    def test_custom_values(self):
        config = FederationConfig(
            heartbeat_interval_s=5.0,
            max_peers=10,
            tls_required=True,
        )
        assert config.heartbeat_interval_s == 5.0
        assert config.max_peers == 10
        assert config.tls_required is True

    def test_load_from_yaml(self):
        """Test loading config from a YAML file."""
        yaml_content = """
heartbeat_interval_s: 3.0
staleness_warning_s: 15.0
max_peers: 25
tls_required: true
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "federation.yaml")
            with open(config_file, "w") as f:
                f.write(yaml_content)
            config = load_federation_config(config_dir=tmpdir)

        assert config.heartbeat_interval_s == 3.0
        assert config.staleness_warning_s == 15.0
        assert config.max_peers == 25
        assert config.tls_required is True
        # Defaults for unset fields
        assert config.staleness_critical_s == 20.0

    def test_load_missing_file_returns_defaults(self):
        """Loading a non-existent dir should return default config."""
        config = load_federation_config(config_dir="/nonexistent/path")
        assert config.heartbeat_interval_s == 2.0
        assert config.max_peers == 50

    def test_load_empty_file_returns_defaults(self):
        """Loading an empty YAML file should return default config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "federation.yaml")
            with open(config_file, "w") as f:
                f.write("")
            config = load_federation_config(config_dir=tmpdir)
        assert config.heartbeat_interval_s == 2.0

    def test_save_and_reload_config(self):
        """Persisted federation config should round-trip through YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = FederationConfig(self_address="https://mesh.example.internal:8000")
            save_federation_config(config, config_dir=tmpdir)
            reloaded = load_federation_config(config_dir=tmpdir)

        assert reloaded.self_address == "https://mesh.example.internal:8000"
