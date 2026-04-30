from types import SimpleNamespace

import pytest

from src.core import tools_api


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
