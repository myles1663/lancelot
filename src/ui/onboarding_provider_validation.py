"""Live provider credential probes used by the onboarding flow."""

from __future__ import annotations

from src.core.outbound_http import OutboundNetworkError, assert_url_allowed


def validate_api_key_live(
    provider: str,
    key: str,
    *,
    url_validator=assert_url_allowed,
    network_error_type=OutboundNetworkError,
) -> dict:
    """Validate provider credentials with bounded HTTP probes."""
    import requests

    try:
        if provider == "gemini":
            url = url_validator(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                component="Onboarding Gemini API key validation",
            )
            response = requests.get(url, timeout=10)
            if response.ok:
                return {"valid": True}
            if response.status_code in (400, 403):
                return {"valid": False, "error": "Invalid API key - rejected by Google"}
            return {"valid": False, "error": f"Unexpected response (HTTP {response.status_code})"}

        if provider == "openai":
            url = url_validator(
                "https://api.openai.com/v1/models",
                component="Onboarding OpenAI API key validation",
            )
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if response.ok:
                return {"valid": True}
            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - rejected by OpenAI"}
            return {"valid": False, "error": f"Unexpected response (HTTP {response.status_code})"}

        if provider == "anthropic":
            url = url_validator(
                "https://api.anthropic.com/v1/messages",
                component="Onboarding Anthropic API key validation",
            )
            response = requests.post(
                url,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-latest",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - rejected by Anthropic"}
            return {"valid": True}

        if provider == "xai":
            url = url_validator(
                "https://api.x.ai/v1/models",
                component="Onboarding xAI API key validation",
            )
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if response.ok:
                return {"valid": True}
            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - rejected by xAI"}
            return {"valid": False, "error": f"Unexpected response (HTTP {response.status_code})"}

        if provider == "nvidia":
            url = url_validator(
                "https://integrate.api.nvidia.com/v1/models",
                component="Onboarding NVIDIA API key validation",
            )
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if response.ok:
                return {"valid": True}
            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - rejected by NVIDIA"}
            return {"valid": False, "error": f"Unexpected response (HTTP {response.status_code})"}

        if provider == "deepseek":
            url = url_validator(
                "https://api.deepseek.com/models",
                component="Onboarding DeepSeek API key validation",
            )
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if response.ok:
                return {"valid": True}
            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - rejected by DeepSeek"}
            return {"valid": False, "error": f"Unexpected response (HTTP {response.status_code})"}

        return {"valid": False, "error": f"Unknown provider: {provider}"}

    except network_error_type as exc:
        return {"valid": False, "error": str(exc)}
    except Exception as exc:
        return {"valid": True, "warning": f"Could not reach {provider} API to validate: {exc}"}
