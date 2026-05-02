import types

from src.core.outbound_http import OutboundNetworkError
from src.ui.onboarding_provider_validation import validate_api_key_live


class _Response:
    def __init__(self, status_code=200, ok=True):
        self.status_code = status_code
        self.ok = ok


def test_validate_gemini_success_invalid_unexpected_and_network_error(monkeypatch):
    calls = []
    responses = iter([
        _Response(),
        _Response(status_code=403, ok=False),
        _Response(status_code=500, ok=False),
    ])

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setitem(__import__("sys").modules, "requests", types.SimpleNamespace(get=fake_get))

    assert validate_api_key_live("gemini", "key", url_validator=lambda url, **_kwargs: url) == {"valid": True}
    assert validate_api_key_live("gemini", "key", url_validator=lambda url, **_kwargs: url)["error"] == "Invalid API key - rejected by Google"
    assert "HTTP 500" in validate_api_key_live("gemini", "key", url_validator=lambda url, **_kwargs: url)["error"]
    assert "key=key" in calls[0][0]

    result = validate_api_key_live(
        "gemini",
        "key",
        url_validator=lambda *_args, **_kwargs: (_ for _ in ()).throw(OutboundNetworkError("blocked")),
    )
    assert result == {"valid": False, "error": "blocked"}


def test_validate_openai_xai_and_nvidia_status_mapping(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers, timeout))
        if "openai" in url:
            return _Response(status_code=401, ok=False)
        if "x.ai" in url:
            return _Response(status_code=429, ok=False)
        return _Response(ok=True)

    monkeypatch.setitem(__import__("sys").modules, "requests", types.SimpleNamespace(get=fake_get))

    assert validate_api_key_live("openai", "openai-key", url_validator=lambda url, **_kwargs: url) == {
        "valid": False,
        "error": "Invalid API key - rejected by OpenAI",
    }
    assert validate_api_key_live("xai", "xai-key", url_validator=lambda url, **_kwargs: url) == {
        "valid": False,
        "error": "Unexpected response (HTTP 429)",
    }
    assert validate_api_key_live("nvidia", "nvidia-key", url_validator=lambda url, **_kwargs: url) == {
        "valid": True,
    }
    assert calls[0][1] == {"Authorization": "Bearer openai-key"}


def test_validate_anthropic_post_payload_and_invalid_key(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json, timeout))
        return _Response(status_code=401, ok=False)

    monkeypatch.setitem(__import__("sys").modules, "requests", types.SimpleNamespace(post=fake_post))

    result = validate_api_key_live("anthropic", "anthropic-key", url_validator=lambda url, **_kwargs: url)

    assert result == {"valid": False, "error": "Invalid API key - rejected by Anthropic"}
    assert calls[0][1]["x-api-key"] == "anthropic-key"
    assert calls[0][2]["messages"] == [{"role": "user", "content": "hi"}]


def test_validate_unknown_provider_and_unreachable_api_warning(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise RuntimeError("temporary outage")

    monkeypatch.setitem(__import__("sys").modules, "requests", types.SimpleNamespace(get=fake_get))

    assert validate_api_key_live("unknown", "key") == {
        "valid": False,
        "error": "Unknown provider: unknown",
    }
    result = validate_api_key_live("openai", "key", url_validator=lambda url, **_kwargs: url)
    assert result["valid"] is True
    assert "Could not reach openai API" in result["warning"]


def test_remaining_provider_status_branches(monkeypatch):
    statuses = {
        "openai": [_Response(ok=True), _Response(status_code=503, ok=False)],
        "anthropic": [_Response(status_code=200, ok=True)],
        "xai": [_Response(ok=True), _Response(status_code=401, ok=False)],
        "nvidia": [_Response(status_code=401, ok=False), _Response(status_code=503, ok=False)],
    }

    def fake_get(url, **_kwargs):
        if "openai" in url:
            return statuses["openai"].pop(0)
        if "x.ai" in url:
            return statuses["xai"].pop(0)
        return statuses["nvidia"].pop(0)

    def fake_post(*_args, **_kwargs):
        return statuses["anthropic"].pop(0)

    monkeypatch.setitem(__import__("sys").modules, "requests", types.SimpleNamespace(get=fake_get, post=fake_post))

    assert validate_api_key_live("openai", "key", url_validator=lambda url, **_kwargs: url) == {"valid": True}
    assert validate_api_key_live("openai", "key", url_validator=lambda url, **_kwargs: url)["error"] == "Unexpected response (HTTP 503)"
    assert validate_api_key_live("anthropic", "key", url_validator=lambda url, **_kwargs: url) == {"valid": True}
    assert validate_api_key_live("xai", "key", url_validator=lambda url, **_kwargs: url) == {"valid": True}
    assert validate_api_key_live("xai", "key", url_validator=lambda url, **_kwargs: url)["error"] == "Invalid API key - rejected by xAI"
    assert validate_api_key_live("nvidia", "key", url_validator=lambda url, **_kwargs: url)["error"] == "Invalid API key - rejected by NVIDIA"
    assert validate_api_key_live("nvidia", "key", url_validator=lambda url, **_kwargs: url)["error"] == "Unexpected response (HTTP 503)"
