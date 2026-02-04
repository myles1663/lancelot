"""
Local model smoke test — validates inference correctness.

Runs a minimal inference pass through the local GGUF model to confirm
that the model loads, generates output, and produces coherent results
for each prompt in the utility suite.

Public API:
    run_smoke_test()       — full smoke test, returns SmokeResult
    quick_inference_check() — single-prompt sanity check, returns bool
"""

import time
import pathlib
from dataclasses import dataclass, field
from typing import Optional

from local_models.lockfile import load_lockfile, load_prompt_template
from local_models.fetch_model import model_path, is_model_present, FetchError


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PromptResult:
    """Result of a single prompt smoke test."""
    name: str
    passed: bool = False
    output: str = ""
    error: str = ""
    elapsed_ms: float = 0.0


@dataclass
class SmokeResult:
    """Aggregate smoke test result."""
    passed: bool = False
    model_name: str = ""
    model_loaded: bool = False
    prompt_results: list = field(default_factory=list)
    error: str = ""
    total_elapsed_ms: float = 0.0

    def summary(self):
        """Return a human-readable summary string."""
        if not self.passed:
            if self.error:
                return f"FAIL: {self.error}"
            failed = [r.name for r in self.prompt_results if not r.passed]
            return f"FAIL: {len(failed)} prompt(s) failed: {', '.join(failed)}"
        n = len(self.prompt_results)
        return f"OK: {n}/{n} prompts passed ({self.total_elapsed_ms:.0f}ms)"


# ---------------------------------------------------------------------------
# Smoke-test inputs — minimal examples for each utility prompt
# ---------------------------------------------------------------------------

_SMOKE_INPUTS = {
    "classify_intent": {
        "input": "What time does the store close?",
        "expect_contains": None,  # Any non-empty response is fine
        "expect_one_of": ["question", "command", "information",
                          "greeting", "feedback", "unclear"],
    },
    "extract_json": {
        "input": "John Smith, age 30, lives in New York.",
        "schema": '{"name": "string", "age": "number", "city": "string"}',
        "expect_contains": "John",
    },
    "summarize_internal": {
        "input": (
            "The quarterly revenue report shows a 15% increase in sales "
            "compared to last quarter. The marketing team attributes this "
            "growth to the new digital campaign launched in January. "
            "Customer acquisition cost decreased by 8%."
        ),
        "expect_contains": None,  # Any non-empty summary
    },
    "redact": {
        "input": "Contact John Smith at john@example.com or 555-123-4567.",
        "expect_contains": "[",  # Should have redaction brackets
    },
    "rag_rewrite": {
        "input": "how do I fix the thingy that's broken on my app",
        "expect_contains": None,  # Any non-empty rewrite
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_smoke_test(models_dir=None, lockfile_data=None):
    """Run the full smoke test suite.

    Loads the model, runs each prompt template with a test input,
    and validates the output.

    Args:
        models_dir: Override directory for model weights.
        lockfile_data: Pre-loaded lockfile dict (loads from disk if None).

    Returns:
        SmokeResult with per-prompt results and overall pass/fail.
    """
    result = SmokeResult()
    start = time.monotonic()

    if lockfile_data is None:
        lockfile_data = load_lockfile()

    result.model_name = lockfile_data["model"]["name"]

    # Check model is present
    if not is_model_present(models_dir=models_dir, lockfile_data=lockfile_data):
        result.error = "Model weights not found or checksum invalid"
        result.total_elapsed_ms = _elapsed_ms(start)
        return result

    # Try loading model
    try:
        llm = _load_model(models_dir=models_dir, lockfile_data=lockfile_data)
        result.model_loaded = True
    except Exception as exc:
        result.error = f"Failed to load model: {exc}"
        result.total_elapsed_ms = _elapsed_ms(start)
        return result

    # Run each prompt
    prompt_names = lockfile_data.get("prompts", [])
    for name in prompt_names:
        pr = _test_prompt(llm, name, lockfile_data)
        result.prompt_results.append(pr)

    result.passed = all(pr.passed for pr in result.prompt_results)
    result.total_elapsed_ms = _elapsed_ms(start)
    return result


def quick_inference_check(models_dir=None, lockfile_data=None):
    """Run a single fast inference check.

    Uses classify_intent with a trivial input. Returns True if the
    model loads and produces non-empty output, False otherwise.
    Designed for onboarding health checks where speed matters.
    """
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    if not is_model_present(models_dir=models_dir, lockfile_data=lockfile_data):
        return False

    try:
        llm = _load_model(models_dir=models_dir, lockfile_data=lockfile_data)
        prompt = "Classify: Hello\nCategory:"
        output = _generate(llm, prompt, max_tokens=16)
        return len(output.strip()) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_model(models_dir=None, lockfile_data=None):
    """Load the GGUF model via llama-cpp-python."""
    try:
        from llama_cpp import Llama
    except ImportError:
        raise RuntimeError(
            "llama-cpp-python is not installed. "
            "Install it with: pip install llama-cpp-python"
        )

    path = model_path(models_dir=models_dir, lockfile_data=lockfile_data)
    rt = lockfile_data["runtime"]

    return Llama(
        model_path=str(path),
        n_ctx=rt.get("context_length", 4096),
        n_threads=rt.get("threads", 4),
        n_gpu_layers=rt.get("gpu_layers", 0),
        verbose=False,
    )


def _generate(llm, prompt, max_tokens=128):
    """Run inference and return the generated text."""
    output = llm(
        prompt,
        max_tokens=max_tokens,
        temperature=0.1,
        stop=["\n\n"],
        echo=False,
    )
    return output["choices"][0]["text"]


def _test_prompt(llm, prompt_name, lockfile_data):
    """Test a single prompt template and return a PromptResult."""
    pr = PromptResult(name=prompt_name, passed=False)
    start = time.monotonic()

    smoke_input = _SMOKE_INPUTS.get(prompt_name)
    if smoke_input is None:
        pr.error = f"No smoke test input defined for prompt '{prompt_name}'"
        pr.elapsed_ms = _elapsed_ms(start)
        return pr

    try:
        template = load_prompt_template(prompt_name)
        filled = template.format(**{
            k: v for k, v in smoke_input.items()
            if k not in ("expect_contains", "expect_one_of")
        })
        output = _generate(llm, filled, max_tokens=128)
        pr.output = output.strip()

        # Validate output
        if len(pr.output) == 0:
            pr.error = "Empty output"
        elif smoke_input.get("expect_contains") and \
                smoke_input["expect_contains"].lower() not in pr.output.lower():
            pr.error = (
                f"Expected output to contain '{smoke_input['expect_contains']}'"
            )
        elif smoke_input.get("expect_one_of"):
            found = any(
                opt.lower() in pr.output.lower()
                for opt in smoke_input["expect_one_of"]
            )
            if not found:
                pr.error = (
                    f"Expected one of {smoke_input['expect_one_of']} in output"
                )
            else:
                pr.passed = True
        else:
            pr.passed = True

    except Exception as exc:
        pr.error = str(exc)

    pr.elapsed_ms = _elapsed_ms(start)
    return pr


def _elapsed_ms(start):
    return (time.monotonic() - start) * 1000
