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

    assert client.validate_model("gpt-5.1-codex") is True
    assert client.validate_model("gpt-5.4") is False
