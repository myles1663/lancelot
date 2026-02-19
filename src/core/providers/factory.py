"""
Provider Factory — creates the appropriate ProviderClient (v8.3.0).

Public API:
    create_provider(provider_name, api_key, **kwargs) → ProviderClient
"""

import logging
from typing import Optional

from providers.base import ProviderClient

logger = logging.getLogger(__name__)

# Environment variable names for API keys
API_KEY_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


def create_provider(
    provider_name: str,
    api_key: str,
    mode: str = "sdk",
    **kwargs,
) -> ProviderClient:
    """Factory to create the right ProviderClient based on provider name.

    Args:
        provider_name: One of "gemini", "openai", "anthropic", "xai".
        api_key: The API key for the provider.
        mode: "sdk" (full SDK features) or "api" (lightweight). Default: "sdk".
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

    elif provider_name == "anthropic":
        from providers.anthropic_client import AnthropicProviderClient
        return AnthropicProviderClient(api_key=api_key, mode=mode, **kwargs)

    elif provider_name == "xai":
        from providers.xai_client import XAIProviderClient
        return XAIProviderClient(api_key=api_key, **kwargs)

    raise ValueError(
        f"Unknown provider: '{provider_name}'. "
        f"Available: {', '.join(API_KEY_VARS.keys())}"
    )
