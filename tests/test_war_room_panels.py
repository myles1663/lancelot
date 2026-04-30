"""
Tests for War Room panels — Soul, Skills, Health, Scheduler (Prompt 14 / E1-E4).
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.core.skills.registry import SkillRegistry, SkillOwnership
from src.core.scheduler.service import SchedulerService
from src.ui.panels.soul_panel import SoulPanel
from src.ui.panels.skills_panel import SkillsPanel
from src.ui.panels.health_panel import HealthPanel
from src.ui.panels.scheduler_panel import SchedulerPanel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill_manifest(tmp_path, name="echo"):
    manifest = {
        "name": name, "version": "1.0.0",
        "permissions": ["read_input"],
        "inputs": [{"name": "text", "type": "string"}],
        "outputs": [{"name": "result", "type": "string"}],
        "risk": "low",
    }
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "skill.yaml"
    p.write_text(yaml.dump(manifest), encoding="utf-8")
    return str(p)


def _write_scheduler_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    jobs = [{"id": "test_job", "name": "Test", "trigger": {"type": "interval", "seconds": 60},
             "enabled": True, "timeout_s": 30, "skill": "echo", "requires_approvals": []}]
    (config_dir / "scheduler.yaml").write_text(yaml.dump({"jobs": jobs}), encoding="utf-8")
    return str(config_dir)


# ===================================================================
# Soul Panel
# ===================================================================

class TestSoulPanel:

    def test_render_data_on_backend_down(self):
        panel = SoulPanel(base_url="http://localhost:99999")
        data = panel.render_data()
        assert data["panel"] == "soul"
        assert data["active_version"] == "unknown"
        assert data["error"] is not None

    def test_render_data_structure(self):
        panel = SoulPanel(base_url="http://localhost:99999")
        data = panel.render_data()
        assert "active_version" in data
        assert "available_versions" in data
        assert "pending_proposals" in data

    def test_no_secrets_exposed(self):
        panel = SoulPanel(base_url="http://localhost:99999", token="secret-token")
        data = panel.render_data()
        rendered = str(data)
        assert "secret-token" not in rendered


# ===================================================================
# Skills Panel
# ===================================================================

class TestSkillsPanel:

    def test_list_empty_registry(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        panel = SkillsPanel(reg)
        data = panel.render_data()
        assert data["panel"] == "skills"
        assert data["skills"] == []

    def test_list_with_skill(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill_manifest(tmp_path)
        reg.install_skill(manifest_path)
        panel = SkillsPanel(reg)
        data = panel.render_data()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "echo"
        assert "permissions" in data["skills"][0]

    def test_enable_disable(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill_manifest(tmp_path)
        reg.install_skill(manifest_path)
        panel = SkillsPanel(reg)

        result = panel.disable_skill("echo")
        assert result["status"] == "disabled"

        result = panel.enable_skill("echo")
        assert result["status"] == "enabled"

    def test_enable_not_found(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        panel = SkillsPanel(reg)
        result = panel.enable_skill("nonexistent")
        assert "error" in result


# ===================================================================
# Health Panel
# ===================================================================

class TestHealthPanel:

    def test_render_data_on_backend_down(self):
        panel = HealthPanel(base_url="http://localhost:99999")
        data = panel.render_data()
        assert data["panel"] == "health"
        assert data["ready"] is False
        assert len(data["degraded_reasons"]) > 0

    def test_render_data_structure(self):
        panel = HealthPanel(base_url="http://localhost:99999")
        data = panel.render_data()
        assert "live_status" in data
        assert "ready" in data
        assert "degraded_reasons" in data
        assert "last_health_tick_at" in data
        assert "onboarding_state" in data

    def test_no_stack_traces_on_error(self):
        panel = HealthPanel(base_url="http://localhost:99999")
        data = panel.render_data()
        rendered = str(data)
        assert "Traceback" not in rendered


# ===================================================================
# Scheduler Panel
# ===================================================================

class TestSchedulerPanel:

    def test_list_empty(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        panel = SchedulerPanel(svc)
        data = panel.render_data()
        assert data["panel"] == "scheduler"
        assert data["jobs"] == []

    def test_list_with_jobs(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        svc.register_from_config()
        panel = SchedulerPanel(svc)
        data = panel.render_data()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["id"] == "test_job"

    def test_enable_disable(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        svc.register_from_config()
        panel = SchedulerPanel(svc)

        result = panel.disable_job("test_job")
        assert result["status"] == "disabled"

        result = panel.enable_job("test_job")
        assert result["status"] == "enabled"

    def test_run_now(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        svc.register_from_config()
        panel = SchedulerPanel(svc)

        result = panel.run_now("test_job")
        assert result["status"] == "triggered"
        assert result["run_count"] == 1

    def test_run_now_not_found(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        panel = SchedulerPanel(svc)
        result = panel.run_now("nonexistent")
        assert "error" in result

    def test_no_secrets_in_output(self, tmp_path):
        config_dir = _write_scheduler_config(tmp_path)
        svc = SchedulerService(str(tmp_path / "data"), config_dir)
        svc.register_from_config()
        panel = SchedulerPanel(svc)
        data = panel.render_data()
        rendered = str(data)
        assert "password" not in rendered.lower()
        assert "secret" not in rendered.lower()
