"""
OpenAIProviderClient — OpenAI adapter via openai SDK (v8.3.0).

Implements the ProviderClient interface for OpenAI models (GPT-4o, etc.).
Handles the OpenAI-specific message format, tool calling, and model listing.
Supports both API key auth and Codex OAuth (ChatGPT subscription).

Public API:
    OpenAIProviderClient(api_key, auth_token, base_url)
"""

import json
import logging
import time
from typing import Any, Optional

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo, ProviderAuthError, _is_auth_error
from providers.tool_schema import NormalizedToolDeclaration, to_openai_tools

logger = logging.getLogger(__name__)


class OpenAIProviderClient(ProviderClient):
    """OpenAI provider adapter using the openai SDK.

    Supports two auth modes:
        1. API key → standard api.openai.com endpoint
        2. Codex OAuth token → chatgpt.com/backend-api/codex endpoint
           (uses ChatGPT Plus/Pro subscription for flat-rate access)
    """

    # Codex API base URL — uses standard OpenAI API with OAuth token
    # (chatgpt.com/backend-api/codex is Cloudflare-protected; the OAuth
    # token works against the regular api.openai.com endpoint)
    CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex/v1"

    def __init__(self, api_key: str = "", auth_token: str = "", base_url: str = ""):
        self._auth_token = auth_token
        self._api_key = api_key
        self._base_url = base_url
        self._is_codex = bool(auth_token and not api_key)
        try:
            import openai
            if auth_token:
                effective_base = base_url or self.CODEX_BASE_URL
                self._client = openai.OpenAI(
                    api_key=auth_token,  # OpenAI SDK uses api_key for Bearer token
                    base_url=effective_base,
                )
                logger.info("OpenAI provider initialized via Codex OAuth (base_url=%s)", effective_base)
            else:
                kwargs = {"api_key": api_key}
                if base_url:
                    kwargs["base_url"] = base_url
                self._client = openai.OpenAI(**kwargs)
                logger.info("OpenAI provider initialized via API key")
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed. Run: pip install openai"
            )

    def update_auth_token(self, new_token: str) -> None:
        """Hot-swap the Codex OAuth token without full re-init (called by refresh manager)."""
        import openai
        self._auth_token = new_token
        effective_base = self._base_url or self.CODEX_BASE_URL
        self._client = openai.OpenAI(
            api_key=new_token,
            base_url=effective_base,
        )
        logger.info("OpenAI Codex OAuth token hot-swapped")

    @property
    def provider_name(self) -> str:
        return "openai-codex" if self._is_codex else "openai"

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

    # Known GPT-5.4 models available via Codex subscription
    # Pricing: per 1M tokens → per 1K tokens (divide by 1000)
    _CODEX_MODELS = [
        ModelInfo(id="gpt-5.1-codex", display_name="GPT-5.1 Codex", supports_tools=True,
                  capability_tier="deep", context_window=400000,
                  input_cost_per_1k=0.00125, output_cost_per_1k=0.01),
        ModelInfo(id="gpt-5.1-codex-mini", display_name="GPT-5.1 Codex Mini", supports_tools=True,
                  capability_tier="fast", context_window=400000,
                  input_cost_per_1k=0.00025, output_cost_per_1k=0.002),
    ]

    def list_models(self) -> list[ModelInfo]:
        """Query OpenAI API for available models.

        Falls back to known model list for Codex OAuth because the ChatGPT
        Codex backend does not expose the standard model listing surface.
        """
        if self._is_codex:
            logger.info("Codex mode: returning known Codex model list")
            return list(self._CODEX_MODELS)

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
        if self._is_codex:
            return any(model.id == model_id for model in self._CODEX_MODELS)
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
        oauth_refreshed = False
        for attempt in range(max_retries + 1):
            try:
                return call_fn()
            except Exception as e:
                last_exc = e
                # On auth error with Codex OAuth, try refreshing the token once
                if _is_auth_error(e) and self._is_codex and not oauth_refreshed:
                    if self._try_oauth_refresh():
                        oauth_refreshed = True
                        logger.info("Codex OAuth token refreshed after 401, retrying…")
                        continue
                    raise ProviderAuthError("openai-codex", str(e)) from e
                if _is_auth_error(e):
                    raise ProviderAuthError("openai", str(e)) from e
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

    def _try_oauth_refresh(self) -> bool:
        """Attempt to refresh Codex OAuth token and hot-swap it."""
        try:
            from openai_codex_oauth_manager import get_openai_codex_manager
            manager = get_openai_codex_manager()
            if manager:
                new_token = manager.get_valid_token()
                if new_token and new_token != self._auth_token:
                    self.update_auth_token(new_token)
                    return True
        except Exception as e:
            logger.warning("Codex OAuth refresh attempt failed: %s", e)
        return False

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
