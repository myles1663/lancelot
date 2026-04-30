import pytest

from src.core import flags_api


@pytest.mark.asyncio
async def test_get_uab_status_exposes_transport_and_features(monkeypatch):
    monkeypatch.setattr(
        flags_api,
        "_uab_rpc",
        lambda method, params=None, timeout=3: {
            "version": "1.3.0",
            "connectedApps": 2,
            "supportedFrameworks": ["browser", "electron"],
            "transport": "json-rpc-compat",
            "standaloneFeatures": ["focused", "watchChanges"],
            "connections": [{"pid": 123, "name": "Chrome", "framework": "browser", "method": "uab-hook"}],
        },
    )

    result = await flags_api.get_uab_status()

    assert result["reachable"] is True
    assert result["transport"] == "json-rpc-compat"
    assert result["standalone_features"] == ["focused", "watchChanges"]
    assert result["connections"][0]["method"] == "uab-hook"


@pytest.mark.asyncio
async def test_get_uab_connected_apps_uses_status_connections(monkeypatch):
    monkeypatch.setattr(
        flags_api,
        "_uab_rpc",
        lambda method, params=None, timeout=3: {
            "connections": [
                {
                    "pid": 456,
                    "name": "Code",
                    "framework": "electron",
                    "method": "uab-hook",
                    "windowTitle": "lancelot",
                    "elementCount": 88,
                }
            ]
        },
    )

    result = await flags_api.get_uab_connected_apps()

    assert result == {
        "apps": [
            {
                "pid": 456,
                "name": "Code",
                "framework": "electron",
                "connectionMethod": "uab-hook",
                "windowTitle": "lancelot",
                "elementCount": 88,
            }
        ]
    }
