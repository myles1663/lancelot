from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.core.skills import executor
from src.core.skills.executor import SkillContext, SkillExecutor
from src.core.skills.registry import SkillEntry, SkillOwnership, SignatureState


class _Registry:
    def __init__(self, entry=None):
        self.entry = entry

    def get_skill(self, name):
        if self.entry is not None and self.entry.name == name:
            return self.entry
        return None


def _entry(name="custom_skill", *, enabled=True, manifest_path="", ownership=SkillOwnership.USER):
    return SkillEntry(
        name=name,
        version="1.0.0",
        enabled=enabled,
        manifest_path=manifest_path,
        ownership=ownership,
        signature_state=SignatureState.UNSIGNED,
    )


def test_builtin_loader_rejects_unknown_builtin_module():
    run_unknown = executor._load_builtin_execute("does_not_exist")

    with pytest.raises(Exception, match="Unknown builtin"):
        run_unknown(SkillContext(skill_name="does_not_exist"), {})


def test_module_loader_rejects_missing_spec_loader_and_missing_execute(tmp_path, monkeypatch):
    skill_file = tmp_path / "execute.py"
    skill_file.write_text("VALUE = 1\n", encoding="utf-8")
    skill_executor = SkillExecutor(_Registry())

    monkeypatch.setattr(executor.importlib.util, "spec_from_file_location", lambda *_: None)
    with pytest.raises(Exception, match="Cannot load module spec"):
        skill_executor._load_module_execute(skill_file, "bad_spec")

    monkeypatch.undo()
    with pytest.raises(Exception, match="no callable 'execute'"):
        skill_executor._load_module_execute(skill_file, "missing_execute")


def test_module_loader_wraps_import_errors(tmp_path):
    skill_file = tmp_path / "execute.py"
    skill_file.write_text("raise RuntimeError('import exploded')\n", encoding="utf-8")

    with pytest.raises(Exception, match="Failed to load skill module"):
        SkillExecutor(_Registry())._load_module_execute(skill_file, "broken")


def test_unsigned_system_skill_loads_in_process_and_emits_receipt(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manifest = skill_dir / "skill.yaml"
    manifest.write_text("name: local\n", encoding="utf-8")
    (skill_dir / "execute.py").write_text(
        "def execute(context, inputs):\n    return {'caller': context.caller, 'value': inputs['value']}\n",
        encoding="utf-8",
    )
    skill_executor = SkillExecutor(
        _Registry(_entry("local", manifest_path=str(manifest), ownership=SkillOwnership.SYSTEM))
    )

    result = skill_executor.run("local", {"value": 7}, SkillContext(skill_name="local", caller="tester"))

    assert result.success is True
    assert result.outputs == {"caller": "tester", "value": 7}
    assert any(receipt["event"] == "skill_unsigned_load" for receipt in skill_executor.receipts)


def test_run_handles_missing_disabled_and_builtin_skill_paths():
    missing = SkillExecutor(_Registry()).run("missing", {})
    assert missing.success is False
    assert "not found" in missing.error

    disabled = SkillExecutor(_Registry(_entry("echo", enabled=False))).run("echo", {})
    assert disabled.success is False
    assert "disabled" in disabled.error

    builtin = SkillExecutor(_Registry()).run("echo", {"hello": "world"})
    assert builtin.success is True
    assert builtin.outputs == {"echo": {"hello": "world"}}


def test_run_reports_builtin_load_and_execute_failures(monkeypatch):
    skill_executor = SkillExecutor(_Registry(_entry("echo", ownership=SkillOwnership.SYSTEM)))

    monkeypatch.setattr(skill_executor, "_load_execute_func", lambda entry: (_ for _ in ()).throw(RuntimeError("load fail")))
    with pytest.raises(RuntimeError, match="load fail"):
        skill_executor.run("echo", {})

    monkeypatch.setattr(skill_executor, "_load_execute_func", lambda entry: (_ for _ in ()).throw(executor.SkillError("bad manifest")))
    manifest_failure = skill_executor.run("echo", {})
    assert manifest_failure.success is False
    assert "bad manifest" in manifest_failure.error

    monkeypatch.setattr(
        skill_executor,
        "_load_execute_func",
        lambda entry: lambda context, inputs: (_ for _ in ()).throw(ValueError("runtime fail")),
    )
    run_failure = skill_executor.run("echo", {})
    assert run_failure.success is False
    assert "runtime fail" in run_failure.error


def test_sandbox_requires_manifest_and_execute_file(tmp_path):
    skill_executor = SkillExecutor(_Registry())

    no_manifest = skill_executor._run_skill_in_sandbox(_entry(manifest_path=""), SkillContext("custom_skill"), {})
    assert no_manifest.success is False
    assert "manifest_path" in no_manifest.error

    manifest = tmp_path / "skill.yaml"
    manifest.write_text("name: custom_skill\n", encoding="utf-8")
    missing_execute = skill_executor._run_skill_in_sandbox(
        _entry(manifest_path=str(manifest)),
        SkillContext("custom_skill"),
        {},
    )
    assert missing_execute.success is False
    assert "execute.py not found" in missing_execute.error


def test_sandbox_subprocess_success_and_failure_paths(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manifest = skill_dir / "skill.yaml"
    manifest.write_text("name: custom_skill\n", encoding="utf-8")
    (skill_dir / "execute.py").write_text("def execute(context, inputs): return inputs\n", encoding="utf-8")
    entry = _entry(manifest_path=str(manifest))
    skill_executor = SkillExecutor(_Registry(entry))

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=3, stdout="", stderr="denied"),
    )
    failed = skill_executor._run_skill_in_sandbox(entry, SkillContext("custom_skill"), {})
    assert failed.success is False
    assert "Sandbox exited with code 3" in failed.error

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    )
    invalid_json = skill_executor._run_skill_in_sandbox(entry, SkillContext("custom_skill"), {})
    assert invalid_json.success is False
    assert "invalid JSON" in invalid_json.error

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout='{"success": false, "error": "skill error"}', stderr=""),
    )
    tool_error = skill_executor._run_skill_in_sandbox(entry, SkillContext("custom_skill"), {})
    assert tool_error.success is False
    assert tool_error.error == "skill error"

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout='{"success": true, "outputs": {"ok": true}}', stderr=""),
    )
    success = skill_executor.run("custom_skill", {})
    assert success.success is True
    assert success.outputs == {"ok": True}


def test_sandbox_timeout_and_unexpected_exception(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manifest = skill_dir / "skill.yaml"
    manifest.write_text("name: custom_skill\n", encoding="utf-8")
    (skill_dir / "execute.py").write_text("def execute(context, inputs): return inputs\n", encoding="utf-8")
    entry = _entry(manifest_path=str(manifest))
    skill_executor = SkillExecutor(_Registry(entry))

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda cmd, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, kwargs["timeout"])),
    )
    timeout = skill_executor._run_skill_in_sandbox(entry, SkillContext("custom_skill"), {})
    assert timeout.success is False
    assert "timed out" in timeout.error

    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_, **__: (_ for _ in ()).throw(OSError("docker missing")),
    )
    unexpected = skill_executor._run_skill_in_sandbox(entry, SkillContext("custom_skill"), {})
    assert unexpected.success is False
    assert "docker missing" in unexpected.error
