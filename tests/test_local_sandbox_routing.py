from src.tools.contracts import Capability
from src.tools.fabric import ToolFabric, ToolFabricConfig
from src.tools.health import reset_health_monitor
from src.tools.router import reset_router
from src.tools.providers.local_sandbox import LocalSandboxProvider


def test_fabric_uses_local_sandbox_for_file_ops_when_docker_missing(tmp_path, monkeypatch):
    reset_health_monitor()
    reset_router()
    monkeypatch.setattr(LocalSandboxProvider, "_check_docker", lambda self: (False, None))

    fabric = ToolFabric(ToolFabricConfig(enabled=True, default_workspace=str(tmp_path)))

    target = tmp_path / "note.txt"
    change = fabric.write_file(str(target), "hello")
    content = fabric.read_file(str(target))

    assert change.action == "created"
    assert content == "hello"


def test_router_skips_local_sandbox_shell_exec_when_docker_missing(tmp_path, monkeypatch):
    reset_health_monitor()
    reset_router()
    monkeypatch.setattr(LocalSandboxProvider, "_check_docker", lambda self: (False, None))

    fabric = ToolFabric(ToolFabricConfig(enabled=True, default_workspace=str(tmp_path)))
    decision = fabric._router.select_provider(
        capability=Capability.SHELL_EXEC,
        workspace=str(tmp_path),
    )

    assert decision.success is False
    assert "capability_unavailable" in decision.alternatives_tried[0]
