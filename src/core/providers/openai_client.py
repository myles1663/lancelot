"""
OpenAIProviderClient — OpenAI adapter via openai SDK (v8.3.0).

Implements the ProviderClient interface for OpenAI models (GPT-4o, etc.).
Handles the OpenAI-specific message format, tool calling, and model listing.

Public API:
    OpenAIProviderClient(api_key)
"""

import json
import logging
import time
from typing import Any, Optional

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo
from providers.tool_schema import NormalizedToolDeclaration, to_openai_tools

logger = logging.getLogger(__name__)


class OpenAIProviderClient(ProviderClient):
    """OpenAI provider adapter using the openai SDK."""

    def __init__(self, api_key: str):
        try:
            import openai
            self._client = openai.OpenAI(api_key=api_key)
            logger.info("OpenAI provider initialized")
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed. Run: pip install openai"
            )

    @property
    def provider_name(self) -> str:
        return "openai"

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
        # Build message list with system instruction
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
        # Convert normalized declarations to OpenAI format
        if tools and isinstance(tools[0], NormalizedToolDeclaration):
            openai_tools = to_openai_tools(tools)
        else:
            openai_tools = tools

        api_messages = self._prepend_system(system_instruction, messages)

        # Map tool_config mode
        kwargs = {}
        if tool_config:
            mode = tool_config.get("mode", "AUTO")
            if mode == "ANY":
                kwargs["tool_choice"] = "required"
            elif mode == "NONE":
                kwargs["tool_choice"] = "none"
            # AUTO is the default, no need to set

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
        """Build OpenAI tool response messages.

        OpenAI requires one message per tool result.
        Returns a list of messages to extend the conversation with.
        """
        messages = []
        for call_id, _fn_name, result_str in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result_str),
            })
        return messages

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build OpenAI user message."""
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
        """Query OpenAI API for available models."""
        models = []
        try:
            for model in self._client.models.list():
                model_id = model.id
                # Filter to chat models only
                if not any(prefix in model_id for prefix in ("gpt-", "o1", "o3", "o4")):
                    continue

                tier = "standard"
                if "mini" in model_id:
                    tier = "fast"
                elif "o1" in model_id or "o3" in model_id or "o4" in model_id:
                    tier = "deep"

                models.append(ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    supports_tools=True,
                    capability_tier=tier,
                ))
        except Exception as e:
            logger.warning("OpenAI model listing failed: %s", e)

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
        """Prepend system message to the message list."""
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
                        "OpenAI API transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    def _parse_response(self, response) -> GenerateResult:
        """Convert an OpenAI response to GenerateResult."""
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

        # Usage
        usage = {"input_tokens": 0, "output_tokens": 0}
        if response.usage:
            usage["input_tokens"] = response.usage.prompt_tokens or 0
            usage["output_tokens"] = response.usage.completion_tokens or 0

        # raw = the assistant message dict for conversation continuity
        raw = message

        return GenerateResult(
            text=text,
            tool_calls=tool_calls,
            raw=raw,
            usage=usage,
        )
