"""
Provider Factory — creates the appropriate ProviderClient (v8.3.0).

Public API:
    create_provider(provider_name, api_key, **kwargs) → ProviderClient
"""

import logging
import os
from typing import Optional

from providers.base import ProviderAuthError, ProviderClient

logger = logging.getLogger(__name__)

# Environment variable names for API keys
API_KEY_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "",  # Codex uses OAuth, no API key env var
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "local-openai": "LOCAL_OPENAI_API_KEY",
}


def create_provider(
    provider_name: str,
    api_key: str,
    mode: str = "sdk",
    auth_token: str = "",
    **kwargs,
) -> ProviderClient:
    """Factory to create the right ProviderClient based on provider name.

    Args:
        provider_name: One of "gemini", "openai", "openai-codex", "anthropic",
            "xai", "nvidia", "deepseek", "local-openai".
        api_key: The API key for the provider.
        mode: "sdk" (full SDK features) or "api" (lightweight). Default: "sdk".
        auth_token: OAuth bearer token (Anthropic or OpenAI Codex, takes priority over api_key).
        **kwargs: Additional provider-specific options.

    Returns:
        An initialized ProviderClient instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    if provider_name == "gemini":
        from providers.gemini_client import GeminiProviderClient
        return GeminiProviderClient(api_key=api_key, **kwargs)

    elif provider_name == "openai":
        from providers.openai_client import OpenAIProviderClient
        return OpenAIProviderClient(api_key=api_key, **kwargs)

    elif provider_name == "openai-codex":
        try:
            from providers.codex_responses_client import OpenAICodexResponsesProviderClient
            return OpenAICodexResponsesProviderClient(auth_token=auth_token, **kwargs)
        except ImportError as exc:
            logger.warning(
                "Codex Responses provider import failed; falling back to Codex CLI transport: %s",
                exc,
            )
            from providers.codex_cli_client import CodexCLIProviderClient
            return CodexCLIProviderClient(auth_token=auth_token, **kwargs)
        except ProviderAuthError as exc:
            logger.warning(
                "Codex Responses provider unavailable; falling back to Codex CLI transport: %s",
                exc,
            )
            from providers.codex_cli_client import CodexCLIProviderClient
            return CodexCLIProviderClient(auth_token=auth_token, **kwargs)

    elif provider_name == "anthropic":
        from providers.anthropic_client import AnthropicProviderClient
        return AnthropicProviderClient(api_key=api_key, mode=mode, auth_token=auth_token, **kwargs)

    elif provider_name == "xai":
        from providers.xai_client import XAIProviderClient
        return XAIProviderClient(api_key=api_key, **kwargs)

    elif provider_name == "nvidia":
        from providers.nvidia_client import NvidiaProviderClient
        return NvidiaProviderClient(api_key=api_key, **kwargs)

    elif provider_name == "deepseek":
        from providers.base import ModelInfo
        from providers.openai_compatible_client import OpenAICompatibleProviderClient

        return OpenAICompatibleProviderClient(
            provider_name="deepseek",
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            known_models=[
                ModelInfo(
                    id="deepseek-v4-flash",
                    display_name="DeepSeek V4 Flash",
                    context_window=1_000_000,
                    supports_tools=True,
                    input_cost_per_1k=0.00014,
                    output_cost_per_1k=0.00028,
                    capability_tier="fast",
                ),
                ModelInfo(
                    id="deepseek-v4-pro",
                    display_name="DeepSeek V4 Pro",
                    context_window=1_000_000,
                    supports_tools=True,
                    input_cost_per_1k=0.000435,
                    output_cost_per_1k=0.00087,
                    capability_tier="deep",
                ),
            ],
            model_filter=lambda model_id: model_id.startswith("deepseek-"),
            **kwargs,
        )

    elif provider_name == "local-openai":
        from providers.base import ModelInfo
        from providers.openai_compatible_client import OpenAICompatibleProviderClient

        base_url = kwargs.pop("base_url", "") or os.getenv("LOCAL_OPENAI_BASE_URL", "")
        context_window = _int_env("LOCAL_OPENAI_CONTEXT_WINDOW", 32768)
        supports_tools = os.getenv("LOCAL_OPENAI_SUPPORTS_TOOLS", "true").strip().lower() not in {
            "0", "false", "no", "off",
        }
        fast_model = os.getenv("LOCAL_OPENAI_FAST_MODEL", "local-fast")
        deep_model = os.getenv("LOCAL_OPENAI_DEEP_MODEL", fast_model)
        cache_model = os.getenv("LOCAL_OPENAI_CACHE_MODEL", fast_model)
        known_models = []
        for model_id, tier in (
            (fast_model, "fast"),
            (deep_model, "deep"),
            (cache_model, "fast"),
        ):
            if model_id and not any(model.id == model_id for model in known_models):
                known_models.append(ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    context_window=context_window,
                    supports_tools=supports_tools,
                    input_cost_per_1k=0.0,
                    output_cost_per_1k=0.0,
                    capability_tier=tier,
                ))

        return OpenAICompatibleProviderClient(
            provider_name="local-openai",
            api_key=api_key,
            base_url=base_url,
            known_models=known_models,
            **kwargs,
        )

    raise ValueError(
        f"Unknown provider: '{provider_name}'. "
        f"Available: {', '.join(API_KEY_VARS.keys())}"
    )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
