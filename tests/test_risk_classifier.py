"""Tests for vNext4 RiskClassifier (Prompts 4-5)."""

import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import load_governance_config
from governance.risk_classifier import RiskClassifier
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")


@pytest.fixture
def config():
    return load_governance_config(CONFIG_PATH)


@pytest.fixture
def classifier(config):
    return RiskClassifier(config.risk_classification)


@pytest.fixture
def test_soul():
    return {
        "version": "test_v1",
        "governance": {
            "escalations": [
                {
                    "capability": "fs.write",
                    "pattern": "*.secret",
                    "escalate_to": 3,
                    "reason": "Secret files are irreversible",
                },
                {
                    "capability": "docker.run",
                    "scope": "workspace",
                    "escalate_to": 3,
                    "reason": "Docker always irreversible per Soul",
                },
            ]
        },
    }


@pytest.fixture
def soul_classifier(config, test_soul):
    return RiskClassifier(config.risk_classification, soul=test_soul)


# ── Default Tier Tests (Prompt 4) ────────────────────────────────

def test_fs_read_is_t0(classifier):
    profile = classifier.classify("fs.read", "workspace")
    assert profile.tier == RiskTier.T0_INERT


def test_fs_write_is_t1(classifier):
    profile = classifier.classify("fs.write", "workspace")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_shell_exec_is_t2(classifier):
    profile = classifier.classify("shell.exec", "workspace")
    assert profile.tier == RiskTier.T2_CONTROLLED


def test_net_post_is_t3(classifier):
    profile = classifier.classify("net.post", "workspace")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_git_status_is_t0(classifier):
    profile = classifier.classify("git.status")
    assert profile.tier == RiskTier.T0_INERT


def test_git_commit_is_t1(classifier):
    profile = classifier.classify("git.commit")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_memory_read_is_t0(classifier):
    profile = classifier.classify("memory.read")
    assert profile.tier == RiskTier.T0_INERT


def test_memory_write_is_t1(classifier):
    profile = classifier.classify("memory.write")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_unknown_capability_is_t3(classifier):
    profile = classifier.classify("unknown_capability")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


# ── Scope Escalation Tests ──────────────────────────────────────

def test_scope_escalation_fs_write_outside(classifier):
    profile = classifier.classify("fs.write", "outside_workspace")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_scope_escalation_shell_exec_unscoped(classifier):
    profile = classifier.classify("shell.exec", "unscoped")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_pattern_escalation_env_file(classifier):
    profile = classifier.classify("fs.write", "workspace", target=".env")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_pattern_escalation_config_env(classifier):
    profile = classifier.classify("fs.write", "workspace", target="config.env")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_no_escalation_readme(classifier):
    profile = classifier.classify("fs.write", "workspace", target="readme.md")
    assert profile.tier == RiskTier.T1_REVERSIBLE


# ── Reversibility Flag ──────────────────────────────────────────

def test_reversible_flag_t0_t1(classifier):
    assert classifier.classify("fs.read").reversible is True
    assert classifier.classify("fs.write", "workspace").reversible is True


def test_reversible_flag_t2_t3(classifier):
    assert classifier.classify("shell.exec").reversible is False
    assert classifier.classify("net.post").reversible is False


# ── classify_step ────────────────────────────────────────────────

def test_classify_step(classifier):
    profile = classifier.classify_step({"capability": "fs.read", "scope": "workspace", "target": None})
    assert profile.tier == RiskTier.T0_INERT


# ── known_capabilities ──────────────────────────────────────────

def test_known_capabilities_count(classifier):
    assert len(classifier.known_capabilities) == 14


# ── Soul Escalation Tests (Prompt 5) ────────────────────────────

def test_soul_escalation_secret_file(soul_classifier):
    profile = soul_classifier.classify("fs.write", "workspace", "data.secret")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE
    assert profile.soul_escalation is not None


def test_soul_escalation_reason(soul_classifier):
    profile = soul_classifier.classify("fs.write", "workspace", "data.secret")
    assert profile.soul_escalation == "Secret files are irreversible"


def test_soul_escalation_docker_run(soul_classifier):
    profile = soul_classifier.classify("docker.run", "workspace")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_soul_no_downgrade(soul_classifier):
    """Soul cannot downgrade: if default is T3, Soul escalation to T2 is ignored."""
    profile = soul_classifier.classify("net.post", "workspace")
    assert profile.tier == RiskTier.T3_IRREVERSIBLE


def test_no_soul_no_escalation(classifier):
    """Without Soul, pattern escalation doesn't apply for soul-specific rules."""
    profile = classifier.classify("fs.write", "workspace", "data.secret")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_soul_without_governance():
    """Soul without governance section: no crash, no escalation."""
    from governance.config import load_governance_config
    config = load_governance_config(CONFIG_PATH)
    soul = {"version": "test", "other_data": True}
    c = RiskClassifier(config.risk_classification, soul=soul)
    profile = c.classify("fs.write", "workspace", "data.secret")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_soul_empty_escalations():
    """Soul with empty escalations list: no crash, no escalation."""
    from governance.config import load_governance_config
    config = load_governance_config(CONFIG_PATH)
    soul = {"version": "test", "governance": {"escalations": []}}
    c = RiskClassifier(config.risk_classification, soul=soul)
    profile = c.classify("fs.write", "workspace", "data.secret")
    assert profile.tier == RiskTier.T1_REVERSIBLE


def test_update_soul_changes_behavior(config, test_soul):
    """update_soul() with new soul changes escalation behavior."""
    c = RiskClassifier(config.risk_classification)
    # No soul — no escalation
    assert c.classify("fs.write", "workspace", "data.secret").tier == RiskTier.T1_REVERSIBLE
    # Add soul
    c.update_soul(test_soul)
    assert c.classify("fs.write", "workspace", "data.secret").tier == RiskTier.T3_IRREVERSIBLE
