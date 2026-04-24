"""
Provider Factory — creates the appropriate ProviderClient (v8.3.0).

Public API:
    create_provider(provider_name, api_key, **kwargs) → ProviderClient
"""

import logging
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
        provider_name: One of "gemini", "openai", "openai-codex", "anthropic", "xai", "nvidia".
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

    raise ValueError(
        f"Unknown provider: '{provider_name}'. "
        f"Available: {', '.join(API_KEY_VARS.keys())}"
    )
