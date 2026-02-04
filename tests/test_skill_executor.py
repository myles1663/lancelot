"""
Tests for src.core.skills.executor — Skill loader + execution (Prompt 8 / B3-B4).
"""

import pytest
import yaml
from pathlib import Path

from src.core.skills.schema import SkillError
from src.core.skills.registry import SkillRegistry, SkillOwnership
from src.core.skills.executor import (
    SkillContext,
    SkillResult,
    SkillExecutor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill_manifest(tmp_path, name="echo", **overrides):
    """Write a valid skill.yaml and return its path."""
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"Skill: {name}",
        "inputs": [{"name": "text", "type": "string", "required": True}],
        "outputs": [{"name": "result", "type": "string"}],
        "risk": "low",
        "permissions": ["read_input"],
        "required_brain": "local_utility",
    }
    manifest.update(overrides)
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = skill_dir / "skill.yaml"
    manifest_path.write_text(yaml.dump(manifest), encoding="utf-8")
    return str(manifest_path)


def _write_execute_py(tmp_path, name, code):
    """Write an execute.py for a skill."""
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "execute.py").write_text(code, encoding="utf-8")


@pytest.fixture
def registry(tmp_path):
    data_dir = str(tmp_path / "data")
    return SkillRegistry(data_dir)


# ===================================================================
# Echo skill end-to-end
# ===================================================================

class TestEchoSkill:

    def test_echo_skill_runs_end_to_end(self, tmp_path, registry):
        """Blueprint requirement: run echo skill end-to-end."""
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run("echo", {"message": "hello"})

        assert result.success is True
        assert result.outputs == {"echo": {"message": "hello"}}
        assert result.duration_ms >= 0

    def test_echo_returns_input_as_output(self, tmp_path, registry):
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run("echo", {"a": 1, "b": "two"})
        assert result.outputs["echo"] == {"a": 1, "b": "two"}


# ===================================================================
# Receipt on run
# ===================================================================

class TestReceipts:

    def test_receipt_created_on_run(self, tmp_path, registry):
        """Blueprint requirement: receipt created on run."""
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run("echo", {"msg": "test"})

        assert result.receipt is not None
        assert result.receipt["event"] == "skill_ran"
        assert result.receipt["skill"] == "echo"

    def test_receipt_on_failure(self, tmp_path, registry):
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)
        registry.disable_skill("echo")

        executor = SkillExecutor(registry)
        result = executor.run("echo", {"msg": "test"})

        assert result.success is False
        receipts = executor.receipts
        assert any(r["event"] == "skill_failed" for r in receipts)

    def test_receipts_accumulated(self, tmp_path, registry):
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        executor.run("echo", {"a": 1})
        executor.run("echo", {"b": 2})
        assert len(executor.receipts) == 2


# ===================================================================
# Skill not found / disabled
# ===================================================================

class TestErrorCases:

    def test_not_found_returns_failure(self, registry):
        executor = SkillExecutor(registry)
        result = executor.run("nonexistent", {})
        assert result.success is False
        assert "not found" in result.error

    def test_disabled_skill_returns_failure(self, tmp_path, registry):
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)
        registry.disable_skill("echo")

        executor = SkillExecutor(registry)
        result = executor.run("echo", {})
        assert result.success is False
        assert "disabled" in result.error


# ===================================================================
# Custom execute.py loading
# ===================================================================

class TestCustomExecute:

    def test_load_custom_execute_py(self, tmp_path, registry):
        name = "custom_skill"
        manifest_path = _write_skill_manifest(tmp_path, name=name)
        _write_execute_py(tmp_path, name, """
def execute(context, inputs):
    return {"doubled": inputs.get("value", 0) * 2}
""")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run(name, {"value": 5})
        assert result.success is True
        assert result.outputs["doubled"] == 10

    def test_execute_py_with_error(self, tmp_path, registry):
        name = "failing_skill"
        manifest_path = _write_skill_manifest(tmp_path, name=name)
        _write_execute_py(tmp_path, name, """
def execute(context, inputs):
    raise ValueError("skill broke")
""")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run(name, {})
        assert result.success is False
        assert "skill broke" in result.error

    def test_missing_execute_function_in_module(self, tmp_path, registry):
        name = "no_func_skill"
        manifest_path = _write_skill_manifest(tmp_path, name=name)
        _write_execute_py(tmp_path, name, """
# No execute function defined
pass
""")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        result = executor.run(name, {})
        assert result.success is False
        assert "execute" in result.error.lower()


# ===================================================================
# SkillContext
# ===================================================================

class TestSkillContext:

    def test_default_context(self, tmp_path, registry):
        manifest_path = _write_skill_manifest(tmp_path, name="echo")
        registry.install_skill(manifest_path)

        executor = SkillExecutor(registry)
        ctx = SkillContext(skill_name="echo", request_id="r123", caller="owner")
        result = executor.run("echo", {"x": 1}, context=ctx)
        assert result.success is True
