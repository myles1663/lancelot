"""Tests for vNext4 PolicyCache (Prompts 8-9)."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import load_governance_config, PolicyCacheConfig
from governance.risk_classifier import RiskClassifier
from governance.policy_cache import PolicyCache, CachedPolicyDecision
from governance.models import RiskTier


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")


@pytest.fixture
def gov_config():
    return load_governance_config(CONFIG_PATH)


@pytest.fixture
def classifier(gov_config):
    return RiskClassifier(gov_config.risk_classification)


@pytest.fixture
def cache(gov_config, classifier):
    return PolicyCache(
        config=gov_config.policy_cache,
        risk_classifier=classifier,
        soul_version="test_v1",
    )


# ── Compilation Tests (Prompt 8) ────────────────────────────────

def test_cache_compiles_without_error(cache):
    assert cache is not None


def test_cache_has_t0_entries(cache):
    """Cache contains entries for T0 capabilities."""
    decision = cache.lookup("fs.read", "workspace")
    assert decision is not None
    assert decision.tier == RiskTier.T0_INERT


def test_cache_has_t1_entries(cache):
    """Cache contains entries for T1 capabilities."""
    decision = cache.lookup("fs.write", "workspace")
    assert decision is not None
    assert decision.tier == RiskTier.T1_REVERSIBLE


def test_cache_no_t2_entries(cache):
    """Cache does NOT contain entries for T2 capabilities."""
    decision = cache.lookup("shell.exec", "workspace")
    assert decision is None


def test_cache_no_t3_entries(cache):
    """Cache does NOT contain entries for T3 capabilities."""
    decision = cache.lookup("net.post", "workspace")
    assert decision is None


def test_fs_read_decision_allow(cache):
    decision = cache.lookup("fs.read", "workspace")
    assert decision.decision == "allow"
    assert decision.tier == RiskTier.T0_INERT


def test_fs_write_decision_allow(cache):
    decision = cache.lookup("fs.write", "workspace")
    assert decision.decision == "allow"
    assert decision.tier == RiskTier.T1_REVERSIBLE


def test_total_entries_count(cache):
    """Total entries match expected T0 + T1 count."""
    # T0: fs.read, fs.list, git.status, git.log, git.diff, memory.read = 6
    # T1: fs.write, git.commit, git.branch, memory.write = 4
    # Total = 10
    assert cache.stats.total_entries == 10


def test_soul_version_stored(cache):
    assert cache.stats.soul_version == "test_v1"


def test_compiled_at_valid(cache):
    from datetime import datetime
    compiled_at = cache.stats.compiled_at
    assert compiled_at
    datetime.fromisoformat(compiled_at.replace("Z", "+00:00") if compiled_at.endswith("Z") else compiled_at)


# ── Lookup + Stats Tests (Prompt 9) ─────────────────────────────

def test_lookup_hit_increments(cache):
    cache.lookup("fs.read", "workspace")
    assert cache.stats.hits >= 1


def test_lookup_miss_increments(cache):
    cache.lookup("shell.exec", "workspace")
    assert cache.stats.misses >= 1


def test_hit_rate_correct(cache):
    cache.lookup("fs.read", "workspace")  # hit
    cache.lookup("shell.exec", "workspace")  # miss
    stats = cache.stats
    assert stats.hit_rate == pytest.approx(0.5)


def test_lookup_nonexistent_returns_none(cache):
    assert cache.lookup("nonexistent_cap") is None


# ── Invalidation Tests ──────────────────────────────────────────

def test_invalidate_clears_entries(cache):
    cache.invalidate()
    assert cache.stats.total_entries == 0
    assert cache.stats.hits == 0
    assert cache.stats.misses == 0


def test_invalidate_lookup_returns_none(cache):
    cache.invalidate()
    assert cache.lookup("fs.read", "workspace") is None


# ── Recompile Tests ─────────────────────────────────────────────

def test_recompile_rebuilds(gov_config, classifier, cache):
    cache.recompile(classifier, soul_version="test_v2")
    assert cache.stats.soul_version == "test_v2"
    assert cache.stats.total_entries == 10


def test_recompile_new_soul_version(gov_config, classifier, cache):
    cache.recompile(classifier, soul_version="test_v2")
    decision = cache.lookup("fs.read", "workspace")
    assert decision is not None
    assert decision.soul_version == "test_v2"


# ── Soul Version Validation ─────────────────────────────────────

def test_soul_version_mismatch_returns_none(gov_config, classifier):
    """If validate_soul_version=True and versions differ, treat as miss."""
    config = PolicyCacheConfig(validate_soul_version=True)
    cache = PolicyCache(config=config, risk_classifier=classifier, soul_version="v1")
    assert cache.lookup("fs.read", "workspace") is not None

    # Now change the internal soul version
    cache._soul_version = "v2"
    # Cached entries still have soul_version="v1", lookup should fail
    result = cache.lookup("fs.read", "workspace")
    assert result is None
