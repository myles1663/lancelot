from __future__ import annotations

import logging as _logging
import os
import re
from typing import Any

_gov_logger = _logging.getLogger("orchestrator")


def _clear_deep_model_validation_cache(runtime: Any) -> None:
    runtime.invalidate_deep_model_validation_cache()


def _apply_provider_profile(runtime: Any, provider_name: str, *, context: str) -> None:
    try:
        from provider_profile import ProfileRegistry
        registry = ProfileRegistry()
        if registry.has_provider(provider_name):
            profile = registry.get_profile(provider_name)
            runtime.set_provider_lane_configuration(
                fast_model=profile.fast.model,
                deep_model=profile.deep.model,
                cache_model=profile.cache.model if profile.cache else profile.fast.model,
                deep_thinking_config=profile.deep.thinking,
            )
    except Exception as profile_exc:
        _logging.warning(
            "Provider profile lookup failed during %s; keeping current model names: %s",
            context,
            profile_exc,
        )


def _codex_auth_method(runtime: Any, has_codex_cli_auth: bool, auth_token: str) -> str:
    if has_codex_cli_auth and not auth_token:
        provider_class = runtime.provider.__class__.__name__ if runtime.provider is not None else ""
        if provider_class == "OpenAICodexResponsesProviderClient":
            return "mounted Codex OAuth token"
        return "Codex CLI auth"
    return "OAuth" if auth_token else "API key"


def _contains_word_or_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text))


def switch_provider(runtime: Any, provider_name: str) -> str:
    """Hot-swap the active LLM provider at runtime."""
    from providers.factory import API_KEY_VARS, create_provider

    api_key_var = API_KEY_VARS.get(provider_name)
    if api_key_var is None:
        raise ValueError(f"Unknown provider: {provider_name}")

    api_key = os.getenv(api_key_var, "") if api_key_var else ""
    auth_token = ""
    if provider_name == "anthropic" and not api_key:
        auth_token = runtime.get_anthropic_oauth_token()
    elif provider_name == "openai-codex":
        auth_token = runtime.get_openai_codex_oauth_token()

    has_codex_cli_auth = provider_name == "openai-codex" and runtime.has_openai_codex_cli_auth()
    has_local_endpoint = provider_name == "local-openai" and bool(
        os.getenv("LOCAL_OPENAI_BASE_URL", "").strip()
    )
    if not api_key and not auth_token and not has_codex_cli_auth and not has_local_endpoint:
        if provider_name == "local-openai":
            raise ValueError(
                f"No API key, OAuth token, or local endpoint configured for {provider_name}"
            )
        raise ValueError(f"No API key or OAuth token configured for {provider_name}")

    provider_mode = os.getenv("LANCELOT_PROVIDER_MODE", "sdk")
    provider_kwargs = {}
    stop_event = runtime.provider_stop_event()
    if stop_event is not None:
        provider_kwargs["stop_event"] = stop_event
    new_provider = create_provider(
        provider_name,
        api_key,
        mode=provider_mode,
        auth_token=auth_token,
        **provider_kwargs,
    )

    runtime.set_provider_runtime(
        new_provider,
        provider_name=provider_name,
        provider_mode=provider_mode,
    )
    _apply_provider_profile(runtime, provider_name, context="hot-swap")

    runtime.clear_context_cache()
    _clear_deep_model_validation_cache(runtime)

    auth_method = (
        _codex_auth_method(runtime, has_codex_cli_auth, auth_token)
        if provider_name == "openai-codex"
        else ("OAuth" if auth_token else "API key")
    )
    _gov_logger.info(
        "Provider hot-swapped to %s via %s (model: %s, mode: %s)",
        provider_name,
        auth_method,
        runtime.model_name,
        provider_mode,
    )
    return f"{provider_name.title()} provider active (model: {runtime.model_name}, mode: {provider_mode})"


def get_anthropic_oauth_token() -> str:
    """Return a valid Anthropic OAuth token from the shared token manager."""
    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager:
            return manager.get_valid_token() or ""
    except Exception as exc:
        _logging.warning("Anthropic OAuth token lookup failed: %s", exc)
    return ""


def get_openai_codex_oauth_token() -> str:
    """Try to get a valid OpenAI Codex OAuth token from the global token manager."""
    try:
        from openai_codex_oauth_manager import get_openai_codex_manager
        manager = get_openai_codex_manager()
        if manager:
            return manager.get_valid_token() or ""
    except Exception as exc:
        _logging.warning("OpenAI Codex OAuth token lookup failed: %s", exc)
    return ""


def has_openai_codex_cli_auth() -> bool:
    """Return True when mounted Codex CLI auth is available to the runtime."""
    try:
        from providers.codex_cli_client import has_codex_cli_auth

        return has_codex_cli_auth()
    except Exception as exc:
        _logging.warning("OpenAI Codex CLI auth lookup failed: %s", exc)
        return False


def set_lane_model(runtime: Any, lane: str, model_id: str) -> None:
    """Override the model assigned to a specific lane at runtime."""
    runtime.set_model_lane(lane, model_id)
    _gov_logger.info("Lane '%s' model overridden to %s", lane, model_id)


def get_deep_model(runtime: Any) -> str:
    """Return the deep/reasoning model name with graceful fallback."""
    deep_model = runtime.deep_model_name() or os.getenv("GEMINI_DEEP_MODEL", "")
    if not deep_model:
        return runtime.model_name

    cached_validation = runtime.cached_deep_model_validation(deep_model)
    if cached_validation is not None:
        return deep_model if cached_validation else runtime.model_name

    try:
        if runtime.provider:
            if runtime.provider.validate_model(deep_model):
                runtime.record_deep_model_validation(deep_model, True)
                _gov_logger.debug(
                    "deep_model_validated",
                    extra={"model": deep_model},
                )
                return deep_model
            raise ValueError(f"Model {deep_model} not accessible")
    except Exception as exc:
        _gov_logger.warning(
            "deep_model_unavailable",
            extra={
                "requested_model": deep_model,
                "fallback_model": runtime.model_name,
                "error": str(exc),
            },
        )
        runtime.record_deep_model_validation(deep_model, False)

    return runtime.model_name


def route_model(runtime: Any, user_message: str) -> str:
    """Select the fast or deep model for a user request."""
    msg_lower = user_message.lower()
    msg_len = len(user_message)

    trivial_keywords = [
        "hello", "hi", "thanks", "thank you", "status",
        "time", "date", "who are you", "hey", "good morning",
        "good night", "bye", "ok", "okay",
    ]
    if msg_len < 50 and any(_contains_word_or_phrase(msg_lower, keyword) for keyword in trivial_keywords):
        return runtime.model_name

    deep_task_keywords = [
        "plan", "architect", "analyze", "compare", "strategy",
        "evaluate", "diagnose", "debug", "refactor", "design",
        "tradeoff", "trade-off", "pros and cons", "step by step",
        "which approach", "best approach", "recommend",
        "explain why", "root cause", "investigate",
    ]
    risk_keywords = [
        "delete", "deploy", "production", "security", "migrate",
        "critical", "rollback", "downtime", "breaking change",
    ]
    complexity_phrases = [
        "how should we", "what's the best way", "what is the best way",
        "help me think through", "walk me through",
        "what are the options", "what are my options",
        "can you figure out", "research",
    ]

    needs_deep = False
    if any(keyword in msg_lower for keyword in deep_task_keywords):
        needs_deep = True
    if any(keyword in msg_lower for keyword in risk_keywords):
        needs_deep = True
    if any(keyword in msg_lower for keyword in complexity_phrases):
        needs_deep = True
    if msg_len > 500 and any(
        word in msg_lower
        for word in ["because", "however", "therefore", "consider", "alternatively", "given that"]
    ):
        needs_deep = True

    if needs_deep:
        deep = runtime.get_deep_model()
        if deep != runtime.model_name:
            _gov_logger.debug(
                "deep_model_selected",
                extra={"model": deep},
            )
        return deep

    return runtime.model_name
