import importlib
import sys
import types

import pytest


def test_list_models_import_has_no_gemini_side_effects(monkeypatch):
    calls = []
    fake_genai = types.SimpleNamespace(
        configure=lambda **_kwargs: calls.append("configure"),
        list_models=lambda: calls.append("list_models"),
    )

    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    sys.modules.pop("src.shared.list_models", None)

    module = importlib.import_module("src.shared.list_models")

    assert hasattr(module, "list_gemini_models")
    assert calls == []


def test_list_gemini_models_requires_api_key(monkeypatch):
    module = importlib.import_module("src.shared.list_models")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        module.list_gemini_models(genai_module=types.SimpleNamespace())


def test_list_gemini_models_filters_to_generate_content_support():
    module = importlib.import_module("src.shared.list_models")
    configured = []
    fake_genai = types.SimpleNamespace(
        configure=lambda **kwargs: configured.append(kwargs["api_key"]),
        list_models=lambda: [
            types.SimpleNamespace(
                name="models/gemini-1.5-pro",
                supported_generation_methods=["generateContent", "countTokens"],
            ),
            types.SimpleNamespace(
                name="models/embedding-001",
                supported_generation_methods=["embedContent"],
            ),
            types.SimpleNamespace(
                name="models/gemini-2.0-flash",
                supported_generation_methods=["generateContent"],
            ),
        ],
    )

    result = module.list_gemini_models(
        api_key="test-key",
        genai_module=fake_genai,
    )

    assert configured == ["test-key"]
    assert result == ["models/gemini-1.5-pro", "models/gemini-2.0-flash"]


def test_list_gemini_models_uses_environment_api_key(monkeypatch):
    module = importlib.import_module("src.shared.list_models")
    configured = []
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    fake_genai = types.SimpleNamespace(
        configure=lambda **kwargs: configured.append(kwargs["api_key"]),
        list_models=lambda: [],
    )

    assert module.list_gemini_models(genai_module=fake_genai) == []
    assert configured == ["env-key"]


def test_list_models_main_reports_success_and_errors(monkeypatch, capsys):
    module = importlib.import_module("src.shared.list_models")

    monkeypatch.setattr(module, "list_gemini_models", lambda: ["models/a", "models/b"])
    assert module.main() == 0
    assert capsys.readouterr().out == "models/a\nmodels/b\n"

    monkeypatch.setattr(module, "list_gemini_models", lambda: (_ for _ in ()).throw(ValueError("missing key")))
    assert module.main() == 1

    monkeypatch.setattr(module, "list_gemini_models", lambda: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert module.main() == 1
