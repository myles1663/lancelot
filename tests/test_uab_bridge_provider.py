import logging
import urllib.error

import pytest

from src.core.execution_authority import create_uab_authority_grant
from src.tools.contracts import Capability, ProviderState, RiskLevel
from src.tools.providers.uab_bridge import UABConfig, UABProvider, classify_action_risk

TEST_GRANT_SECRET = "uab-provider-test-secret"


def _uab_risk_for_action(action: str, app_name: str = "Notepad") -> str:
    risk = classify_action_risk(action, app_name)
    if risk is RiskLevel.LOW:
        return "safe"
    if risk is RiskLevel.MEDIUM:
        return "moderate"
    return "destructive"


def _grant(action: str, *, pid: int = 7, app_name: str = "Notepad", selector_scope: str = ""):
    uab_risk = _uab_risk_for_action(action, app_name)
    return create_uab_authority_grant(
        secret=TEST_GRANT_SECRET,
        risk_tier="T2_CONTROLLED" if uab_risk != "destructive" else "T3_IRREVERSIBLE",
        uab_risk=uab_risk,
        capability=f"uab_{action}",
        app_name=app_name,
        app_pid=pid,
        action=action,
        selector_scope=selector_scope,
        policy_version="test-policy",
        soul_version="v1",
        mutating=uab_risk != "safe",
        destructive=uab_risk == "destructive",
        sensitive_read=action in {
            "screenshot",
            "readDocument",
            "readCell",
            "readRange",
            "readFormula",
            "readSlides",
            "readSlideText",
            "readEmails",
            "getCookies",
            "getLocalStorage",
            "getSessionStorage",
        },
        external_submission=action in {"sendEmail", "submitForm", "upload"},
        credential_sensitive=action in {
            "getCookies",
            "setCookie",
            "deleteCookie",
            "clearCookies",
            "getLocalStorage",
            "setLocalStorage",
            "deleteLocalStorage",
            "clearLocalStorage",
            "getSessionStorage",
            "setSessionStorage",
            "deleteSessionStorage",
            "clearSessionStorage",
            "executeScript",
        },
    ).to_dict()


class _UrlopenResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


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


def test_unknown_uab_action_risk_fails_closed():
    assert classify_action_risk("unknownGovernedAction") is RiskLevel.HIGH


def test_provider_uab_risk_label_uses_locked_terminology():
    assert UABProvider._uab_risk_label(RiskLevel.LOW) == "safe"
    assert UABProvider._uab_risk_label(RiskLevel.MEDIUM) == "moderate"
    assert UABProvider._uab_risk_label(RiskLevel.HIGH) == "destructive"

    with pytest.raises(ValueError, match="Unknown Tool Fabric risk label"):
        UABProvider._uab_risk_label("critical")


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


def test_rpc_call_logs_http_error_body_decode_failure(monkeypatch, caplog):
    provider = UABProvider()

    class BrokenHTTPError(urllib.error.HTTPError):
        def read(self):
            raise ValueError("boom")

    def fake_urlopen(*args, **kwargs):
        raise BrokenHTTPError(
            url="http://host.docker.internal:7900",
            code=503,
            msg="unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ConnectionError, match="UAB daemon returned HTTP 503"):
            provider._rpc_call("getStatus")

    assert "UAB daemon HTTP response error body could not be decoded" in caplog.text


def test_classify_action_risk_covers_destructive_sensitive_mutating_and_read_only_paths():
    assert classify_action_risk("close") == RiskLevel.HIGH
    assert classify_action_risk("move") == RiskLevel.HIGH
    assert classify_action_risk("deleteCookie") == RiskLevel.HIGH
    assert classify_action_risk("click", app_name="1Password") == RiskLevel.HIGH
    assert classify_action_risk("state", app_name="Outlook") == RiskLevel.MEDIUM
    assert classify_action_risk("click", app_name="Notepad") == RiskLevel.MEDIUM
    assert classify_action_risk("executeScript", app_name="Notepad") == RiskLevel.MEDIUM
    assert classify_action_risk("state", app_name="Notepad") == RiskLevel.LOW
    assert classify_action_risk("getTabs", app_name="Notepad") == RiskLevel.LOW


def test_provider_denies_mutating_action_without_grant_before_rpc():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

    result = provider.act(7, "edit1", "type", {"text": "hello"})

    assert result.success is False
    assert result.error_message == "UAB authority grant required for provider action 'type'"
    assert calls == []
    assert provider.get_denial_events()[0]["reason_code"] == "missing_authority_grant"
    assert provider.get_denial_events()[0]["action"] == "type"


def test_provider_denies_invalid_grant_before_rpc():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))
    tampered = _grant("type", selector_scope="edit1")
    tampered["action"] = "click"

    result = provider.act(7, "edit1", "type", {"uabAuthorityGrant": tampered})

    assert result.success is False
    assert "UAB authority grant rejected" in (result.error_message or "")
    assert calls == []
    assert provider.get_denial_events()[0]["reason_code"] == "invalid_authority_grant"


def test_provider_denies_replayed_grant_before_rpc():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []

    def fake_rpc(method, params=None, timeout=None):
        calls.append((method, params))
        return {"success": True, "durationMs": 1, "result": {"ok": True}}

    provider._rpc_call = fake_rpc
    grant = _grant("type", selector_scope="edit1")

    first = provider.act(7, "edit1", "type", {"uabAuthorityGrant": grant})
    second = provider.act(7, "edit1", "type", {"uabAuthorityGrant": grant})

    assert first.success is True
    assert second.success is False
    assert second.error_message == "UAB authority grant rejected: replayed nonce"
    assert calls == [
        (
            "act",
            {
                "pid": 7,
                "elementId": "edit1",
                "action": "type",
                "params": {"uabAuthorityGrant": grant},
            },
        )
    ]
    assert provider.get_denial_events()[0]["reason_code"] == "replayed_authority_grant"


def test_provider_denies_classification_required_read_without_app_context_or_grant():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

    result = provider.get_tabs(7)

    assert result.success is False
    assert result.error_message == "UAB provider classification required for action 'getTabs'"
    assert calls == []
    assert provider.get_denial_events()[0]["reason_code"] == "classification_required"


def test_provider_allows_classified_non_sensitive_read_without_grant():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []

    def fake_rpc(method, params=None, timeout=None):
        calls.append((method, params))
        return {"success": True, "durationMs": 1, "result": {"tabs": []}}

    provider._rpc_call = fake_rpc

    result = provider.get_tabs(7)

    assert result.success is True
    assert calls == [("act", {"pid": 7, "elementId": "", "action": "getTabs", "params": {}})]
    assert provider.get_denial_events() == []


def test_provider_denies_sensitive_read_without_grant():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

    result = provider.screenshot(7)

    assert result.success is False
    assert result.error_message == "UAB authority grant required for provider action 'screenshot'"
    assert calls == []
    assert provider.get_denial_events()[0]["action"] == "screenshot"


def test_provider_denies_sensitive_app_direct_read_helpers_before_rpc():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Outlook"}
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

    state = provider.state(7)
    elements = provider.enumerate(7)
    queried = provider.query(7, {"type": "edit"})

    assert state.pid == 7
    assert elements == []
    assert queried == []
    assert calls == []
    assert [event["action"] for event in provider.get_denial_events()] == [
        "state",
        "enumerate",
        "query",
    ]
    assert all(
        event["reason_code"] == "missing_authority_grant"
        for event in provider.get_denial_events()
    )


def test_provider_allows_safe_direct_read_helpers_without_grant():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []

    def fake_rpc(method, params=None, timeout=None):
        calls.append((method, params))
        if method == "state":
            return {"window": {"title": "Notepad", "focused": True}}
        if method == "enumerate":
            return [{"id": "root", "type": "window"}]
        if method == "query":
            return [{"id": "edit1", "type": "edit"}]
        raise AssertionError(method)

    provider._rpc_call = fake_rpc

    assert provider.state(7).window_title == "Notepad"
    assert provider.enumerate(7)[0].id == "root"
    assert provider.query(7, {"type": "edit"})[0].id == "edit1"
    assert [call[0] for call in calls] == ["state", "enumerate", "query"]
    assert provider.get_denial_events() == []


def test_provider_denies_dict_helper_paths_without_grant_before_rpc():
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    calls = []
    provider._rpc_call = lambda method, params=None, timeout=None: calls.append((method, params))

    chain = provider.execute_chain({"pid": 7, "steps": []})
    atomic = provider.atomic_chain(7, [{"method": "click"}])
    smart = provider.smart_invoke(7, "Save")

    assert chain["success"] is False
    assert atomic["success"] is False
    assert smart["success"] is False
    assert calls == []
    assert [event["action"] for event in provider.get_denial_events()] == [
        "chain",
        "atomicChain",
        "smartInvoke",
    ]


def test_config_uses_env_default_and_provider_metadata():
    provider = UABProvider(config=UABConfig(daemon_url="http://uab.test"))

    assert provider.provider_id == "uab_bridge"
    assert provider.capabilities == [Capability.APP_CONTROL]
    assert provider.config.daemon_url == "http://uab.test"


def test_normalizers_handle_non_dict_values():
    provider = UABProvider()

    assert provider._normalize_connection("bad") == {}
    assert provider._normalize_status("bad") == {
        "version": "unknown",
        "connected_apps": 0,
        "supported_frameworks": [],
        "transport": "json-rpc",
        "standalone_features": [],
        "connections": [],
    }


def test_rpc_call_returns_result_and_translates_rpc_errors(monkeypatch):
    provider = UABProvider(config=UABConfig(daemon_url="http://uab.test"))

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _UrlopenResponse(b'{"result": {"status": "ok"}}'),
    )
    assert provider._rpc_call("health") == {"status": "ok"}

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _UrlopenResponse(
            b'{"error": {"code": 7, "message": "denied"}}'
        ),
    )
    with pytest.raises(RuntimeError, match="UAB RPC error 7: denied"):
        provider._rpc_call("health")


def test_rpc_call_translates_url_error_and_invalid_json(monkeypatch):
    provider = UABProvider(config=UABConfig(daemon_url="http://uab.test"))

    def raise_url_error(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    with pytest.raises(ConnectionError, match="Cannot reach UAB daemon"):
        provider._rpc_call("health")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _UrlopenResponse(b"{not-json"),
    )
    with pytest.raises(ConnectionError, match="invalid JSON"):
        provider._rpc_call("health")


def test_health_check_returns_offline_metadata_on_exception():
    provider = UABProvider()
    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))

    health = provider.health_check()

    assert health.state == ProviderState.OFFLINE
    assert "offline" in (health.error_message or "")
    assert health.metadata["mode"] == "uab_bridge"


def test_get_daemon_status_returns_defaults_on_failure(caplog):
    provider = UABProvider()
    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("status boom"))

    with caplog.at_level(logging.WARNING):
        status = provider.get_daemon_status()

    assert status["version"] == "unknown"
    assert "UAB status query failed" in caplog.text


def test_detect_connect_disconnect_and_get_app_name_cover_success_and_failure(caplog):
    provider = UABProvider()
    calls = []

    def fake_rpc(method, params=None, timeout=None):
        calls.append((method, params))
        if method == "detect":
            return [
                {
                    "pid": 101,
                    "name": "Notepad",
                    "path": "C:/Windows/notepad.exe",
                    "framework": "winui",
                    "confidence": 0.95,
                    "windowTitle": "notes.txt",
                    "connectionInfo": {"kind": "uia"},
                }
            ]
        if method == "connect":
            if "pid" in params:
                return {
                    "success": True,
                    "pid": params["pid"],
                    "name": "Notepad",
                    "framework": "winui",
                    "connectionMethod": "uia",
                }
            return "bad"
        if method == "disconnect":
            return {"success": True}
        raise AssertionError(method)

    provider._rpc_call = fake_rpc

    detected = provider.detect()
    assert detected[0].pid == 101
    assert detected[0].window_title == "notes.txt"

    connected = provider.connect(101)
    assert connected.success is True
    assert provider.get_app_name(101) == "Notepad"

    invalid = provider.connect("Notepad")
    assert invalid.success is False
    assert invalid.error_message == "Invalid response"

    assert provider.disconnect(101) is True
    assert provider.get_app_name(101) == "unknown"

    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("detect boom"))
    with caplog.at_level(logging.WARNING):
        assert provider.detect() == []
        assert provider.connect(202).success is False
        assert provider.disconnect(202) is False

    assert "UAB detect failed" in caplog.text
    assert "UAB connect failed" in caplog.text
    assert "UAB disconnect failed" in caplog.text

    provider._rpc_call = lambda *args, **kwargs: "bad"
    assert provider.detect() == []


def test_enumerate_query_and_state_parse_results_and_fallbacks(caplog):
    provider = UABProvider(config=UABConfig(max_elements=1, max_element_depth=1))

    def fake_rpc(method, params=None, timeout=None):
        if method == "enumerate":
            return [
                {
                    "id": "root",
                    "type": "window",
                    "children": [{"id": "child", "type": "button"}],
                },
                {"id": "overflow", "type": "button"},
            ]
        if method == "query":
            return [{"id": "match", "type": "edit"}]
        if method == "state":
            return {
                "window": {
                    "title": "Notepad",
                    "size": {"width": 800, "height": 600},
                    "position": {"x": 10, "y": 20},
                    "focused": True,
                },
                "activeElement": "edit1",
                "modals": ["save"],
                "menus": ["file"],
                "clipboard": "hello",
            }
        raise AssertionError(method)

    provider._rpc_call = fake_rpc

    elements = provider.enumerate(7)
    assert len(elements) == 1
    assert elements[0].id == "root"
    assert elements[0].children[0].id == "child"

    queried = provider.query(7, {"type": "edit"})
    assert queried[0].id == "match"

    state = provider.state(7)
    assert state.window_title == "Notepad"
    assert state.focused is True
    assert state.active_element == "edit1"

    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("lookup boom"))
    with caplog.at_level(logging.WARNING):
        assert provider.enumerate(7) == []
        assert provider.query(7, {"type": "edit"}) == []
        fallback_state = provider.state(7)

    assert fallback_state.pid == 7
    assert "UAB enumerate failed" in caplog.text
    assert "UAB query failed" in caplog.text
    assert "UAB state failed" in caplog.text

    provider._rpc_call = lambda method, params=None, timeout=None: "bad"
    assert provider.enumerate(7) == []
    assert provider.query(7, {"type": "edit"}) == []
    assert provider.state(7).pid == 7


def test_act_keypress_hotkey_window_and_screenshot_cover_success_invalid_and_failure(caplog):
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}
    responses = {
        "act": {"success": True, "stateChanges": ["typed"], "durationMs": 9, "result": {"ok": True}},
        "keypress": {"success": True, "durationMs": 3, "result": {"key": "Enter"}},
        "hotkey": {"success": True, "durationMs": 4, "result": {"keys": ["ctrl", "s"]}},
        "maximize": {"success": True, "durationMs": 5, "result": {"window": "maximized"}},
        "screenshot": {"success": True, "durationMs": 6, "result": {"path": "shot.png"}},
    }
    recorded = []

    def fake_rpc(method, params=None, timeout=None):
        recorded.append((method, params))
        return responses[method]

    provider._rpc_call = fake_rpc

    act_result = provider.act(
        7,
        "edit1",
        "type",
        {"text": "hello", "uabAuthorityGrant": _grant("type", selector_scope="edit1")},
    )
    assert act_result.success is True
    assert act_result.result_data == {"ok": True}

    assert provider.keypress(7, "Enter", uab_authority_grant=_grant("keypress")).success is True
    assert provider.hotkey(7, ["ctrl", "s"], uab_authority_grant=_grant("hotkey")).success is True
    assert provider.maximize(7, uab_authority_grant=_grant("maximize")).success is True
    assert provider.screenshot(
        7,
        output_path="shot.png",
        uab_authority_grant=_grant("screenshot"),
    ).success is True
    assert recorded[0][1]["params"]["uabAuthorityGrant"]["action"] == "type"
    assert recorded[1][1]["uabAuthorityGrant"]["action"] == "keypress"
    assert recorded[2][1]["uabAuthorityGrant"]["action"] == "hotkey"
    assert recorded[3][1]["uabAuthorityGrant"]["action"] == "maximize"
    assert recorded[4][1]["uabAuthorityGrant"]["action"] == "screenshot"
    assert recorded[4][1]["outputPath"] == "shot.png"

    provider._rpc_call = lambda *args, **kwargs: "bad"
    assert provider.act(
        7,
        "edit1",
        "type",
        {"uabAuthorityGrant": _grant("type", selector_scope="edit1")},
    ).success is False
    assert provider.keypress(7, "Enter", uab_authority_grant=_grant("keypress")).success is False
    assert provider.hotkey(7, ["ctrl", "s"], uab_authority_grant=_grant("hotkey")).success is False
    assert provider.restore(7, uab_authority_grant=_grant("restore")).success is False
    assert provider.screenshot(7, uab_authority_grant=_grant("screenshot")).success is False

    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rpc boom"))
    with caplog.at_level(logging.WARNING):
        assert provider.act(
            7,
            "edit1",
            "type",
            {"uabAuthorityGrant": _grant("type", selector_scope="edit1")},
        ).success is False
        assert provider.keypress(7, "Enter", uab_authority_grant=_grant("keypress")).success is False
        assert provider.hotkey(7, ["ctrl", "s"], uab_authority_grant=_grant("hotkey")).success is False
        assert provider.minimize(7, uab_authority_grant=_grant("minimize")).success is False
        assert provider.screenshot(7, uab_authority_grant=_grant("screenshot")).success is False
    assert "UAB act failed" in caplog.text


def test_chain_diag_and_spatial_helpers_cover_success_and_failure(caplog):
    provider = UABProvider(config=UABConfig(authority_grant_secret=TEST_GRANT_SECRET))
    provider._connected_apps[7] = {"name": "Notepad"}

    def fake_rpc(method, params=None, timeout=None):
        mapping = {
            "chain": {"success": True},
            "health": [{"pid": 7}],
            "cacheStats": {"tree": 2},
            "auditLog": [{"action": "click"}],
            "spatialMap": {"rows": []},
            "textMap": {"text": "map"},
            "findByDescription": [{"name": "Save"}],
            "focused": {"id": "edit1"},
            "findByPath": {"elements": [{"name": "Save"}]},
            "watchChanges": {"events": [{"type": "focus"}]},
            "atomicChain": {"success": True},
            "smartInvoke": {"success": True},
        }
        return mapping[method]

    provider._rpc_call = fake_rpc

    assert provider.execute_chain({
        "pid": 7,
        "steps": [],
        "uabAuthorityGrant": _grant("chain"),
    }) == {"success": True}
    assert provider.get_health_summary() == [{"pid": 7}]
    assert provider.get_cache_stats() == {"tree": 2}
    assert provider.get_audit_log() == [{"action": "click"}]
    assert provider.spatial_map(7) == {"rows": []}
    assert provider.text_map(7) == {"text": "map"}
    assert provider.find_by_description(7, "Save") == [{"name": "Save"}]
    assert provider.focused(7) == {"id": "edit1"}
    assert provider.find_by_path(7, path=["Window", "Toolbar"], name="Save", parent="toolbar", element_type="button", occurrence=2) == [{"name": "Save"}]
    assert provider.watch_changes(7) == [{"type": "focus"}]
    assert provider.atomic_chain(
        7,
        [{"method": "click"}],
        uab_authority_grant=_grant("atomicChain"),
    ) == {"success": True}
    assert provider.smart_invoke(
        7,
        "Save",
        parent="toolbar",
        element_type="button",
        occurrence=1,
        uab_authority_grant=_grant("smartInvoke", selector_scope="Save"),
    ) == {"success": True}

    provider._rpc_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("helper boom"))
    with caplog.at_level(logging.WARNING):
        assert provider.execute_chain({
            "pid": 7,
            "steps": [],
            "uabAuthorityGrant": _grant("chain"),
        })["success"] is False
        assert provider.get_health_summary() == []
        assert provider.get_cache_stats() == {}
        assert provider.get_audit_log() == []
        assert provider.spatial_map(7)["error"] == "helper boom"
        assert provider.text_map(7)["error"] == "helper boom"
        assert provider.find_by_description(7, "Save") == []
        assert provider.focused(7)["error"] == "helper boom"
        assert provider.find_by_path(7, name="Save") == []
        assert provider.watch_changes(7) == []
        assert provider.atomic_chain(
            7,
            [{"method": "click"}],
            uab_authority_grant=_grant("atomicChain"),
        )["success"] is False
        assert provider.smart_invoke(
            7,
            "Save",
            uab_authority_grant=_grant("smartInvoke", selector_scope="Save"),
        )["success"] is False

    assert "UAB chain execution failed" in caplog.text
    assert "UAB health summary failed" in caplog.text
    assert "UAB cache stats failed" in caplog.text
    assert "UAB smartInvoke failed" in caplog.text


def test_find_by_path_and_watch_changes_accept_list_results():
    provider = UABProvider()

    def fake_rpc(method, params=None, timeout=None):
        if method == "findByPath":
            return [{"name": "Save"}]
        if method == "watchChanges":
            return [{"type": "focus"}]
        raise AssertionError(method)

    provider._rpc_call = fake_rpc

    assert provider.find_by_path(7, name="Save") == [{"name": "Save"}]
    assert provider.watch_changes(7) == [{"type": "focus"}]


def test_office_and_browser_wrappers_forward_to_act():
    provider = UABProvider()
    calls = []

    def fake_act(pid, element_id, action, params=None):
        calls.append((pid, element_id, action, params))
        return {"success": True}

    provider.act = fake_act

    provider.read_document(7)
    provider.read_cell(7, 1, 2, sheet="Sheet1")
    provider.write_cell(7, 1, 2, "hello", sheet="Sheet1")
    provider.read_range(7, "A1:B2", sheet="Sheet1")
    provider.write_range(7, "A1:B2", [["1"]], sheet="Sheet1")
    provider.get_sheets(7)
    provider.read_emails(7)
    provider.compose_email(7, "to@example.com", "subject", "body", cc="cc@example.com")
    provider.send_email(7, "to@example.com", "subject", "body", cc="cc@example.com")
    provider.navigate(7, "https://example.com")
    provider.get_tabs(7)
    provider.switch_tab(7, "tab-1")
    provider.execute_script(7, "return 1")
    provider.get_cookies(7, url="https://example.com", domain="example.com")
    provider.set_cookie(7, "session", "abc", domain="example.com", url="https://example.com")
    provider.get_local_storage(7, key="theme")
    provider.set_local_storage(7, "theme", "dark")

    assert calls == [
        (7, "", "readDocument", None),
        (7, "", "readCell", {"row": 1, "col": 2, "sheet": "Sheet1"}),
        (7, "", "writeCell", {"row": 1, "col": 2, "text": "hello", "sheet": "Sheet1"}),
        (7, "", "readRange", {"cellRange": "A1:B2", "sheet": "Sheet1"}),
        (7, "", "writeRange", {"cellRange": "A1:B2", "values": [["1"]], "sheet": "Sheet1"}),
        (7, "", "getSheets", None),
        (7, "", "readEmails", None),
        (7, "", "composeEmail", {"to": "to@example.com", "subject": "subject", "body": "body", "cc": "cc@example.com"}),
        (7, "", "sendEmail", {"to": "to@example.com", "subject": "subject", "body": "body", "cc": "cc@example.com"}),
        (7, "", "navigate", {"url": "https://example.com"}),
        (7, "", "getTabs", None),
        (7, "", "switchTab", {"tabId": "tab-1"}),
        (7, "", "executeScript", {"script": "return 1"}),
        (7, "", "getCookies", {"url": "https://example.com", "domain": "example.com"}),
        (7, "", "setCookie", {"cookieName": "session", "cookieValue": "abc", "domain": "example.com", "url": "https://example.com"}),
        (7, "", "getLocalStorage", {"storageKey": "theme"}),
        (7, "", "setLocalStorage", {"storageKey": "theme", "storageValue": "dark"}),
    ]


def test_window_wrappers_delegate_to_window_action():
    provider = UABProvider()
    calls = []

    def fake_window_action(method, pid, **extra):
        calls.append((method, pid, extra))
        return {"success": True}

    provider._window_action = fake_window_action

    provider.minimize(7)
    provider.close_window(7)
    provider.move_window(7, 10, 20)
    provider.resize_window(7, 800, 600)

    assert calls == [
        ("minimize", 7, {}),
        ("closeWindow", 7, {}),
        ("moveWindow", 7, {"x": 10, "y": 20}),
        ("resizeWindow", 7, {"width": 800, "height": 600}),
    ]


def test_parse_element_and_connected_app_helpers_cover_cache_and_status_paths():
    provider = UABProvider(config=UABConfig(max_element_depth=0))
    parsed = provider._parse_element(
        {
            "id": "root",
            "type": "window",
            "label": "Main",
            "children": [{"id": "child", "type": "button"}],
            "actions": ["click"],
            "visible": False,
            "enabled": False,
            "meta": {"role": "main"},
        }
    )

    assert parsed.id == "root"
    assert parsed.children == []
    assert parsed.visible is False
    assert parsed.enabled is False

    provider._connected_apps = {
        7: {"name": "Notepad", "framework": "winui", "connection_method": "uia", "connected_at": "now"}
    }
    assert provider.get_connected_apps()[7]["name"] == "Notepad"
    assert provider.get_app_name(7) == "Notepad"

    provider._connected_apps = {}
    provider.get_daemon_status = lambda: {
        "connections": [
            {
                "pid": 7,
                "name": "Notepad",
                "framework": "winui",
                "connection_method": "uia",
                "element_count": 3,
                "window_title": "notes.txt",
            },
            {"pid": 0, "name": "skip"},
        ]
    }
    connected = provider.get_connected_apps()
    assert connected == {
        7: {
            "name": "Notepad",
            "framework": "winui",
            "connection_method": "uia",
            "element_count": 3,
            "window_title": "notes.txt",
        }
    }
    assert provider.get_app_name(999) == "unknown"
