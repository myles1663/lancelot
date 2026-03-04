"""Tests for HIVE config — YAML loading and defaults."""

import os
import tempfile
import pytest
import yaml

from src.hive.config import HiveConfig, load_hive_config


class TestHiveConfig:
    def test_defaults(self):
        config = HiveConfig()
        assert config.max_concurrent_agents == 10
        assert config.default_task_timeout == 300
        assert config.max_actions_per_agent == 50
        assert config.max_subtasks_per_decomposition == 20
        assert config.spawn_approval_tier == "T2"
        assert config.default_control_method == "supervised"
        assert config.collapse_on_governance_violation is True
        assert config.collapse_on_soul_violation is True
        assert config.uab_enabled is False
        assert config.uab_allowed_apps == []
        assert config.max_retry_attempts == 2
        assert config.never_retry_identical_plan is True

    def test_custom_values(self):
        config = HiveConfig(
            max_concurrent_agents=5,
            default_task_timeout=60,
            default_control_method="fully_autonomous",
            uab_enabled=True,
            uab_allowed_apps=["notepad", "chrome"],
        )
        assert config.max_concurrent_agents == 5
        assert config.default_task_timeout == 60
        assert config.default_control_method == "fully_autonomous"
        assert config.uab_enabled is True
        assert config.uab_allowed_apps == ["notepad", "chrome"]

    def test_validation_min_agents(self):
        with pytest.raises(Exception):
            HiveConfig(max_concurrent_agents=0)

    def test_validation_max_agents(self):
        with pytest.raises(Exception):
            HiveConfig(max_concurrent_agents=100)

    def test_validation_min_timeout(self):
        with pytest.raises(Exception):
            HiveConfig(default_task_timeout=5)


class TestLoadHiveConfig:
    def test_load_from_yaml(self, tmp_path):
        config_data = {
            "max_concurrent_agents": 5,
            "default_task_timeout": 120,
            "spawn_approval_tier": "T1",
            "uab_enabled": True,
        }
        config_file = tmp_path / "hive.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_hive_config(str(tmp_path))
        assert config.max_concurrent_agents == 5
        assert config.default_task_timeout == 120
        assert config.spawn_approval_tier == "T1"
        assert config.uab_enabled is True

    def test_missing_file_returns_defaults(self, tmp_path):
        config = load_hive_config(str(tmp_path))
        assert config.max_concurrent_agents == 10
        assert config.default_task_timeout == 300

    def test_empty_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "hive.yaml"
        config_file.write_text("")

        config = load_hive_config(str(tmp_path))
        assert config.max_concurrent_agents == 10

    def test_invalid_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "hive.yaml"
        config_file.write_text("not: [valid: yaml: {{{")

        config = load_hive_config(str(tmp_path))
        assert config.max_concurrent_agents == 10

    def test_partial_yaml_fills_defaults(self, tmp_path):
        config_data = {"max_concurrent_agents": 3}
        config_file = tmp_path / "hive.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_hive_config(str(tmp_path))
        assert config.max_concurrent_agents == 3
        assert config.default_task_timeout == 300  # default
