"""
FlagshipClient — HTTP client for flagship AI provider APIs (Prompt 16).

Single-owner module providing a provider-agnostic interface to Gemini,
OpenAI, and Anthropic APIs.  Uses the REST endpoints directly via
urllib to avoid heavy SDK dependencies at the routing layer.

API keys are read from environment variables:
    GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY

Public API:
    FlagshipClient(provider, profile)
    client.complete(prompt, lane="fast", **kwargs) → str
    client.is_configured() → bool
    FlagshipError
"""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

from src.core.provider_profile import ProviderProfile, LaneConfig

logger = logging.getLogger(__name__)

# Provider API endpoints
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_XAI_URL = "https://api.x.ai/v1/chat/completions"

# Environment variable names for API keys
_API_KEY_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


class FlagshipError(Exception):
    """Raised when a flagship provider API call fails."""


class FlagshipClient:
    """Provider-agnostic HTTP client for flagship AI APIs."""

    def __init__(self, provider: str, profile: ProviderProfile):
        self._provider = provider
        self._profile = profile
        self._api_key: Optional[str] = None

        env_var = _API_KEY_VARS.get(provider)
        if env_var:
            self._api_key = os.environ.get(env_var)

    def is_configured(self) -> bool:
        """Check if the API key is available."""
        return bool(self._api_key)

    def complete(
        self,
        prompt: str,
        lane: str = "fast",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: float = 60.0,
    ) -> str:
        """Run a completion against the flagship provider.

        Args:
            prompt: The prompt text.
            lane: "fast", "deep", or "cache".
            max_tokens: Override max tokens (uses lane default if None).
            temperature: Override temperature (uses lane default if None).
            timeout: HTTP timeout in seconds.

        Returns:
            Generated text string.

        Raises:
            FlagshipError on API failure or missing configuration.
        """
        if not self._api_key:
            raise FlagshipError(
                f"API key not configured for {self._provider} "
                f"(set {_API_KEY_VARS.get(self._provider, 'UNKNOWN')})"
            )

        lane_config = self._get_lane_config(lane)
        effective_max = max_tokens or lane_config.max_tokens
        effective_temp = temperature if temperature is not None else lane_config.temperature

        if self._provider == "gemini":
            return self._call_gemini(prompt, lane_config.model, effective_max, effective_temp, timeout)
        elif self._provider == "openai":
            return self._call_openai(prompt, lane_config.model, effective_max, effective_temp, timeout)
        elif self._provider == "anthropic":
            return self._call_anthropic(prompt, lane_config.model, effective_max, effective_temp, timeout)
        elif self._provider == "xai":
            return self._call_xai(prompt, lane_config.model, effective_max, effective_temp, timeout)
        else:
            raise FlagshipError(f"Unsupported provider: {self._provider}")

    def _get_lane_config(self, lane: str) -> LaneConfig:
        """Get the LaneConfig for the specified lane."""
        if lane == "fast":
            return self._profile.fast
        elif lane == "deep":
            return self._profile.deep
        elif lane == "cache":
            if self._profile.cache is None:
                raise FlagshipError(
                    f"Provider '{self._provider}' has no cache lane configured"
                )
            return self._profile.cache
        else:
            raise FlagshipError(f"Unknown lane: '{lane}'")

    # ------------------------------------------------------------------
    # Provider-specific API calls
    # ------------------------------------------------------------------

    def _call_gemini(
        self, prompt: str, model: str, max_tokens: int, temperature: float, timeout: float
    ) -> str:
        url = _GEMINI_URL.format(model=model)
        headers = {"x-goog-api-key": self._api_key}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        data = self._http_post(url, payload, timeout, extra_headers=headers)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise FlagshipError(f"Unexpected Gemini response: {exc}") from exc

    def _call_openai(
        self, prompt: str, model: str, max_tokens: int, temperature: float, timeout: float
    ) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        data = self._http_post(_OPENAI_URL, payload, timeout, extra_headers=headers)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise FlagshipError(f"Unexpected OpenAI response: {exc}") from exc

    def _call_anthropic(
        self, prompt: str, model: str, max_tokens: int, temperature: float, timeout: float
    ) -> str:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2024-10-22",
        }
        data = self._http_post(_ANTHROPIC_URL, payload, timeout, extra_headers=headers)
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise FlagshipError(f"Unexpected Anthropic response: {exc}") from exc

    def _call_xai(
        self, prompt: str, model: str, max_tokens: int, temperature: float, timeout: float
    ) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        data = self._http_post(_XAI_URL, payload, timeout, extra_headers=headers)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise FlagshipError(f"Unexpected xAI response: {exc}") from exc

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _http_post(
        self,
        url: str,
        payload: dict,
        timeout: float,
        extra_headers: Optional[dict] = None,
    ) -> dict:
        """POST JSON and return parsed response."""
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise FlagshipError(
                f"HTTP {exc.code} from {self._provider}: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise FlagshipError(
                f"Connection failed to {self._provider}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise FlagshipError(
                f"Request to {self._provider} failed: {exc}"
            ) from exc
