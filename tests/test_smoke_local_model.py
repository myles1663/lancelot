"""
Tests for local_models.smoke_test — local model smoke testing.
Prompt 10: smoke_test.py.

Unit tests run always. Integration tests marked @local_model are
auto-skipped when model weights or llama-cpp-python are absent.
"""

import hashlib
import pathlib
import pytest
from unittest.mock import MagicMock, patch

from local_models.smoke_test import (
    run_smoke_test,
    quick_inference_check,
    SmokeResult,
    PromptResult,
    _SMOKE_INPUTS,
)
from local_models.lockfile import load_lockfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lockfile_data():
    """Valid lockfile dict for testing."""
    return {
        "model": {
            "name": "test-model",
            "version": "1.0",
            "quantization": "Q4_K_M",
            "format": "gguf",
            "filename": "test.gguf",
            "size_mb": 100,
            "checksum": {
                "algorithm": "sha256",
                "hash": "a" * 64,
            },
            "sources": [
                {"url": "http://localhost/test.gguf", "provider": "test"}
            ],
            "license": {"model": "Apache-2.0", "runtime": "MIT"},
        },
        "runtime": {
            "engine": "llama.cpp",
            "context_length": 4096,
            "threads": 4,
            "gpu_layers": 0,
        },
        "prompts": ["classify_intent"],
    }


def _mock_llm_response(text="question"):
    """Return a mock Llama response dict."""
    return {"choices": [{"text": text}]}


# ===================================================================
# SmokeResult
# ===================================================================

class TestSmokeResult:

    def test_default_is_not_passed(self):
        r = SmokeResult()
        assert r.passed is False
        assert r.model_loaded is False
        assert r.prompt_results == []

    def test_summary_on_success(self):
        r = SmokeResult(
            passed=True,
            prompt_results=[
                PromptResult(name="a", passed=True),
                PromptResult(name="b", passed=True),
            ],
            total_elapsed_ms=150.0,
        )
        assert "OK" in r.summary()
        assert "2/2" in r.summary()

    def test_summary_on_failure_with_error(self):
        r = SmokeResult(passed=False, error="Model not found")
        assert "FAIL" in r.summary()
        assert "Model not found" in r.summary()

    def test_summary_on_failure_with_prompt_failures(self):
        r = SmokeResult(
            passed=False,
            prompt_results=[
                PromptResult(name="good", passed=True),
                PromptResult(name="bad", passed=False),
            ],
        )
        s = r.summary()
        assert "FAIL" in s
        assert "bad" in s

    def test_elapsed_ms_in_summary(self):
        r = SmokeResult(
            passed=True,
            prompt_results=[PromptResult(name="x", passed=True)],
            total_elapsed_ms=42.5,
        )
        assert "42" in r.summary()


# ===================================================================
# PromptResult
# ===================================================================

class TestPromptResult:

    def test_default_not_passed(self):
        pr = PromptResult(name="test")
        assert pr.passed is False
        assert pr.output == ""
        assert pr.error == ""

    def test_fields_settable(self):
        pr = PromptResult(
            name="classify_intent",
            passed=True,
            output="question",
            elapsed_ms=50.0,
        )
        assert pr.name == "classify_intent"
        assert pr.passed is True


# ===================================================================
# Smoke inputs defined for all prompts
# ===================================================================

class TestSmokeInputs:

    def test_all_lockfile_prompts_have_smoke_inputs(self):
        data = load_lockfile()
        for name in data["prompts"]:
            assert name in _SMOKE_INPUTS, \
                f"Missing smoke test input for prompt '{name}'"

    def test_classify_intent_has_expect_one_of(self):
        inp = _SMOKE_INPUTS["classify_intent"]
        assert "expect_one_of" in inp
        assert "question" in inp["expect_one_of"]

    def test_extract_json_has_schema(self):
        inp = _SMOKE_INPUTS["extract_json"]
        assert "schema" in inp
        assert "input" in inp

    def test_redact_expects_bracket(self):
        inp = _SMOKE_INPUTS["redact"]
        assert inp["expect_contains"] == "["

    def test_all_inputs_have_input_field(self):
        for name, inp in _SMOKE_INPUTS.items():
            assert "input" in inp, f"Smoke input for '{name}' missing 'input'"


# ===================================================================
# run_smoke_test — model not present (unit, no model needed)
# ===================================================================

class TestRunSmokeTestNoModel:

    def test_fails_when_model_missing(self, tmp_path):
        data = _make_lockfile_data()
        result = run_smoke_test(models_dir=tmp_path, lockfile_data=data)
        assert result.passed is False
        assert "not found" in result.error.lower() or "checksum" in result.error.lower()
        assert result.model_loaded is False

    def test_model_name_set_even_on_failure(self, tmp_path):
        data = _make_lockfile_data()
        result = run_smoke_test(models_dir=tmp_path, lockfile_data=data)
        assert result.model_name == "test-model"

    def test_elapsed_ms_is_non_negative(self, tmp_path):
        data = _make_lockfile_data()
        result = run_smoke_test(models_dir=tmp_path, lockfile_data=data)
        assert result.total_elapsed_ms >= 0


# ===================================================================
# run_smoke_test — with mocked model (unit, no model needed)
# ===================================================================

class TestRunSmokeTestMocked:

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_passes_when_model_generates_valid_output(
        self, mock_load, mock_present
    ):
        mock_llm = MagicMock()
        mock_llm.return_value = _mock_llm_response("question")
        mock_load.return_value = mock_llm

        data = _make_lockfile_data()
        result = run_smoke_test(lockfile_data=data)

        assert result.model_loaded is True
        assert result.passed is True
        assert len(result.prompt_results) == 1
        assert result.prompt_results[0].name == "classify_intent"
        assert result.prompt_results[0].passed is True

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_fails_when_model_returns_empty(self, mock_load, mock_present):
        mock_llm = MagicMock()
        mock_llm.return_value = _mock_llm_response("")
        mock_load.return_value = mock_llm

        data = _make_lockfile_data()
        result = run_smoke_test(lockfile_data=data)

        assert result.passed is False
        assert result.prompt_results[0].error == "Empty output"

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_fails_when_model_load_raises(self, mock_load, mock_present):
        mock_load.side_effect = RuntimeError("CUDA out of memory")

        data = _make_lockfile_data()
        result = run_smoke_test(lockfile_data=data)

        assert result.passed is False
        assert "Failed to load" in result.error
        assert result.model_loaded is False

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_fails_when_inference_raises(self, mock_load, mock_present):
        mock_llm = MagicMock()
        mock_llm.side_effect = RuntimeError("inference error")
        mock_load.return_value = mock_llm

        data = _make_lockfile_data()
        result = run_smoke_test(lockfile_data=data)

        assert result.passed is False
        assert result.prompt_results[0].passed is False
        assert "inference error" in result.prompt_results[0].error

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_classify_intent_accepts_valid_category(
        self, mock_load, mock_present
    ):
        for category in ["question", "command", "greeting", "unclear"]:
            mock_llm = MagicMock()
            mock_llm.return_value = _mock_llm_response(category)
            mock_load.return_value = mock_llm

            data = _make_lockfile_data()
            result = run_smoke_test(lockfile_data=data)
            assert result.prompt_results[0].passed is True, \
                f"Should accept category '{category}'"

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_classify_intent_rejects_invalid_category(
        self, mock_load, mock_present
    ):
        mock_llm = MagicMock()
        mock_llm.return_value = _mock_llm_response("banana_phone")
        mock_load.return_value = mock_llm

        data = _make_lockfile_data()
        result = run_smoke_test(lockfile_data=data)
        assert result.prompt_results[0].passed is False
        assert "Expected one of" in result.prompt_results[0].error


# ===================================================================
# run_smoke_test — multi-prompt (unit, mocked)
# ===================================================================

class TestRunSmokeTestMultiPrompt:

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_all_five_prompts_tested(self, mock_load, mock_present):
        responses = {
            "classify_intent": "question",
            "extract_json": '{"name": "John", "age": 30, "city": "New York"}',
            "summarize_internal": "Revenue grew 15% due to digital marketing.",
            "redact": "Contact [NAME] at [EMAIL] or [PHONE].",
            "rag_rewrite": "application error troubleshooting steps",
        }

        call_count = [0]
        def mock_inference(prompt, **kwargs):
            # Determine which prompt is being called based on content
            for key, resp in responses.items():
                if key == "classify_intent" and "category" in prompt.lower():
                    return _mock_llm_response(resp)
                elif key == "extract_json" and "json" in prompt.lower():
                    return _mock_llm_response(resp)
                elif key == "summarize_internal" and "summarize" in prompt.lower():
                    return _mock_llm_response(resp)
                elif key == "redact" and "redact" in prompt.lower():
                    return _mock_llm_response(resp)
                elif key == "rag_rewrite" and "rewrite" in prompt.lower():
                    return _mock_llm_response(resp)
            return _mock_llm_response("fallback response with [ bracket")

        mock_llm = MagicMock()
        mock_llm.side_effect = mock_inference
        mock_load.return_value = mock_llm

        data = load_lockfile()  # Use real lockfile with all 5 prompts
        result = run_smoke_test(lockfile_data=data)

        assert len(result.prompt_results) == 5
        names = {pr.name for pr in result.prompt_results}
        assert names == {
            "classify_intent", "extract_json", "summarize_internal",
            "redact", "rag_rewrite",
        }

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_partial_failure_marks_overall_fail(self, mock_load, mock_present):
        call_idx = [0]
        def mixed_response(prompt, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:
                return _mock_llm_response("question")  # pass
            return _mock_llm_response("")  # fail (empty)

        mock_llm = MagicMock()
        mock_llm.side_effect = mixed_response
        mock_load.return_value = mock_llm

        data = _make_lockfile_data()
        data["prompts"] = ["classify_intent", "summarize_internal"]
        result = run_smoke_test(lockfile_data=data)

        assert result.passed is False
        assert result.prompt_results[0].passed is True
        assert result.prompt_results[1].passed is False


# ===================================================================
# quick_inference_check — unit tests (mocked)
# ===================================================================

class TestQuickInferenceCheck:

    @patch("local_models.smoke_test.is_model_present", return_value=False)
    def test_returns_false_when_model_missing(self, mock_present):
        assert quick_inference_check(lockfile_data=_make_lockfile_data()) is False

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_returns_true_on_valid_output(self, mock_load, mock_present):
        mock_llm = MagicMock()
        mock_llm.return_value = _mock_llm_response("greeting")
        mock_load.return_value = mock_llm

        assert quick_inference_check(lockfile_data=_make_lockfile_data()) is True

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_returns_false_on_empty_output(self, mock_load, mock_present):
        mock_llm = MagicMock()
        mock_llm.return_value = _mock_llm_response("   ")
        mock_load.return_value = mock_llm

        assert quick_inference_check(lockfile_data=_make_lockfile_data()) is False

    @patch("local_models.smoke_test.is_model_present", return_value=True)
    @patch("local_models.smoke_test._load_model")
    def test_returns_false_on_exception(self, mock_load, mock_present):
        mock_load.side_effect = RuntimeError("crash")
        assert quick_inference_check(lockfile_data=_make_lockfile_data()) is False


# ===================================================================
# Integration tests — real model (env-gated)
# ===================================================================

@pytest.mark.local_model
@pytest.mark.slow
class TestRealModelInference:
    """These tests only run when the model is downloaded and
    llama-cpp-python is installed."""

    def test_quick_inference_check_passes(self):
        assert quick_inference_check() is True

    def test_full_smoke_test_passes(self):
        result = run_smoke_test()
        assert result.model_loaded is True
        assert result.passed is True, result.summary()
        assert len(result.prompt_results) == 5

    def test_classify_intent_returns_valid_category(self):
        result = run_smoke_test()
        ci = next(
            pr for pr in result.prompt_results
            if pr.name == "classify_intent"
        )
        assert ci.passed is True
        categories = {"question", "command", "information",
                      "greeting", "feedback", "unclear"}
        assert any(c in ci.output.lower() for c in categories)

    def test_extract_json_returns_json(self):
        result = run_smoke_test()
        ej = next(
            pr for pr in result.prompt_results
            if pr.name == "extract_json"
        )
        assert ej.passed is True
        assert "john" in ej.output.lower() or "{" in ej.output

    def test_redact_replaces_pii(self):
        result = run_smoke_test()
        rd = next(
            pr for pr in result.prompt_results
            if pr.name == "redact"
        )
        assert rd.passed is True
        assert "[" in rd.output

    def test_smoke_result_elapsed_is_reasonable(self):
        result = run_smoke_test()
        # Should complete within the 30s pytest timeout
        assert result.total_elapsed_ms > 0
        assert result.total_elapsed_ms < 300_000  # 5 min max
