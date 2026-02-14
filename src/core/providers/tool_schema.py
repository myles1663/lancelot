"""
Tool Schema Normalizer — provider-agnostic tool declarations (v8.3.0).

Tools are defined once in NormalizedToolDeclaration format (JSON Schema params).
Each provider client converts to its native format via the converter functions.

Public API:
    NormalizedToolDeclaration
    to_gemini_declarations(tools)   → list[types.FunctionDeclaration]
    to_openai_tools(tools)          → list[dict]
    to_anthropic_tools(tools)       → list[dict]
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class NormalizedToolDeclaration:
    """Provider-agnostic tool/function declaration.

    Parameters use JSON Schema format, which is the common denominator
    across Gemini, OpenAI, and Anthropic APIs.

    Example:
        NormalizedToolDeclaration(
            name="network_client",
            description="Make an HTTP request",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                    "method": {"type": "string", "enum": ["GET", "POST"]},
                },
                "required": ["url"],
            }
        )
    """
    name: str
    description: str
    parameters: dict


# ---------------------------------------------------------------------------
# Gemini conversion
# ---------------------------------------------------------------------------

def to_gemini_declarations(tools: list[NormalizedToolDeclaration]) -> list:
    """Convert normalized declarations to Gemini FunctionDeclaration objects."""
    from google.genai import types

    declarations = []
    for tool in tools:
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
        )
    return declarations


# ---------------------------------------------------------------------------
# OpenAI conversion
# ---------------------------------------------------------------------------

def to_openai_tools(tools: list[NormalizedToolDeclaration]) -> list[dict]:
    """Convert normalized declarations to OpenAI tools format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


# ---------------------------------------------------------------------------
# Anthropic conversion
# ---------------------------------------------------------------------------

def to_anthropic_tools(tools: list[NormalizedToolDeclaration]) -> list[dict]:
    """Convert normalized declarations to Anthropic tools format."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]
