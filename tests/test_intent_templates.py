"""Tests for vNext4 IntentTemplate system (Prompts 16-17)."""

import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.intent_templates import (
    IntentTemplate,
    IntentTemplateRegistry,
    PlanStepTemplate,
)
from governance.config import IntentTemplateConfig
from governance.models import RiskTier


@pytest.fixture
def config():
    return IntentTemplateConfig(promotion_threshold=3, max_template_risk_tier=1)


@pytest.fixture
def registry(tmp_path, config):
    return IntentTemplateRegistry(config=config, data_dir=str(tmp_path))


def make_steps(*tiers):
    """Create plan step dicts with given risk tiers."""
    caps = {0: "fs.read", 1: "fs.write", 2: "shell.exec", 3: "net.post"}
    return [
        {"capability": caps.get(t, f"cap.t{t}"), "risk_tier": t}
        for t in tiers
    ]


# ── Prompt 16: Data Model Tests ──────────────────────────────────

def test_to_dict_from_dict_roundtrip():
    """IntentTemplate.to_dict() and from_dict() round-trip correctly."""
    steps = [PlanStepTemplate(capability="fs.read", risk_tier=RiskTier.T0_INERT)]
    template = IntentTemplate(
        template_id="test-1",
        intent_pattern="read files",
        plan_skeleton=steps,
        max_risk_tier=RiskTier.T0_INERT,
        success_count=5,
        failure_count=1,
        active=True,
    )
    d = template.to_dict()
    restored = IntentTemplate.from_dict(d)
    assert restored.template_id == "test-1"
    assert restored.intent_pattern == "read files"
    assert len(restored.plan_skeleton) == 1
    assert restored.plan_skeleton[0].capability == "fs.read"
    assert restored.success_count == 5
    assert restored.active is True


def test_registry_loads_from_empty_dir(tmp_path, config):
    """Registry loads from empty directory without error."""
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    assert len(reg.list_all()) == 0


def test_create_candidate_inactive(registry):
    """create_candidate() creates a template with active=False."""
    tid = registry.create_candidate("read workspace", make_steps(0))
    template = registry.get_template(tid)
    assert template is not None
    assert template.active is False
    assert template.success_count == 1


def test_create_candidate_persists_to_json(tmp_path, config):
    """create_candidate() persists to JSON file."""
    reg = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    reg.create_candidate("read workspace", make_steps(0))
    path = tmp_path / "intent_templates.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data) == 1
    assert data[0]["intent_pattern"] == "read workspace"


def test_create_candidate_rejects_t2(registry):
    """create_candidate() with T2 step raises ValueError."""
    with pytest.raises(ValueError, match="exceeds max_template_risk_tier"):
        registry.create_candidate("run command", make_steps(2))


def test_create_candidate_t0_t1_succeeds(registry):
    """create_candidate() with T0 and T1 steps succeeds."""
    tid = registry.create_candidate("read and write", make_steps(0, 1))
    template = registry.get_template(tid)
    assert len(template.plan_skeleton) == 2


def test_list_active_empty(registry):
    """list_active() returns empty when no templates are promoted."""
    registry.create_candidate("test", make_steps(0))
    assert len(registry.list_active()) == 0


def test_list_all(registry):
    """list_all() returns all templates."""
    registry.create_candidate("test1", make_steps(0))
    registry.create_candidate("test2", make_steps(1))
    assert len(registry.list_all()) == 2


def test_get_template(registry):
    """get_template() returns correct template by ID."""
    tid = registry.create_candidate("test", make_steps(0))
    template = registry.get_template(tid)
    assert template.template_id == tid
    assert template.intent_pattern == "test"


def test_registry_reloads_from_json(tmp_path, config):
    """Registry reloads from persisted JSON on re-init."""
    reg1 = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    tid = reg1.create_candidate("persistent test", make_steps(0, 1))

    reg2 = IntentTemplateRegistry(config=config, data_dir=str(tmp_path))
    template = reg2.get_template(tid)
    assert template is not None
    assert template.intent_pattern == "persistent test"


# ── Prompt 17: Matching + Promotion Tests ────────────────────────

def test_match_no_active_templates(registry):
    """match() returns None when no active templates exist."""
    registry.create_candidate("test", make_steps(0))
    assert registry.match("test something") is None


def test_match_returns_after_promotion(registry):
    """match() returns template after promotion."""
    tid = registry.create_candidate("read workspace", make_steps(0))
    # Promote (threshold=3, already at 1 from creation)
    registry.record_success(tid)
    registry.record_success(tid)
    assert registry.get_template(tid).active is True
    result = registry.match("please read workspace files")
    assert result is not None
    assert result.template_id == tid


def test_promotion_lifecycle(registry):
    """Promotion: create candidate, record 3 successes (threshold=3), template becomes active."""
    tid = registry.create_candidate("workspace read", make_steps(0))
    assert registry.get_template(tid).active is False
    # Already at success_count=1 from creation
    registry.record_success(tid)  # 2
    assert registry.get_template(tid).active is False
    registry.record_success(tid)  # 3 → promoted
    assert registry.get_template(tid).active is True


def test_match_after_promotion(registry):
    """After promotion, match() returns the template."""
    tid = registry.create_candidate("list files", make_steps(0))
    registry.record_success(tid)
    registry.record_success(tid)

    result = registry.match("list files in directory")
    assert result is not None


def test_match_updates_last_used(registry):
    """match() updates last_used timestamp."""
    tid = registry.create_candidate("read data", make_steps(0))
    registry.record_success(tid)
    registry.record_success(tid)

    template = registry.get_template(tid)
    old_last_used = template.last_used

    registry.match("read data from workspace")
    assert template.last_used != old_last_used
    assert template.last_used != ""


def test_failure_deactivates(registry):
    """record_failure() deactivates template when failures > successes."""
    tid = registry.create_candidate("test", make_steps(0))
    registry.record_success(tid)
    registry.record_success(tid)
    # Now active, success_count=3
    assert registry.get_template(tid).active is True

    # Record 4 failures (>3 successes)
    registry.record_failure(tid)
    registry.record_failure(tid)
    registry.record_failure(tid)
    assert registry.get_template(tid).active is True  # 3 failures == 3 successes
    registry.record_failure(tid)  # 4 > 3
    assert registry.get_template(tid).active is False


def test_invalidate(registry):
    """invalidate() sets active=False."""
    tid = registry.create_candidate("test", make_steps(0))
    registry.record_success(tid)
    registry.record_success(tid)
    assert registry.get_template(tid).active is True

    registry.invalidate(tid, reason="soul changed")
    assert registry.get_template(tid).active is False
    assert registry.get_template(tid).invalidation_reason == "soul changed"


def test_invalidate_all(registry):
    """invalidate_all() deactivates all templates and returns count."""
    tid1 = registry.create_candidate("test1", make_steps(0))
    tid2 = registry.create_candidate("test2", make_steps(0))
    # Promote both
    registry.record_success(tid1)
    registry.record_success(tid1)
    registry.record_success(tid2)
    registry.record_success(tid2)

    count = registry.invalidate_all(reason="soul updated")
    assert count == 2
    assert all(not t.active for t in registry.list_all())


def test_cleanup_stale(registry):
    """cleanup_stale() removes old templates."""
    tid = registry.create_candidate("old intent", make_steps(0))
    # Force created_at to be old
    template = registry.get_template(tid)
    template.created_at = "2020-01-01T00:00:00+00:00"
    template.last_used = ""
    registry._save()

    removed = registry.cleanup_stale()
    assert removed == 1
    assert registry.get_template(tid) is None


def test_promotion_threshold_respected(registry):
    """2 successes with threshold=3 → still inactive."""
    tid = registry.create_candidate("test", make_steps(0))
    # success_count is 1 from creation
    registry.record_success(tid)  # 2
    assert registry.get_template(tid).active is False
    assert registry.get_template(tid).success_count == 2
