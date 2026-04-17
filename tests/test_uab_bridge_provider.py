from src.tools.providers.uab_bridge import UABProvider


def test_get_daemon_status_normalizes_transport_and_connections():
    provider = UABProvider()
    provider._rpc_call = lambda method, params=None, timeout=None: {
        "version": "1.3.0",
        "connectedApps": 2,
        "supportedFrameworks": ["browser", "electron"],
        "transport": "json-rpc-compat",
        "standaloneFeatures": ["focused", "findByPath"],
        "connections": [
            {
                "pid": 101,
                "name": "Notepad",
                "framework": "winui",
                "method": "accessibility",
                "elementCount": 14,
                "windowTitle": "notes.txt",
            }
        ],
    }

    status = provider.get_daemon_status()

    assert status["version"] == "1.3.0"
    assert status["transport"] == "json-rpc-compat"
    assert status["standalone_features"] == ["focused", "findByPath"]
    assert status["connections"][0]["connection_method"] == "accessibility"
    assert status["connections"][0]["window_title"] == "notes.txt"


def test_health_check_exposes_standalone_status_metadata():
    provider = UABProvider()
    provider._rpc_call = lambda method, params=None, timeout=None: {
        "version": "1.3.0",
        "connectedApps": 1,
        "supportedFrameworks": ["browser"],
        "transport": "json-rpc-compat",
        "standaloneFeatures": ["smartInvoke"],
    }

    health = provider.health_check()

    assert health.is_healthy is True
    assert health.version == "1.3.0"
    assert health.metadata["transport"] == "json-rpc-compat"
    assert health.metadata["standalone_features"] == ["smartInvoke"]


def test_find_by_path_and_watch_changes_extract_nested_results():
    provider = UABProvider()

    def fake_rpc(method, params=None, timeout=None):
        if method == "findByPath":
            return {"pid": 7, "count": 1, "elements": [{"name": "Save", "automationId": "save"}]}
        if method == "watchChanges":
            return {"pid": 7, "eventCount": 1, "events": [{"type": "focus"}]}
        raise AssertionError(f"Unexpected method: {method}")

    provider._rpc_call = fake_rpc

    assert provider.find_by_path(7, name="Save") == [{"name": "Save", "automationId": "save"}]
    assert provider.watch_changes(7) == [{"type": "focus"}]
