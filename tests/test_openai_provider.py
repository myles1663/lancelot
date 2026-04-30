import sys
from types import SimpleNamespace

from src.core.providers.openai_client import OpenAIProviderClient


def test_codex_init_uses_chatgpt_backend(monkeypatch):
    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))

    OpenAIProviderClient(auth_token="codex-token")

    assert captured["api_key"] == "codex-token"
    assert captured["base_url"] == OpenAIProviderClient.CODEX_BASE_URL


def test_codex_list_models_skips_remote_models_api():
    client = OpenAIProviderClient.__new__(OpenAIProviderClient)
    client._is_codex = True
    client._client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: (_ for _ in ()).throw(AssertionError("remote models API should not be called"))
        )
    )

    models = client.list_models()

    assert [m.id for m in models] == [m.id for m in OpenAIProviderClient._CODEX_MODELS]


def test_codex_validate_model_uses_local_model_set():
    client = OpenAIProviderClient.__new__(OpenAIProviderClient)
    client._is_codex = True

    assert client.validate_model("gpt-5.4") is True
    assert client.validate_model("gpt-5.4-mini") is True
    assert client.validate_model("gpt-5.4-nano") is True
    assert client.validate_model("gpt-5.1-codex") is False


def test_codex_factory_uses_responses_provider_for_chatgpt_subscription_auth(monkeypatch):
    captured = {}

    class _FakeResponses:
        def create(self, **kwargs):
            return []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.responses = _FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))

    from providers.factory import create_provider

    client = create_provider("openai-codex", "", auth_token="codex-token")

    assert client.provider_name == "openai-codex"
    assert client.__class__.__name__ == "OpenAICodexResponsesProviderClient"
    assert captured["api_key"] == "codex-token"
    assert captured["base_url"] == "https://chatgpt.com/backend-api/codex"


def test_codex_factory_keeps_cli_fallback_when_only_cli_auth_exists(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    auth_file = codex_home / ".codex" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("providers.codex_cli_client.shutil.which", lambda name: "/usr/bin/codex")

    from providers.factory import create_provider

    client = create_provider("openai-codex", "", codex_home=str(codex_home))

    assert client.provider_name == "openai-codex"
    assert client.__class__.__name__ == "CodexCLIProviderClient"
