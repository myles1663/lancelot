# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
XAIProviderClient — xAI (Grok) adapter via OpenAI-compatible API.

xAI exposes an OpenAI-compatible REST API at https://api.x.ai/v1,
so we reuse the openai SDK with a custom base_url.

Public API:
    XAIProviderClient(api_key)
"""

import json
import logging
import time
from typing import Any, Optional

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo
from providers.tool_schema import NormalizedToolDeclaration, to_openai_tools

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"


class XAIProviderClient(ProviderClient):
    """xAI (Grok) provider adapter using the openai SDK with custom base_url."""

    def __init__(self, api_key: str):
        try:
            import openai
            self._client = openai.OpenAI(
                api_key=api_key,
                base_url=XAI_BASE_URL,
            )
            logger.info("xAI provider initialized (base_url=%s)", XAI_BASE_URL)
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed (required for xAI). Run: pip install openai"
            )

    @property
    def provider_name(self) -> str:
        return "xai"

    # ------------------------------------------------------------------
    # Generate (text only)
    # ------------------------------------------------------------------

    def generate(
        self,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ) -> GenerateResult:
        api_messages = self._prepend_system(system_instruction, messages)

        response = self._call_with_retry(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=api_messages,
            )
        )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Generate with tools
    # ------------------------------------------------------------------

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

        kwargs = {}
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

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        """Build xAI/OpenAI-compatible tool response messages."""
        messages = []
        for call_id, _fn_name, result_str in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result_str),
            })
        return messages

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build xAI/OpenAI-compatible user message."""
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

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """Query xAI API for available Grok models."""
        models = []
        try:
            for model in self._client.models.list():
                model_id = model.id
                if not model_id.startswith("grok-"):
                    continue
                # Skip image/video generation models
                if "image" in model_id or "imagine" in model_id or "video" in model_id:
                    continue

                tier = "standard"
                if "mini" in model_id:
                    tier = "fast"
                elif "grok-4" in model_id or "grok-3" == model_id:
                    tier = "deep"

                models.append(ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    supports_tools=True,
                    capability_tier=tier,
                ))
        except Exception as e:
            logger.warning("xAI model listing failed: %s", e)

        return models

    def validate_model(self, model_id: str) -> bool:
        try:
            self._client.models.retrieve(model_id)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prepend_system(system_instruction: str, messages: list) -> list:
        result = []
        if system_instruction:
            result.append({"role": "system", "content": system_instruction})
        result.extend(messages)
        return result

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
            except Exception as e:
                last_exc = e
                if attempt < max_retries and self._is_retryable_error(e):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "xAI API transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    def _parse_response(self, response) -> GenerateResult:
        """Convert an xAI/OpenAI-compatible response to GenerateResult."""
        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls = []

        if message.tool_calls:
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
        if response.usage:
            usage["input_tokens"] = response.usage.prompt_tokens or 0
            usage["output_tokens"] = response.usage.completion_tokens or 0

        raw = message

        return GenerateResult(
            text=text,
            tool_calls=tool_calls,
            raw=raw,
            usage=usage,
        )
