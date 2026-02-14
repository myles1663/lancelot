"""
ProviderClient — abstract base class for LLM provider adapters (v8.3.0).

Defines the normalized interface that all provider clients must implement.
The orchestrator calls through this interface instead of provider-specific SDKs.

Public API:
    ToolCall          — normalized tool/function call from any provider
    GenerateResult    — normalized generation result
    ModelInfo         — discovered model metadata
    ProviderClient    — abstract base class
"""

import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """Normalized tool/function call from any provider."""
    name: str
    args: dict
    id: str = ""  # provider-assigned ID for response matching

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())


@dataclass
class GenerateResult:
    """Normalized generation result from any provider.

    Attributes:
        text: The generated text (None if only tool calls returned).
        tool_calls: List of tool/function calls requested by the model.
        raw: Provider-specific response object for conversation continuity.
        usage: Token usage dict {"input_tokens": N, "output_tokens": N}.
    """
    text: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class ModelInfo:
    """Discovered model metadata from a provider API."""
    id: str
    display_name: str
    context_window: int = 0
    supports_tools: bool = False
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    capability_tier: str = "standard"  # "fast" | "standard" | "deep"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "context_window": self.context_window,
            "supports_tools": self.supports_tools,
            "input_cost_per_1k": self.input_cost_per_1k,
            "output_cost_per_1k": self.output_cost_per_1k,
            "capability_tier": self.capability_tier,
        }


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class ProviderClient(ABC):
    """Abstract base class for LLM provider adapters.

    Each provider (Gemini, OpenAI, Anthropic) implements this interface
    so the orchestrator can work with any of them transparently.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'gemini', 'openai', 'anthropic')."""
        ...

    @abstractmethod
    def generate(
        self,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ) -> GenerateResult:
        """Generate a text response (no tool calling).

        Args:
            model: Model identifier (e.g. "gemini-2.0-flash").
            messages: Provider-native message list (built via build_user_message).
            system_instruction: System-level instruction text.
            config: Provider-specific config overrides (e.g. {"thinking": ...}).

        Returns:
            GenerateResult with text and usage.
        """
        ...

    @abstractmethod
    def generate_with_tools(
        self,
        model: str,
        messages: list,
        system_instruction: str,
        tools: list,
        tool_config: Optional[dict] = None,
        config: Optional[dict] = None,
    ) -> GenerateResult:
        """Generate with function/tool calling support.

        Args:
            model: Model identifier.
            messages: Provider-native message list.
            system_instruction: System-level instruction text.
            tools: List of NormalizedToolDeclaration objects.
            tool_config: Tool calling config (e.g. {"mode": "ANY"} or {"mode": "AUTO"}).
            config: Provider-specific config overrides.

        Returns:
            GenerateResult with text and/or tool_calls.
        """
        ...

    @abstractmethod
    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        """Build a provider-native message containing tool/function results.

        Args:
            tool_results: List of (tool_call_id, function_name, result_json_str) tuples.

        Returns:
            Provider-native message object to append to the conversation.
        """
        ...

    @abstractmethod
    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        """Build a provider-native user message.

        Args:
            text: The user's text content.
            images: Optional list of image data (bytes, mime_type pairs).

        Returns:
            Provider-native message object.
        """
        ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Query the provider API for available models.

        Returns:
            List of ModelInfo objects describing available models.
        """
        ...

    @abstractmethod
    def validate_model(self, model_id: str) -> bool:
        """Check if a specific model is accessible.

        Returns:
            True if the model exists and is usable.
        """
        ...
