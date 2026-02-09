"""
local-llm HTTP server — exposes the local GGUF model via FastAPI.

Endpoints:
    GET  /health               — liveness + readiness probe
    POST /v1/completions       — text completion (llama.cpp compatible)
    POST /v1/chat/completions  — OpenAI-compatible chat completions with tool support

The model is loaded once at startup from the path specified by
LOCAL_MODEL_PATH env var or from the lockfile default.

Fix Pack V8: Added chat completions endpoint with function calling support.
"""

import os
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local-llm")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_llm = None
_model_name = ""
_loaded_at = None


class CompletionRequest(BaseModel):
    prompt: str
    max_tokens: int = Field(default=128, ge=1, le=4096)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    stop: Optional[list] = None


class CompletionResponse(BaseModel):
    text: str
    model: str
    tokens_generated: int
    elapsed_ms: float


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ToolFunction(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolDeclaration(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None
    max_tokens: int = Field(default=512, ge=1, le=8192)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    tool_choice: Optional[str] = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

def _do_load_model():
    """Load the GGUF model into memory."""
    global _llm, _model_name, _loaded_at

    try:
        from llama_cpp import Llama
    except ImportError:
        logger.error("llama-cpp-python not installed")
        raise RuntimeError("llama-cpp-python is required")

    model_path = os.environ.get("LOCAL_MODEL_PATH")
    if not model_path:
        # Fall back to lockfile default
        try:
            from lockfile import load_lockfile, get_model_info
            data = load_lockfile()
            info = get_model_info(data)
            models_dir = os.environ.get("LOCAL_MODELS_DIR", "/home/llm/models")
            model_path = os.path.join(models_dir, info["filename"])
            _model_name = info["name"]
        except Exception as exc:
            logger.error(f"Failed to read lockfile: {exc}")
            raise

    if not os.path.exists(model_path):
        logger.error(f"Model file not found: {model_path}")
        raise FileNotFoundError(f"Model not found: {model_path}")

    n_ctx = int(os.environ.get("LOCAL_MODEL_CTX", "8192"))
    n_threads = int(os.environ.get("LOCAL_MODEL_THREADS", "4"))
    n_gpu = int(os.environ.get("LOCAL_MODEL_GPU_LAYERS", "28"))

    logger.info(f"Loading model: {model_path}")
    logger.info(f"Config: ctx={n_ctx}, threads={n_threads}, gpu_layers={n_gpu}")

    _llm = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_threads=n_threads,
        n_gpu_layers=n_gpu,
        verbose=False,
    )
    _loaded_at = time.time()
    logger.info(f"Model loaded: {_model_name or model_path}")


@asynccontextmanager
async def lifespan(a):
    _do_load_model()
    yield


app = FastAPI(title="Lancelot local-llm", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness + readiness probe for Docker HEALTHCHECK."""
    if _llm is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    uptime = time.time() - _loaded_at if _loaded_at else 0
    return {
        "status": "ok",
        "model": _model_name,
        "uptime_seconds": round(uptime, 1),
        "capabilities": ["completions", "chat_completions", "tool_calling"],
    }


@app.post("/v1/completions", response_model=CompletionResponse)
def completions(req: CompletionRequest):
    """Run text completion against the local model."""
    if _llm is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.monotonic()
    try:
        result = _llm(
            req.prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            stop=req.stop or ["\n\n"],
            echo=False,
        )
    except Exception as exc:
        logger.error("Inference error: %s", exc)
        raise HTTPException(status_code=500, detail="Model inference failed")

    text = result["choices"][0]["text"]
    elapsed = (time.monotonic() - start) * 1000
    tokens = result.get("usage", {}).get("completion_tokens", len(text.split()))

    return CompletionResponse(
        text=text,
        model=_model_name,
        tokens_generated=tokens,
        elapsed_ms=round(elapsed, 1),
    )


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    """OpenAI-compatible chat completions with tool/function calling support.

    Fix Pack V8: Qwen3-8B has native tool calling support. llama-cpp-python's
    create_chat_completion() handles the tool calling format automatically.
    """
    if _llm is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.monotonic()

    # Build kwargs for create_chat_completion
    kwargs = {
        "messages": req.messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    if req.tools:
        kwargs["tools"] = req.tools
    if req.tool_choice:
        kwargs["tool_choice"] = req.tool_choice

    try:
        result = _llm.create_chat_completion(**kwargs)
    except Exception as exc:
        logger.error("Chat completion error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Chat completion failed: {exc}",
        )

    elapsed = (time.monotonic() - start) * 1000

    # Add timing metadata
    if isinstance(result, dict):
        result["_elapsed_ms"] = round(elapsed, 1)
        result["_model"] = _model_name

    logger.info(
        "Chat completion: %d messages, tools=%s, elapsed=%.1fms",
        len(req.messages),
        "yes" if req.tools else "no",
        elapsed,
    )

    return result


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("LOCAL_LLM_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
