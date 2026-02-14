"""
AnthropicProviderClient — Anthropic adapter via anthropic SDK (v8.3.0).

Implements the ProviderClient interface for Anthropic models (Claude).
Handles Anthropic's message format, tool_use/tool_result blocks, and model listing.

Public API:
    AnthropicProviderClient(api_key)
"""

import json
import logging
import time
from typing import Any, Optional

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo
from providers.tool_schema import NormalizedToolDeclaration, to_anthropic_tools

logger = logging.getLogger(__name__)


class AnthropicProviderClient(ProviderClient):
    """Anthropic provider adapter using the anthropic SDK."""

    def __init__(self, api_key: str):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            logger.info("Anthropic provider initialized")
        except ImportError:
            raise ImportError(
                "Anthropic SDK not installed. Run: pip install anthropic"
            )

    @property
    def provider_name(self) -> str:
        return "anthropic"

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
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": 8192,
        }
        if system_instruction:
            kwargs["system"] = system_instruction

        response = self._call_with_retry(
            lambda: self._client.messages.create(**kwargs)
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
        # Convert normalized declarations to Anthropic format
        if tools and isinstance(tools[0], NormalizedToolDeclaration):
            anthropic_tools = to_anthropic_tools(tools)
        else:
            anthropic_tools = tools

        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": 8192,
            "tools": anthropic_tools,
        }
        if system_instruction:
            kwargs["system"] = system_instruction

        # Map tool_config mode
        if tool_config:
            mode = tool_config.get("mode", "AUTO")
            if mode == "ANY":
                kwargs["tool_choice"] = {"type": "any"}
            elif mode == "NONE":
                # Don't pass tools at all for NONE mode
                del kwargs["tools"]
            # AUTO is the default

        response = self._call_with_retry(
            lambda: self._client.messages.create(**kwargs)
        )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        """Build Anthropic tool_result message.

        Anthropic requires all tool results in a single user message
        with type=tool_result content blocks.
        """
        content = []
        for call_id, _fn_name, result_str in tool_results:
            content.append({
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(result_str),
            })
        return {"role": "user", "content": content}

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build Anthropic user message."""
        if images:
            import base64
            content = []
            for img_data, mime_type in images:
                b64 = base64.b64encode(img_data).decode("utf-8")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64,
                    },
                })
            content.append({"type": "text", "text": text})
            return {"role": "user", "content": content}

        return {"role": "user", "content": text}

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """Query Anthropic API for available models."""
        models = []
        try:
            response = self._client.models.list()
            for model in response.data:
                model_id = model.id
                tier = "standard"
                if "haiku" in model_id:
                    tier = "fast"
                elif "opus" in model_id:
                    tier = "deep"
                elif "sonnet" in model_id:
                    tier = "deep"

                models.append(ModelInfo(
                    id=model_id,
                    display_name=getattr(model, "display_name", model_id),
                    supports_tools=True,
                    capability_tier=tier,
                ))
        except Exception as e:
            logger.warning("Anthropic model listing failed: %s", e)
            # Fallback: return known models
            models = [
                ModelInfo(id="claude-3-5-haiku-latest", display_name="Claude 3.5 Haiku",
                          context_window=200000, supports_tools=True, capability_tier="fast"),
                ModelInfo(id="claude-sonnet-4-20250514", display_name="Claude Sonnet 4",
                          context_window=200000, supports_tools=True, capability_tier="deep"),
            ]

        return models

    def validate_model(self, model_id: str) -> bool:
        try:
            self._client.models.retrieve(model_id)
            return True
        except Exception:
            # Fallback: assume valid if it matches known patterns
            return any(name in model_id for name in ("claude", "haiku", "sonnet", "opus"))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        err_str = str(exc).lower()
        return any(kw in err_str for kw in (
            "429", "rate_limit", "529", "overloaded", "timeout", "503"
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
                        "Anthropic API transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    def _parse_response(self, response) -> GenerateResult:
        """Convert an Anthropic response to GenerateResult."""
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    name=block.name,
                    args=block.input if isinstance(block.input, dict) else {},
                    id=block.id,
                ))

        text = "\n".join(text_parts) if text_parts else None

        # Usage
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "input_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "output_tokens", 0) or 0

        # raw = the response content blocks for conversation continuity
        # Anthropic needs the assistant message appended as-is
        raw = {"role": "assistant", "content": response.content}

        return GenerateResult(
            text=text,
            tool_calls=tool_calls,
            raw=raw,
            usage=usage,
        )
