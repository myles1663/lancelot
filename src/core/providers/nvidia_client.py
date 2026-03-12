# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
NvidiaProviderClient — NVIDIA Nemotron adapter via OpenAI-compatible NIM API.

NVIDIA NIM exposes an OpenAI-compatible REST API at https://integrate.api.nvidia.com/v1,
so we reuse the openai SDK with a custom base_url.

Supported models:
    - nvidia/nemotron-3-nano-30b-a3b       (30B params, 3B active, 256K context)
    - nvidia/nemotron-3-super-120b-a12b     (120B params, 12B active, 1M context)
    - nvidia/nemotron-nano-9b-v2            (9B params, 128K context)
    - nvidia/llama-3.3-nemotron-super-49b-v1 (49B params, 131K context)
    - nvidia/llama-3.1-nemotron-70b-instruct (70B params, 128K context)

Public API:
    NvidiaProviderClient(api_key, base_url=None)
"""

import json
import logging
import os
import time
from typing import Any, Optional

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo, ProviderAuthError, _is_auth_error
from providers.tool_schema import NormalizedToolDeclaration, to_openai_tools

logger = logging.getLogger(__name__)

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Known Nemotron models for list_models() fallback
_KNOWN_MODELS = [
    ModelInfo(
        id="nvidia/nemotron-3-nano-30b-a3b",
        display_name="Nemotron 3 Nano 30B",
        context_window=262144,
        supports_tools=True,
        input_cost_per_1k=0.00005,
        output_cost_per_1k=0.0002,
        capability_tier="fast",
    ),
    ModelInfo(
        id="nvidia/nemotron-3-super-120b-a12b",
        display_name="Nemotron 3 Super 120B",
        context_window=1048576,
        supports_tools=True,
        input_cost_per_1k=0.00058,
        output_cost_per_1k=0.0013,
        capability_tier="deep",
    ),
    ModelInfo(
        id="nvidia/nemotron-nano-9b-v2",
        display_name="Nemotron Nano 9B v2",
        context_window=131072,
        supports_tools=True,
        input_cost_per_1k=0.00004,
        output_cost_per_1k=0.00016,
        capability_tier="fast",
    ),
    ModelInfo(
        id="nvidia/llama-3.3-nemotron-super-49b-v1",
        display_name="Llama 3.3 Nemotron Super 49B",
        context_window=131072,
        supports_tools=True,
        input_cost_per_1k=0.0001,
        output_cost_per_1k=0.0004,
        capability_tier="standard",
    ),
    ModelInfo(
        id="nvidia/llama-3.1-nemotron-70b-instruct",
        display_name="Llama 3.1 Nemotron 70B Instruct",
        context_window=131072,
        supports_tools=True,
        input_cost_per_1k=0.0012,
        output_cost_per_1k=0.0012,
        capability_tier="deep",
    ),
]


class NvidiaProviderClient(ProviderClient):
    """NVIDIA Nemotron provider adapter using the openai SDK with NIM base_url."""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self._base_url = base_url or os.getenv("NVIDIA_BASE_URL", NVIDIA_NIM_BASE_URL)
        try:
            import openai as _openai
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed (required for NVIDIA NIM). Run: pip install openai"
            )
        self._openai = _openai
        self._client = _openai.OpenAI(
            api_key=api_key,
            base_url=self._base_url,
        )
        logger.info("NVIDIA provider initialized (base_url=%s)", self._base_url)

    @property
    def provider_name(self) -> str:
        return "nvidia"

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
        """Build OpenAI-compatible tool response messages."""
        messages = []
        for call_id, _fn_name, result_str in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result_str),
            })
        return messages

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build OpenAI-compatible user message."""
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
        """Query NIM API for available models, falling back to known catalog."""
        models = []
        try:
            for model in self._client.models.list():
                model_id = model.id
                if not model_id.startswith("nvidia/"):
                    continue

                tier = "standard"
                if "nano" in model_id:
                    tier = "fast"
                elif "super" in model_id or "ultra" in model_id or "70b" in model_id:
                    tier = "deep"

                models.append(ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    supports_tools=True,
                    capability_tier=tier,
                ))
        except Exception as e:
            logger.warning("NVIDIA model listing failed: %s — using known catalog", e)
            return list(_KNOWN_MODELS)

        return models if models else list(_KNOWN_MODELS)

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
                if _is_auth_error(e):
                    raise ProviderAuthError("nvidia", str(e)) from e
                if attempt < max_retries and self._is_retryable_error(e):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "NVIDIA API transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    def _parse_response(self, response) -> GenerateResult:
        """Convert an OpenAI-compatible response to GenerateResult."""
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
