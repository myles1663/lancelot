"""
Tests for P65: APL Module Scaffold + Feature Flag + Config.
"""

import os
import pytest
from unittest.mock import patch

from src.core.governance.approval_learning.config import (
    APLConfig,
    DetectionConfig,
    RulesConfig,
    PersistenceConfig,
    load_apl_config,
)


class TestFeatureFlag:
    def test_defaults_false(self):
        from src.core import feature_flags
        feature_flags.reload_flags()
        assert feature_flags.FEATURE_APPROVAL_LEARNING is False

    def test_env_true(self):
        from src.core import feature_flags
        with patch.dict(os.environ, {"FEATURE_APPROVAL_LEARNING": "true"}):
            feature_flags.reload_flags()
            assert feature_flags.FEATURE_APPROVAL_LEARNING is True
        feature_flags.reload_flags()


class TestDetectionConfig:
    def test_defaults(self):
        c = DetectionConfig()
        assert c.min_observations == 20
        assert c.confidence_threshold == 0.85
        assert c.max_pattern_dimensions == 3
        assert c.analysis_window_days == 30
        assert c.analysis_trigger_interval == 10

    def test_confidence_validation(self):
        with pytest.raises(Exception):
            DetectionConfig(confidence_threshold=1.5)

    def test_dimensions_validation(self):
        with pytest.raises(Exception):
            DetectionConfig(max_pattern_dimensions=0)


class TestRulesConfig:
    def test_defaults(self):
        c = RulesConfig()
        assert c.max_active_rules == 50
        assert c.max_auto_decisions_per_day == 50
        assert c.max_auto_decisions_total == 500
        assert c.re_confirmation_interval == 500
        assert c.cooldown_after_decline == 30


class TestPersistenceConfig:
    def test_paths(self):
        c = PersistenceConfig()
        assert c.decision_log_path == "data/apl/decisions.jsonl"
        assert c.rules_path == "data/apl/rules.json"
        assert c.patterns_path == "data/apl/patterns.json"


class TestAPLConfig:
    def test_load_yaml(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert config.version == "1.0"
        assert config.detection.min_observations == 20

    def test_never_automate_from_yaml(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert len(config.never_automate) == 4

    def test_is_never_automate_exact(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert config.is_never_automate("connector.stripe.charge_customer") is True

    def test_is_never_automate_false(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert config.is_never_automate("connector.email.send_message") is False

    def test_is_never_automate_wildcard_delete(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert config.is_never_automate("connector.email.delete_message") is True

    def test_is_never_automate_wildcard_slack_delete(self):
        config = load_apl_config("config/approval_learning.yaml")
        assert config.is_never_automate("connector.slack.delete_channel") is True

    def test_missing_file_returns_defaults(self):
        config = load_apl_config("/nonexistent/path.yaml")
        assert isinstance(config, APLConfig)
        assert config.detection.min_observations == 20
        assert len(config.never_automate) == 0
