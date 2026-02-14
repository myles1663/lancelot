"""
GeminiProviderClient — Google Gemini adapter via google-genai SDK (v8.3.0).

Wraps the existing Gemini integration behind the ProviderClient interface.
Preserves all existing behavior: retry logic, thinking config, multimodal.

Public API:
    GeminiProviderClient(api_key, credentials=None)
"""

import json
import logging
import time
import uuid
from typing import Any, Optional

from google import genai
from google.genai import types

from providers.base import ProviderClient, GenerateResult, ToolCall, ModelInfo
from providers.tool_schema import NormalizedToolDeclaration, to_gemini_declarations

logger = logging.getLogger(__name__)


class GeminiProviderClient(ProviderClient):
    """Gemini provider adapter using the google-genai SDK."""

    def __init__(self, api_key: str = "", credentials: Any = None):
        if api_key:
            self._client = genai.Client(api_key=api_key)
            logger.info("Gemini provider initialized via API key")
        elif credentials:
            self._client = genai.Client(credentials=credentials)
            logger.info("Gemini provider initialized via OAuth credentials")
        else:
            raise ValueError("GeminiProviderClient requires api_key or credentials")

    @property
    def provider_name(self) -> str:
        return "gemini"

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
        config = config or {}
        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
        )

        # Apply thinking config if provided (convert dict to types.ThinkingConfig)
        thinking = config.get("thinking")
        if thinking:
            if isinstance(thinking, dict):
                gen_config.thinking_config = types.ThinkingConfig(**thinking)
            else:
                gen_config.thinking_config = thinking

        response = self._call_with_retry(
            lambda: self._client.models.generate_content(
                model=model,
                contents=messages,
                config=gen_config,
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
        config = config or {}

        # Convert normalized declarations to Gemini native format
        if tools and isinstance(tools[0], NormalizedToolDeclaration):
            gemini_declarations = to_gemini_declarations(tools)
        else:
            # Already native Gemini declarations (backward compat)
            gemini_declarations = tools

        gemini_tool = types.Tool(function_declarations=gemini_declarations)

        # Build tool config
        gemini_tool_config = None
        if tool_config:
            mode = tool_config.get("mode", "AUTO")
            gemini_tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=mode)
            )

        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            tools=[gemini_tool],
            tool_config=gemini_tool_config,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # Apply thinking config if provided (convert dict to types.ThinkingConfig)
        thinking = config.get("thinking")
        if thinking:
            if isinstance(thinking, dict):
                gen_config.thinking_config = types.ThinkingConfig(**thinking)
            else:
                gen_config.thinking_config = thinking

        response = self._call_with_retry(
            lambda: self._client.models.generate_content(
                model=model,
                contents=messages,
                config=gen_config,
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
        """Build Gemini-native tool response Content.

        Args:
            tool_results: List of (tool_call_id, function_name, result_json_str).

        Returns:
            types.Content with function response parts.
        """
        parts = []
        for _call_id, fn_name, result_str in tool_results:
            # Parse result as dict if possible, otherwise wrap
            try:
                result_dict = json.loads(result_str) if isinstance(result_str, str) else result_str
            except (json.JSONDecodeError, TypeError):
                result_dict = {"result": str(result_str)}

            if not isinstance(result_dict, dict):
                result_dict = {"result": result_dict}

            parts.append(
                types.Part.from_function_response(
                    name=fn_name,
                    response=result_dict,
                )
            )
        return types.Content(role="user", parts=parts)

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build Gemini-native user Content.

        Args:
            text: User text.
            images: Optional list of (bytes, mime_type) tuples.

        Returns:
            types.Content with text and optional image parts.
        """
        parts = []
        if images:
            for img_data, mime_type in images:
                parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))
        parts.append(types.Part(text=text))
        return types.Content(role="user", parts=parts)

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """Query Gemini API for available models."""
        models = []
        try:
            for model in self._client.models.list():
                model_id = model.name
                # Strip "models/" prefix if present
                if model_id.startswith("models/"):
                    model_id = model_id[7:]

                # Determine capability tier from model name
                tier = "standard"
                if "flash" in model_id.lower() or "lite" in model_id.lower():
                    tier = "fast"
                elif "pro" in model_id.lower() or "ultra" in model_id.lower():
                    tier = "deep"

                # Check for function calling support
                supports_tools = False
                if hasattr(model, "supported_generation_methods"):
                    methods = model.supported_generation_methods or []
                    supports_tools = "generateContent" in methods

                # Get context window
                ctx = 0
                if hasattr(model, "input_token_limit"):
                    ctx = model.input_token_limit or 0

                models.append(ModelInfo(
                    id=model_id,
                    display_name=getattr(model, "display_name", model_id),
                    context_window=ctx,
                    supports_tools=supports_tools,
                    capability_tier=tier,
                ))
        except Exception as e:
            logger.warning("Gemini model listing failed: %s", e)

        return models

    def validate_model(self, model_id: str) -> bool:
        """Check if a Gemini model is accessible."""
        try:
            self._client.models.get(model=model_id)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Retry logic (preserved from orchestrator)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        err_str = str(exc).lower()
        return any(kw in err_str for kw in (
            "429", "resource_exhausted", "503", "service_unavailable", "overloaded"
        ))

    def _call_with_retry(self, call_fn, max_retries: int = 3, base_delay: float = 1.0):
        """Execute an API call with exponential backoff on transient errors."""
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return call_fn()
            except Exception as e:
                last_exc = e
                if attempt < max_retries and self._is_retryable_error(e):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Gemini API transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response) -> GenerateResult:
        """Convert a Gemini response to GenerateResult."""
        text = response.text if response.text else None
        tool_calls = []

        if response.function_calls:
            for fc in response.function_calls:
                tool_calls.append(ToolCall(
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                    id=str(uuid.uuid4()),
                ))

        # Extract usage metadata
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            usage["input_tokens"] = getattr(um, "prompt_token_count", 0) or 0
            usage["output_tokens"] = getattr(um, "candidates_token_count", 0) or 0

        # raw = the model's response content for conversation continuity
        raw = None
        if response.candidates and len(response.candidates) > 0:
            raw = response.candidates[0].content

        return GenerateResult(
            text=text,
            tool_calls=tool_calls,
            raw=raw,
            usage=usage,
        )
