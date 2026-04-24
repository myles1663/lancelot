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
import re
import json
import time
import uuid
import shlex
import logging
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local-llm")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_llm = None
_model_name = ""
_model_profile = ""
_model_path = ""
_engine = ""
_llama_server_process = None
_llama_server_url = ""
_loaded_at = None
_llm_lock = threading.Lock()
_readiness_lock = threading.Lock()
_readiness_stop = threading.Event()
_readiness_thread = None
_readiness = {
    "loaded": False,
    "ready": False,
    "status": "starting",
    "last_verified_at": None,
    "last_checked_at": None,
    "last_error": None,
    "consecutive_failures": 0,
    "last_smoke_elapsed_ms": None,
}
_READINESS_PROMPT = "Classify: hello\nCategory:"
_READINESS_MAX_TOKENS = 12
_READINESS_INTERVAL_S = int(os.environ.get("LOCAL_MODEL_READINESS_INTERVAL_S", "30"))
_CONTEXT_WINDOW_ERROR_RE = re.compile(
    r"(requested tokens .*exceed context window|context window|n_ctx)",
    re.IGNORECASE,
)
_ENGINE_LLAMA_CPP_PYTHON = "llama_cpp_python"
_ENGINE_PRISM_LLAMA_SERVER = "prism_llama_server"


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
    """Load the selected GGUF profile into the configured runtime."""
    global _loaded_at

    model_path, runtime = _resolve_model_runtime()
    if not os.path.exists(model_path):
        logger.error("Model file not found: %s", model_path)
        raise FileNotFoundError(f"Model not found: {model_path}")

    if _engine == _ENGINE_PRISM_LLAMA_SERVER:
        _start_llama_server_backend(model_path, runtime)
    else:
        _load_llama_cpp_python(model_path, runtime)

    _loaded_at = time.time()
    with _readiness_lock:
        _readiness["loaded"] = True
        _readiness["status"] = "loaded"
    logger.info("Model runtime loaded: model=%s engine=%s", _model_name, _engine)


def _resolve_model_runtime() -> tuple[str, dict]:
    global _model_name, _model_profile, _model_path, _engine

    model_path = os.environ.get("LOCAL_MODEL_PATH")
    runtime: dict[str, Any] = {
        "engine": _ENGINE_LLAMA_CPP_PYTHON,
        "context_length": 4096,
        "threads": 4,
        "gpu_layers": 0,
    }
    profile_name = os.environ.get("LOCAL_MODEL_PROFILE")

    if not model_path:
        try:
            from lockfile import load_lockfile, get_model_info
            data = load_lockfile()
            info = get_model_info(data, profile_name=profile_name)
            models_dir = os.environ.get("LOCAL_MODELS_DIR", "/home/llm/models")
            model_path = os.path.join(models_dir, info["filename"])
            _model_name = os.environ.get("LOCAL_LLM_MODEL") or info["name"]
            _model_profile = info.get("profile") or info["name"]
            runtime.update(info.get("runtime") or {})
        except Exception as exc:
            logger.error("Failed to read lockfile: %s", exc)
            raise
    else:
        _model_name = os.environ.get("LOCAL_LLM_MODEL") or os.path.basename(model_path)
        _model_profile = profile_name or _model_name

    _engine = _normalize_engine(os.environ.get("LOCAL_LLM_ENGINE") or runtime["engine"])
    runtime["engine"] = _engine
    runtime["context_length"] = int(
        os.environ.get("LOCAL_MODEL_CTX", str(runtime.get("context_length", 4096)))
    )
    runtime["threads"] = int(
        os.environ.get("LOCAL_MODEL_THREADS", str(runtime.get("threads", 4)))
    )
    runtime["gpu_layers"] = int(
        os.environ.get("LOCAL_MODEL_GPU_LAYERS", str(runtime.get("gpu_layers", 0)))
    )
    _model_path = model_path
    return model_path, runtime


def _normalize_engine(engine: str) -> str:
    normalized = str(engine or "").strip().lower().replace("-", "_").replace(".", "_")
    if normalized in {"llama_cpp", "llama_cpp_python", "llamacpp"}:
        return _ENGINE_LLAMA_CPP_PYTHON
    if normalized in {"prism", "prism_llama_server", "llama_server"}:
        return _ENGINE_PRISM_LLAMA_SERVER
    raise RuntimeError(f"Unsupported local LLM engine: {engine}")


def _load_llama_cpp_python(model_path: str, runtime: dict) -> None:
    global _llm
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        logger.error("llama-cpp-python not installed")
        raise RuntimeError(
            "llama-cpp-python is required for the default local model service. "
            "Use LOCAL_LLM_ENGINE=prism_llama_server with Dockerfile.prism for "
            "portable Prism Bonsai smoke tests or Dockerfile.prism.cuda for "
            "GPU-backed production Bonsai profiles."
        ) from exc

    logger.info("Loading llama-cpp-python model: %s", model_path)
    logger.info(
        "Config: ctx=%s, threads=%s, gpu_layers=%s",
        runtime["context_length"],
        runtime["threads"],
        runtime["gpu_layers"],
    )

    _llm = Llama(
        model_path=model_path,
        n_ctx=runtime["context_length"],
        n_threads=runtime["threads"],
        n_gpu_layers=runtime["gpu_layers"],
        verbose=False,
    )


def _start_llama_server_backend(model_path: str, runtime: dict) -> None:
    """Start the Prism llama.cpp server and proxy through this FastAPI wrapper."""
    global _llm, _llama_server_process, _llama_server_url

    server_bin = os.environ.get("LOCAL_LLM_SERVER_BIN", "/usr/local/bin/llama-server")
    backend_host = os.environ.get("LOCAL_LLM_BACKEND_HOST", "127.0.0.1")
    backend_port = int(os.environ.get("LOCAL_LLM_BACKEND_PORT", "8091"))
    _llama_server_url = f"http://{backend_host}:{backend_port}"
    extra_args = shlex.split(os.environ.get("LOCAL_LLM_SERVER_EXTRA_ARGS", ""))
    command = [
        server_bin,
        "-m", model_path,
        "--host", backend_host,
        "--port", str(backend_port),
        "-c", str(runtime["context_length"]),
        "-t", str(runtime["threads"]),
        "-ngl", str(runtime["gpu_layers"]),
        *extra_args,
    ]
    logger.info("Starting Prism llama-server backend: %s", shlex.join(command))
    _llama_server_process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _llm = {"engine": _ENGINE_PRISM_LLAMA_SERVER}
    threading.Thread(
        target=_forward_llama_server_logs,
        daemon=True,
        name="local-llm-prism-logs",
    ).start()


def _forward_llama_server_logs() -> None:
    proc = _llama_server_process
    if proc is None or proc.stdout is None:
        return
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            logger.info("llama-server: %s", line)


def _stop_llama_server_backend() -> None:
    global _llama_server_process
    proc = _llama_server_process
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _llama_server_process = None


def _update_readiness(
    *,
    ready: bool,
    checked_at: str,
    error: str | None = None,
    elapsed_ms: float | None = None,
) -> None:
    with _readiness_lock:
        _readiness["last_checked_at"] = checked_at
        _readiness["last_smoke_elapsed_ms"] = elapsed_ms
        _readiness["ready"] = bool(ready)
        if ready:
            _readiness["status"] = "ready"
            _readiness["last_verified_at"] = checked_at
            _readiness["last_error"] = None
            _readiness["consecutive_failures"] = 0
        else:
            _readiness["status"] = "loaded_not_ready" if _readiness["loaded"] else "unavailable"
            _readiness["last_error"] = error or "Inference smoke failed"
            _readiness["consecutive_failures"] += 1


def _run_readiness_smoke() -> bool:
    if _llm is None:
        raise RuntimeError("Model not loaded")
    start = time.monotonic()
    result = _run_completion(
        _READINESS_PROMPT,
        max_tokens=_READINESS_MAX_TOKENS,
        temperature=0.0,
        stop=["\n\n"],
    )
    text = _completion_text(result).strip()
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    elapsed_ms = round((time.monotonic() - start) * 1000, 1)
    if not text:
        _update_readiness(
            ready=False,
            checked_at=checked_at,
            error="Inference smoke returned empty output",
            elapsed_ms=elapsed_ms,
        )
        return False
    _update_readiness(ready=True, checked_at=checked_at, elapsed_ms=elapsed_ms)
    return True


def _readiness_loop() -> None:
    while not _readiness_stop.wait(_READINESS_INTERVAL_S):
        try:
            _run_readiness_smoke()
        except Exception as exc:
            checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            logger.error("Local readiness smoke failed: %s", exc)
            _update_readiness(
                ready=False,
                checked_at=checked_at,
                error=str(exc),
            )


def _start_readiness_monitor() -> None:
    global _readiness_thread
    if _readiness_thread is not None and _readiness_thread.is_alive():
        return
    _readiness_stop.clear()
    _readiness_thread = threading.Thread(
        target=_readiness_loop,
        daemon=True,
        name="local-llm-readiness",
    )
    _readiness_thread.start()


def _stop_readiness_monitor() -> None:
    global _readiness_thread
    _readiness_stop.set()
    if _readiness_thread is not None:
        _readiness_thread.join(timeout=5)
        _readiness_thread = None


def _readiness_payload() -> dict:
    with _readiness_lock:
        payload = dict(_readiness)
    uptime = time.time() - _loaded_at if _loaded_at else 0
    payload.update({
        "model": _model_name,
        "profile": _model_profile,
        "engine": _engine or _ENGINE_LLAMA_CPP_PYTHON,
        "uptime_seconds": round(uptime, 1),
        "capabilities": ["completions", "chat_completions", "tool_calling"],
    })
    if _llama_server_url:
        payload["backend_url"] = _llama_server_url
    if payload["ready"]:
        payload["status"] = "ok"
    elif payload["loaded"]:
        payload["status"] = "degraded"
    else:
        payload["status"] = "unavailable"
    return payload


def _ensure_ready_for_inference() -> None:
    if _llm is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    payload = _readiness_payload()
    if not payload["ready"]:
        detail = payload.get("last_error") or "Local model not ready for inference"
        raise HTTPException(status_code=503, detail=detail)


def _is_context_window_error(exc: Exception) -> bool:
    return bool(_CONTEXT_WINDOW_ERROR_RE.search(str(exc)))


def _run_completion(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    stop: Optional[list] = None,
) -> dict:
    if _engine == _ENGINE_PRISM_LLAMA_SERVER:
        return _post_backend_json(
            "/v1/completions",
            {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop": stop or ["\n\n"],
            },
        )

    with _llm_lock:
        return _llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop or ["\n\n"],
            echo=False,
        )


def _run_chat_completion(payload: dict) -> dict:
    if _engine == _ENGINE_PRISM_LLAMA_SERVER:
        return _post_backend_json("/v1/chat/completions", payload)

    with _llm_lock:
        return _llm.create_chat_completion(**payload)


def _post_backend_json(path: str, payload: dict) -> dict:
    if _llama_server_process is not None and _llama_server_process.poll() is not None:
        raise RuntimeError(
            f"llama-server exited with code {_llama_server_process.returncode}"
        )

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_llama_server_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = float(os.environ.get("LOCAL_LLM_BACKEND_TIMEOUT_S", "120"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"llama-server HTTP {exc.code} for {path}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"llama-server unavailable for {path}: {exc}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"llama-server returned non-JSON response for {path}: {raw[:500]}"
        ) from exc


def _completion_text(result: dict) -> str:
    choices = result.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    if "text" in first:
        return str(first.get("text") or "")
    message = first.get("message") or {}
    return str(message.get("content") or "")


@asynccontextmanager
async def lifespan(a):
    _do_load_model()
    try:
        _run_readiness_smoke()
    except Exception as exc:
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.error("Initial local readiness smoke failed: %s", exc)
        _update_readiness(ready=False, checked_at=checked_at, error=str(exc))
    _start_readiness_monitor()
    yield
    _stop_readiness_monitor()
    _stop_llama_server_backend()


app = FastAPI(title="Lancelot local-llm", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Readiness probe backed by real inference smoke, not just model load state."""
    payload = _readiness_payload()
    if not payload["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.post("/v1/completions", response_model=CompletionResponse)
def completions(req: CompletionRequest):
    """Run text completion against the local model."""
    _ensure_ready_for_inference()

    start = time.monotonic()
    try:
        result = _run_completion(
            req.prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            stop=req.stop,
        )
    except Exception as exc:
        if _is_context_window_error(exc):
            logger.warning("Context window exceeded during completion: %s", exc)
            raise HTTPException(status_code=422, detail="Context window exceeded")
        logger.error("Inference error: %s", exc)
        raise HTTPException(status_code=500, detail="Model inference failed")

    text = _completion_text(result)
    elapsed = (time.monotonic() - start) * 1000
    tokens = result.get("usage", {}).get("completion_tokens", len(text.split()))

    return CompletionResponse(
        text=text,
        model=_model_name,
        tokens_generated=tokens,
        elapsed_ms=round(elapsed, 1),
    )


def _strip_think_tags(text: str) -> str:
    """Remove Qwen3 <think>...</think> reasoning blocks from output."""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()


def _extract_tool_calls(text: str) -> tuple:
    """Parse Qwen3 <tool_call> tags into OpenAI-format tool_calls.

    Returns (clean_content, tool_calls_list).
    If no tool calls found, tool_calls_list is None.
    """
    pattern = r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>"
    matches = re.findall(pattern, text)
    if not matches:
        return text, None

    tool_calls = []
    for match in matches:
        try:
            parsed = json.loads(match)
            name = parsed.get("name", "")
            args = parsed.get("arguments", {})
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            })
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_call JSON: %s", match)
            continue

    # Remove tool_call tags from content
    clean = re.sub(r"<tool_call>[\s\S]*?</tool_call>\s*", "", text).strip()
    return clean, tool_calls if tool_calls else None


def _postprocess_chat_result(result: dict) -> dict:
    """Post-process llama-cpp-python output for Qwen3 compatibility.

    1. Strip <think> reasoning tags from content
    2. Convert <tool_call> tags to OpenAI-format tool_calls array
    """
    if not isinstance(result, dict):
        return result

    for choice in result.get("choices", []):
        msg = choice.get("message", {})
        content = msg.get("content", "")
        if not content:
            continue

        # Strip thinking tokens
        content = _strip_think_tags(content)

        # Extract tool calls from content
        content, tool_calls = _extract_tool_calls(content)

        msg["content"] = content if content else None
        if tool_calls:
            msg["tool_calls"] = tool_calls
            choice["finish_reason"] = "tool_calls"

    return result


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    """OpenAI-compatible chat completions with tool/function calling support.

    Fix Pack V8: Qwen3-8B outputs tool calls as <tool_call> XML tags and
    reasoning as <think> tags. This endpoint post-processes the output to
    convert to standard OpenAI format.
    """
    _ensure_ready_for_inference()

    start = time.monotonic()

    # Inject /no_think into system message to suppress chain-of-thought
    messages = list(req.messages)

    # Sanitize: replace None content with "" to prevent TypeError in
    # llama-cpp-python and our own /no_think check. OpenAI tool-call
    # assistant messages legitimately have content=None.
    for msg in messages:
        if msg.get("content") is None:
            msg["content"] = ""

    if messages and messages[0].get("role") == "system":
        sys_content = messages[0].get("content", "")
        if "/no_think" not in sys_content:
            messages[0] = {**messages[0], "content": sys_content + "\n/no_think"}
    else:
        messages.insert(0, {"role": "system", "content": "/no_think"})

    # Build kwargs for create_chat_completion
    kwargs = {
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    if req.tools:
        kwargs["tools"] = req.tools
        # Default to "required" when tools are provided to force tool usage
        kwargs["tool_choice"] = req.tool_choice or "auto"
    if req.tool_choice:
        kwargs["tool_choice"] = req.tool_choice

    try:
        result = _run_chat_completion(kwargs)
    except Exception as exc:
        logger.error("Chat completion error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Chat completion failed: {exc}",
        )

    elapsed = (time.monotonic() - start) * 1000

    # Post-process: strip <think> tags, convert <tool_call> to OpenAI format
    result = _postprocess_chat_result(result)

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
