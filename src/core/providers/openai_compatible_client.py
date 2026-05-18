"""
OpenAICompatibleProviderClient - adapter for OpenAI-compatible chat APIs.

Used for hosted providers with OpenAI-compatible endpoints and for operator
managed local/open-weight model servers such as Ollama, vLLM, llama.cpp, or LM
Studio when they expose a Chat Completions API.
"""

import json
import logging
import time
from typing import Any, Callable, Optional

from providers.base import (
    GenerateResult,
    ModelInfo,
    ProviderAuthError,
    ProviderClient,
    ToolCall,
    _is_auth_error,
)
from providers.tool_schema import NormalizedToolDeclaration, to_openai_tools

logger = logging.getLogger(__name__)


class OpenAICompatibleProviderClient(ProviderClient):
    """Generic OpenAI-compatible provider adapter."""

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str = "",
        base_url: str,
        known_models: Optional[list[ModelInfo]] = None,
        model_filter: Optional[Callable[[str], bool]] = None,
    ):
        if not base_url:
            raise ValueError(f"{provider_name} base URL is required")

        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._known_models = list(known_models or [])
        self._model_filter = model_filter
        try:
            import openai as _openai
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed (required for OpenAI-compatible providers)."
            )

        self._client = _openai.OpenAI(
            api_key=api_key or "local-provider",
            base_url=self._base_url,
        )
        logger.info(
            "%s provider initialized via OpenAI-compatible endpoint (base_url=%s)",
            provider_name,
            self._base_url,
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def generate(
        self,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ) -> GenerateResult:
        api_messages = self._prepend_system(system_instruction, messages)
        kwargs = self._generation_kwargs(config)
        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=api_messages,
                **kwargs,
            )
        )
        return self._parse_response(response)

    def generate_with_tools(
        self,
        model: str,
        messages: list,
        system_instruction: str,
        tools: list,
        tool_config: Optional[dict] = None,
        config: Optional[dict] = None,
    ) -> GenerateResult:
        if tools and isinstance(tools[0], NormalizedToolDeclaration):
            openai_tools = to_openai_tools(tools)
        else:
            openai_tools = tools

        api_messages = self._prepend_system(system_instruction, messages)
        kwargs = self._generation_kwargs(config)
        if tool_config:
            mode = tool_config.get("mode", "AUTO")
            if mode == "ANY":
                kwargs["tool_choice"] = "required"
            elif mode == "NONE":
                kwargs["tool_choice"] = "none"

        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=api_messages,
                tools=openai_tools,
                **kwargs,
            )
        )
        return self._parse_response(response)

    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return [
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result_str),
            }
            for call_id, _fn_name, result_str in tool_results
        ]

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        if images:
            import base64

            content = []
            for img_data, mime_type in images:
                b64 = base64.b64encode(img_data).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                })
            content.append({"type": "text", "text": text})
            return {"role": "user", "content": content}

        return {"role": "user", "content": text}

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        try:
            for model in self._client.models.list():
                model_id = model.id
                if self._model_filter and not self._model_filter(model_id):
                    continue
                models.append(self._known_model_or_default(model_id))
        except Exception as exc:
            logger.warning(
                "%s model listing failed: %s - using configured catalog",
                self._provider_name,
                exc,
            )
            return list(self._known_models)

        return models if models else list(self._known_models)

    def validate_model(self, model_id: str) -> bool:
        if any(model.id == model_id for model in self._known_models):
            return True
        try:
            self._client.models.retrieve(model_id)
            return True
        except Exception:
            return False

    @staticmethod
    def _prepend_system(system_instruction: str, messages: list) -> list:
        result = []
        if system_instruction:
            result.append({"role": "system", "content": system_instruction})
        result.extend(messages)
        return result

    @staticmethod
    def _generation_kwargs(config: Optional[dict]) -> dict:
        if not config:
            return {}

        kwargs = {}
        if "temperature" in config:
            kwargs["temperature"] = config["temperature"]
        if "max_tokens" in config:
            kwargs["max_tokens"] = config["max_tokens"]
        if config.get("thinking"):
            kwargs["extra_body"] = {"thinking": config["thinking"]}
        if config.get("reasoning_effort"):
            kwargs["reasoning_effort"] = config["reasoning_effort"]
        return kwargs

    def _known_model_or_default(self, model_id: str) -> ModelInfo:
        for model in self._known_models:
            if model.id == model_id:
                return model

        tier = "standard"
        lowered = model_id.lower()
        if any(token in lowered for token in ("flash", "mini", "small", "7b", "8b")):
            tier = "fast"
        elif any(token in lowered for token in ("pro", "reason", "70b", "120b", "405b")):
            tier = "deep"

        return ModelInfo(
            id=model_id,
            display_name=model_id,
            supports_tools=True,
            capability_tier=tier,
        )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        err_str = str(exc).lower()
        return any(kw in err_str for kw in (
            "429", "rate_limit", "503", "service_unavailable", "overloaded", "timeout"
        ))

    def _call_with_retry(self, call_fn, max_retries: int = 3, base_delay: float = 1.0):
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return call_fn()
            except Exception as exc:
                last_exc = exc
                if _is_auth_error(exc):
                    raise ProviderAuthError(self._provider_name, str(exc)) from exc
                if attempt < max_retries and self._is_retryable_error(exc):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "%s API transient error (attempt %d/%d): %s - retrying in %.1fs",
                        self._provider_name,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    def _parse_response(self, response) -> GenerateResult:
        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls = []

        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}
                tool_calls.append(ToolCall(
                    name=tc.function.name,
                    args=args,
                    id=tc.id,
                ))

        usage = {"input_tokens": 0, "output_tokens": 0}
        if getattr(response, "usage", None):
            usage["input_tokens"] = response.usage.prompt_tokens or 0
            usage["output_tokens"] = response.usage.completion_tokens or 0

        return GenerateResult(
            text=text,
            tool_calls=tool_calls,
            raw=message,
            usage=usage,
        )
