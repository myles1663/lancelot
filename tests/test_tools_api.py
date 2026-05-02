from types import SimpleNamespace

import pytest

from src.core import tools_api


@pytest.mark.asyncio
async def test_tools_api_disabled_and_safe_error_paths(monkeypatch):
    monkeypatch.setattr(tools_api, "_fabric", None)

    assert await tools_api.tools_health() == {
        "providers": {},
        "summary": {"total_providers": 0},
        "enabled": False,
    }
    assert await tools_api.tools_routing() == {"capabilities": {}, "enabled": False}
    assert await tools_api.tools_config() == {"enabled": False, "safe_mode": False, "receipts": False}

    class BrokenFabric:
        config = SimpleNamespace(enabled=True, safe_mode=True, emit_receipts=True)

        def probe_health(self):
            raise RuntimeError("probe failed")

        def get_routing_summary(self):
            raise RuntimeError("routing failed")

    monkeypatch.setattr(tools_api, "_fabric", BrokenFabric())
    assert (await tools_api.tools_health()).status_code == 500
    assert (await tools_api.tools_routing()).status_code == 500

    monkeypatch.setattr(
        tools_api,
        "_fabric",
        SimpleNamespace(config=SimpleNamespace(enabled=True, safe_mode=True, emit_receipts=False)),
    )
    assert await tools_api.tools_config() == {"enabled": True, "safe_mode": True, "receipts": False}


def test_tools_api_init_respects_feature_flag_and_import_failures(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "feature_flags",
        SimpleNamespace(FEATURE_TOOLS_FABRIC=False),
    )
    monkeypatch.setattr(tools_api, "_fabric", None)
    tools_api.init_tools_api()
    assert tools_api._fabric is None

    fabric = object()
    monkeypatch.setitem(
        __import__("sys").modules,
        "feature_flags",
        SimpleNamespace(FEATURE_TOOLS_FABRIC=True),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.fabric",
        SimpleNamespace(get_tool_fabric=lambda: fabric),
    )
    tools_api.init_tools_api()
    assert tools_api._fabric is fabric

    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.fabric",
        SimpleNamespace(get_tool_fabric=lambda: (_ for _ in ()).throw(RuntimeError("down"))),
    )
    tools_api.init_tools_api()


@pytest.mark.asyncio
async def test_tools_health_reprobes_provider_state(monkeypatch):
    calls = {"probe": 0, "get": 0}

    class DummyFabric:
        def probe_health(self):
            calls["probe"] += 1
            return {
                "local_sandbox": SimpleNamespace(
                    state=SimpleNamespace(value="healthy"),
                    error_message=None,
                )
            }

        def get_health(self):
            calls["get"] += 1
            return {}

    monkeypatch.setattr(tools_api, "_fabric", DummyFabric())

    result = await tools_api.tools_health()

    assert calls["probe"] == 1
    assert calls["get"] == 0
    assert result["providers"]["local_sandbox"]["state"] == "healthy"
    assert result["summary"]["healthy"] == 1


@pytest.mark.asyncio
async def test_tools_routing_and_health_summarize_multiple_provider_states(monkeypatch):
    class DummyFabric:
        config = SimpleNamespace(enabled=True, safe_mode=False, emit_receipts=True)

        def probe_health(self):
            return {
                "healthy": SimpleNamespace(state=SimpleNamespace(value="healthy"), error_message=None),
                "degraded": SimpleNamespace(state=SimpleNamespace(value="degraded"), error_message="slow"),
                "offline": SimpleNamespace(state=SimpleNamespace(value="offline"), error_message="down"),
            }

        def get_routing_summary(self):
            return {"command.run": ["local_sandbox"]}

    monkeypatch.setattr(tools_api, "_fabric", DummyFabric())

    health = await tools_api.tools_health()
    routing = await tools_api.tools_routing()

    assert health["summary"] == {
        "total_providers": 3,
        "healthy": 1,
        "degraded": 1,
        "offline": 1,
    }
    assert health["providers"]["offline"]["error"] == "down"
    assert routing == {"routing": {"command.run": ["local_sandbox"]}, "enabled": True}
