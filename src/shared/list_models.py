"""On-demand Gemini model listing utility.

This module must remain side-effect free at import time. Importing it should
never touch the network or the Gemini client; listing only happens when
`list_gemini_models()` or `main()` is called explicitly.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


def _normalize_methods(methods: Optional[Iterable[str]]) -> set[str]:
    return {str(method or "").strip() for method in (methods or []) if str(method or "").strip()}


def list_gemini_models(
    api_key: Optional[str] = None,
    *,
    genai_module=None,
) -> List[str]:
    """Return Gemini models that support `generateContent`.

    Args:
        api_key: Explicit Gemini API key. Falls back to `GEMINI_API_KEY`.
        genai_module: Optional injected `google.generativeai` module for tests.

    Raises:
        ValueError: If no API key is configured.
        Exception: Any provider/client failure from the Gemini SDK.
    """
    resolved_api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not resolved_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    if genai_module is None:
        import google.generativeai as genai_module

    genai_module.configure(api_key=resolved_api_key)

    model_names: List[str] = []
    for model in genai_module.list_models():
        methods = _normalize_methods(getattr(model, "supported_generation_methods", []))
        if "generateContent" not in methods:
            continue
        name = str(getattr(model, "name", "")).strip()
        if name:
            model_names.append(name)
    return model_names


def main() -> int:
    """CLI entrypoint for manual operator use."""
    try:
        model_names = list_gemini_models()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("Failed to list Gemini models: %s", exc)
        return 1

    for model_name in model_names:
        sys.stdout.write(f"{model_name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
