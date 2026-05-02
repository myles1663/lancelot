from __future__ import annotations

import sys
from types import SimpleNamespace

from src.tools import fabric
from src.tools.contracts import Capability, ExecResult, FileChange, PatchResult
from src.tools.fabric import ToolFabric, ToolFabricConfig
from src.tools.router import RouteDecision


class _Router:
    def __init__(self, route):
        self.route = route
        self.preferences = []

    def select_for_intent(self, intent):
        return self.route

    def select_provider(self, capability, **kwargs):
        return self.route

    def set_preferences(self, capability, chain):
        self.preferences.append((capability, chain))

    def get_routing_summary(self):
        return {"preferences": self.preferences}


class _Health:
    def __init__(self, provider=None):
        self.provider = provider
        self.registered = []
        self.unregistered = []
        self.probed = []

    def register(self, provider):
        self.registered.append(provider)

    def unregister(self, provider_id):
        self.unregistered.append(provider_id)

    def get_provider(self, provider_id):
        return self.provider

    def get_all_health(self):
        return {"provider": "healthy"}

    def probe(self, provider_id, force=False):
        self.probed.append((provider_id, force))
        return "specific-health"

    def sweep(self):
        return {"all": "healthy"}


class _Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def evaluate_path(self, path, workspace, action):
        return SimpleNamespace(allowed=self.allowed, reasons=["blocked path"])


class _ShellProvider:
    provider_id = "shell"

    def __init__(self, *, raises=False):
        self.raises = raises
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("provider exploded")
        return ExecResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, working_dir=kwargs["cwd"])


class _RepoProvider:
    provider_id = "repo"

    def status(self, workspace):
        return {"workspace": workspace}

    def diff(self, workspace, ref=None):
        return f"diff:{workspace}:{ref}"

    def apply_patch(self, workspace, patch, dry_run=False):
        return PatchResult(success=True, files_changed=[FileChange(path="a.txt", action="modified")])

    def commit(self, workspace, message, files=None):
        return f"commit:{message}:{files}"

    def branch(self, workspace, name, checkout=True):
        return True

    def checkout(self, workspace, ref):
        return True


class _FileProvider:
    provider_id = "files"

    def read(self, path):
        return f"read:{path}"

    def write(self, path, content, atomic=True):
        return FileChange(path=path, action="modified", size_after=len(content))

    def list(self, path, recursive=False):
        return [f"{path}:{recursive}"]

    def apply_diff(self, path, diff):
        return FileChange(path=path, action="modified")

    def delete(self, path):
        return FileChange(path=path, action="deleted")


def _bare_fabric(provider=None, route=None, *, receipts=True, path_allowed=True):
    tf = ToolFabric.__new__(ToolFabric)
    tf.config = ToolFabricConfig(enabled=True, emit_receipts=receipts, default_workspace="/workspace")
    tf._router = _Router(route or RouteDecision("provider", Capability.SHELL_EXEC, True, "ok"))
    tf._health_monitor = _Health(provider)
    tf._policy_engine = _Policy(path_allowed)
    tf.stored = []
    tf._store_receipt = lambda receipt: tf.stored.append(receipt)
    return tf


def test_default_provider_setup_registers_optional_providers(monkeypatch):
    created = []

    class Provider:
        def __init__(self, *args, **kwargs):
            self.provider_id = self.__class__.__name__
            self.config = SimpleNamespace(agent_url="http://host-agent", daemon_url="http://uab")
            created.append((self.__class__.__name__, args, kwargs))

    monkeypatch.setattr(fabric, "FEATURE_TOOLS_HOST_EXECUTION", True)
    monkeypatch.setattr(fabric, "FEATURE_TOOLS_HOST_BRIDGE", True)
    monkeypatch.setattr(fabric, "FEATURE_TOOLS_ANTIGRAVITY", True)
    monkeypatch.setattr(fabric, "FEATURE_TOOLS_UAB", True)
    monkeypatch.setattr(fabric, "FEATURE_TOOLS_CLI_PROVIDERS", True)
    monkeypatch.setattr(fabric, "LocalSandboxProvider", type("LocalSandboxProvider", (Provider,), {}))
    monkeypatch.setattr(fabric, "TemplateScaffolder", type("TemplateScaffolder", (Provider,), {}))
    monkeypatch.setattr(fabric, "HostExecutionProvider", type("HostExecutionProvider", (Provider,), {}))
    monkeypatch.setattr(fabric, "HostBridgeProvider", type("HostBridgeProvider", (Provider,), {}))
    monkeypatch.setattr(fabric, "AntigravityUIProvider", type("AntigravityUIProvider", (Provider,), {}))
    monkeypatch.setattr(fabric, "AntigravityVisionProvider", type("AntigravityVisionProvider", (Provider,), {}))
    monkeypatch.setitem(sys.modules, "src.tools.providers.uab_bridge", SimpleNamespace(UABProvider=type("UABProvider", (Provider,), {})))

    tf = ToolFabric(ToolFabricConfig(enabled=False, default_workspace="/workspace"))

    names = [provider.provider_id for provider in tf._health_monitor._providers.values()]
    assert "HostExecutionProvider" in names
    assert "HostBridgeProvider" in names
    assert "AntigravityUIProvider" in names
    assert "AntigravityVisionProvider" in names
    assert "UABProvider" in names


def test_register_unregister_and_router_preferences(monkeypatch):
    tf = _bare_fabric()
    provider = SimpleNamespace(provider_id="extra")

    tf.register_provider(provider)
    tf.unregister_provider("extra")

    assert tf._health_monitor.registered == [provider]
    assert tf._health_monitor.unregistered == ["extra"]

    import feature_flags

    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", True, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", True, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_UAB", True, raising=False)
    tf.update_router_preferences()

    assert (Capability.SHELL_EXEC, ["host_bridge", "host_execution", "local_sandbox"]) in tf._router.preferences
    assert (Capability.APP_CONTROL, ["uab_bridge"]) in tf._router.preferences


def test_run_command_disabled_routing_mismatch_success_and_provider_exception():
    disabled = ToolFabric.__new__(ToolFabric)
    disabled.config = ToolFabricConfig(enabled=False)
    result = disabled.run_command("echo no")
    assert result.exit_code == 1
    assert result.stderr == "Tool Fabric is disabled"

    routing_failure = _bare_fabric(
        route=RouteDecision(None, Capability.SHELL_EXEC, False, "no route"),
    )
    result = routing_failure.run_command("echo no")
    assert result.exit_code == 126
    assert "Routing failed: no route" in result.stderr
    assert routing_failure.stored[-1].success is False

    mismatch = _bare_fabric(provider=object())
    result = mismatch.run_command(["echo", "no"])
    assert result.exit_code == 127
    assert "does not support ShellExec" in result.stderr

    shell = _ShellProvider()
    success = _bare_fabric(
        provider=shell,
        route=RouteDecision("shell", Capability.SHELL_EXEC, True, "ok", policy_decision={"allowed": True}),
    )
    result = success.run_command("echo ok", env={"A": "B"}, timeout_s=7, network=True, session_id="s1")
    assert result.success is True
    assert shell.calls[0]["env"] == {"A": "B"}
    assert success.stored[-1].provider_id == "shell"

    exploding = _bare_fabric(provider=_ShellProvider(raises=True))
    result = exploding.run_command("echo fail")
    assert result.exit_code == 1
    assert "provider exploded" in result.stderr


def test_repo_and_file_operations_cover_success_policy_and_missing_provider_paths():
    no_provider = _bare_fabric(provider=None, route=RouteDecision(None, Capability.REPO_OPS, False, "none"))
    assert no_provider.git_status() == {"error": "No RepoOps provider available"}
    assert no_provider.git_diff() == "Error: No RepoOps provider available"
    assert no_provider.git_apply_patch("patch").success is False
    assert no_provider.git_commit("msg") == "Error: No RepoOps provider available"
    assert no_provider.read_file("a.txt") == "Error: No FileOps provider available"
    assert no_provider.write_file("a.txt", "x").action == "error"
    assert no_provider.list_files(".") == ["Error: No FileOps provider available"]

    repo = _bare_fabric(provider=_RepoProvider(), route=RouteDecision("repo", Capability.REPO_OPS, True, "ok"))
    assert repo.git_status("/repo") == {"workspace": "/repo"}
    assert repo.git_diff("/repo", "HEAD") == "diff:/repo:HEAD"
    assert repo.git_apply_patch("patch", "/repo").success is True
    assert repo.git_commit("msg", "/repo", ["a.txt"]) == "commit:msg:['a.txt']"

    denied = _bare_fabric(provider=_FileProvider(), route=RouteDecision("files", Capability.FILE_OPS, True, "ok"), path_allowed=False)
    assert denied.read_file("../secret") == "Error: blocked path"
    assert denied.write_file("../secret", "x").action == "error"

    files = _bare_fabric(provider=_FileProvider(), route=RouteDecision("files", Capability.FILE_OPS, True, "ok"))
    assert files.read_file("a.txt") == "read:a.txt"
    assert files.write_file("a.txt", "hello").size_after == 5
    assert files.list_files(".", recursive=True) == [".:True"]


def test_health_probe_safe_mode_and_availability_paths():
    tf = _bare_fabric(route=RouteDecision("provider", Capability.SHELL_EXEC, True, "ok"))

    assert tf.get_health() == {"provider": "healthy"}
    assert tf.probe_health("provider") == {"provider": "specific-health"}
    assert tf.probe_health() == {"all": "healthy"}
    assert tf.get_routing_summary()["preferences"] == []
    assert tf.is_available(Capability.SHELL_EXEC) is True

    tf.enable_safe_mode()
    assert tf.config.safe_mode is True
    assert (Capability.UI_BUILDER, ["ui_templates"]) in tf._router.preferences

    tf.disable_safe_mode()
    assert tf.config.safe_mode is False
    assert (Capability.UI_BUILDER, ["ui_templates", "ui_antigravity"]) in tf._router.preferences
