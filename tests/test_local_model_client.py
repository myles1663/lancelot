"""
Tests for src.core.local_model_client — LocalModelClient & utility tasks.
Prompt 13: LocalModelClient & Utility Prompts.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from src.core import local_model_client as local_model_module
from src.core.local_model_client import LocalModelClient, LocalModelError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prompts():
    """Fake prompt templates matching the real templates."""
    return {
        "classify_intent": (
            "Classify the user's intent into exactly one category.\n\n"
            "Categories: question, command, information, greeting, feedback, unclear\n\n"
            "User message: {input}\n\n"
            "Respond with ONLY the category name, nothing else."
        ),
        "extract_json": (
            "Extract structured data from the following text and return valid JSON.\n\n"
            "Text: {input}\n\n"
            "Schema: {schema}\n\n"
            "Return ONLY valid JSON matching the schema. No explanation."
        ),
        "summarize_internal": (
            "Summarize the following text in 2-3 concise sentences for internal use.\n"
            "Focus on key facts and actionable information.\n\n"
            "Text: {input}\n\n"
            "Summary:"
        ),
        "redact": (
            "Redact all personally identifiable information (PII) from the following text.\n"
            "Replace each PII item with its type in brackets: "
            "[NAME], [EMAIL], [PHONE], [ADDRESS], [SSN], [CREDIT_CARD], [DATE_OF_BIRTH].\n\n"
            "Text: {input}\n\n"
            "Redacted text:"
        ),
        "rag_rewrite": (
            "Rewrite the following query to improve retrieval from a vector database.\n"
            "Make it specific, remove filler words, and expand abbreviations.\n\n"
            "Original query: {input}\n\n"
            "Rewritten query:"
        ),
        "verify_intent": (
            "Classify whether the user wants a plan, action, or question.\n"
            "Input: {input}\n"
            "Answer:"
        ),
    }


@pytest.fixture
def client(prompts):
    """Client with mocked prompt loading."""
    with patch("src.core.local_model_client.load_all_prompts", return_value=prompts):
        c = LocalModelClient(base_url="http://test-llm:8080")
        # Pre-load prompts so the mock is captured
        c._get_prompts()
        yield c


def _mock_urlopen(response_data, status=200):
    """Create a mock for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ===================================================================
# Constructor & configuration
# ===================================================================

class TestClientInit:

    def test_default_url(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            c = LocalModelClient()
            assert c._base_url == "http://localhost:8080"

    def test_explicit_url(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            c = LocalModelClient(base_url="http://custom:9090")
            assert c._base_url == "http://custom:9090"

    def test_url_from_env(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            with patch.dict("os.environ", {"LOCAL_LLM_URL": "http://env-host:8080"}):
                c = LocalModelClient()
                assert c._base_url == "http://env-host:8080"

    def test_explicit_url_overrides_env(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            with patch.dict("os.environ", {"LOCAL_LLM_URL": "http://env-host:8080"}):
                c = LocalModelClient(base_url="http://explicit:9090")
                assert c._base_url == "http://explicit:9090"

    def test_trailing_slash_stripped(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            c = LocalModelClient(base_url="http://host:8080/")
            assert c._base_url == "http://host:8080"


# ===================================================================
# Prompt template loading
# ===================================================================

class TestPromptLoading:

    def test_prompts_loaded_lazily(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={"a": "b"}) as mock_load:
            c = LocalModelClient()
            mock_load.assert_not_called()
            c._get_prompts()
            mock_load.assert_called_once()

    def test_prompts_cached(self):
        with patch("src.core.local_model_client.load_all_prompts", return_value={"a": "b"}) as mock_load:
            c = LocalModelClient()
            c._get_prompts()
            c._get_prompts()
            mock_load.assert_called_once()

    def test_render_substitutes_variables(self, client):
        result = client._render("classify_intent", input="hello")
        assert "hello" in result
        assert "{input}" not in result

    def test_render_multiple_variables(self, client):
        result = client._render("extract_json", input="some text", schema='{"name": "string"}')
        assert "some text" in result
        assert '{"name": "string"}' in result


# ===================================================================
# Health endpoint
# ===================================================================

class TestHealth:

    @patch("urllib.request.urlopen")
    def test_health_returns_data(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "status": "ok", "ready": True, "loaded": True,
            "model": "test-model", "uptime_seconds": 42.0,
        })
        data = client.health()
        assert data["status"] == "ok"
        assert data["model"] == "test-model"
        assert data["uptime_seconds"] == 42.0

    @patch("urllib.request.urlopen")
    def test_health_accepts_short_timeout_for_boot_checks(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"status": "ok", "ready": True})

        client.health(timeout=0.25)

        assert mock_open.call_args.kwargs["timeout"] == 0.25

    @patch("urllib.request.urlopen")
    def test_is_healthy_true(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"status": "ok", "ready": True})
        assert client.is_healthy() is True

    @patch("urllib.request.urlopen")
    def test_native_llama_server_health_is_normalized(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"status": "ok"})
        data = client.health()
        assert data["ready"] is True
        assert data["loaded"] is True
        assert data["last_error"] is None
        assert client.is_healthy() is True

    @patch("urllib.request.urlopen")
    def test_is_healthy_false_on_error(self, mock_open, client):
        mock_open.side_effect = URLError("connection refused")
        assert client.is_healthy() is False

    @patch("urllib.request.urlopen")
    def test_is_healthy_false_on_bad_status(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"status": "degraded", "ready": False})
        assert client.is_healthy() is False

    @patch("urllib.request.urlopen")
    def test_health_returns_payload_on_503(self, mock_open, client):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/health", 503, "Model not ready",
            {}, MagicMock(read=lambda: b'{\"status\":\"degraded\",\"ready\":false,\"loaded\":true,\"last_error\":\"GPU crash\"}'),
        )
        data = client.health()
        assert data["ready"] is False
        assert data["loaded"] is True
        assert data["last_error"] == "GPU crash"

    @patch("urllib.request.urlopen")
    def test_health_logs_warning_for_non_json_http_error_body(self, mock_open, client, caplog):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/health", 503, "Model not ready",
            {}, MagicMock(read=lambda: b"gpu failure"),
        )
        with caplog.at_level("WARNING"):
            with pytest.raises(LocalModelError, match="503"):
                client.health()
        assert "non-JSON HTTP error body" in caplog.text

    def test_health_payload_normalization_handles_non_object_and_error_status(self):
        assert local_model_module._normalize_health_payload("bad") == {
            "loaded": False,
            "ready": False,
            "status": "unavailable",
        }
        degraded = local_model_module._normalize_health_payload({"status": "degraded", "error": "warming"})
        assert degraded["ready"] is False
        assert degraded["loaded"] is False
        assert degraded["last_error"] == "warming"


# ===================================================================
# Raw completion
# ===================================================================

class TestComplete:

    @patch("urllib.request.urlopen")
    def test_complete_returns_text(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "Paris", "model": "test", "tokens_generated": 1, "elapsed_ms": 50.0,
        })
        result = client.complete("Capital of France?")
        assert result == "Paris"

    @patch("urllib.request.urlopen")
    def test_complete_accepts_openai_style_choices(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "choices": [{"text": "Paris"}],
            "usage": {"completion_tokens": 1},
        })
        result = client.complete("Capital of France?")
        assert result == "Paris"

    @patch("urllib.request.urlopen")
    def test_complete_sends_correct_payload(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "out", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.complete("prompt", max_tokens=64, temperature=0.5, stop=["\n"])

        call_args = mock_open.call_args
        request_obj = call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["prompt"] == "prompt"
        assert sent["max_tokens"] == 64
        assert sent["temperature"] == 0.5
        assert sent["stop"] == ["\n"]

    @patch("urllib.request.urlopen")
    def test_complete_omits_stop_when_none(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "out", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.complete("prompt")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert "stop" not in sent

    @patch("urllib.request.urlopen")
    def test_complete_raises_on_connection_failure(self, mock_open, client):
        mock_open.side_effect = URLError("connection refused")
        with pytest.raises(LocalModelError, match="Connection failed"):
            client.complete("hello")

    @patch("urllib.request.urlopen")
    def test_complete_raises_on_500(self, mock_open, client):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/v1/completions", 500, "Inference error",
            {}, MagicMock(read=lambda: b"Inference error: GPU crash"),
        )
        with pytest.raises(LocalModelError, match="500"):
            client.complete("crash me")

    @patch("urllib.request.urlopen")
    def test_complete_accepts_openai_chat_message_choices_and_rejects_missing_text(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"choices": [{"message": {"content": "chat answer"}}]})
        assert client.complete("chat style") == "chat answer"

        mock_open.return_value = _mock_urlopen({"choices": [{}]})
        with pytest.raises(LocalModelError, match="did not include text"):
            client.complete("missing text")

        mock_open.return_value = _mock_urlopen(["not", "object"])
        with pytest.raises(LocalModelError, match="non-object"):
            client.complete("bad payload")


# ===================================================================
# classify_intent
# ===================================================================

class TestClassifyIntent:

    @patch("urllib.request.urlopen")
    def test_returns_category(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "question", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        result = client.classify_intent("What time is it?")
        assert result == "question"

    @patch("urllib.request.urlopen")
    def test_strips_whitespace(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "  greeting\n", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        result = client.classify_intent("Hello!")
        assert result == "greeting"

    @patch("urllib.request.urlopen")
    def test_lowercases_result(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "COMMAND", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        result = client.classify_intent("Delete the file")
        assert result == "command"

    @patch("urllib.request.urlopen")
    def test_extracts_category_from_noisy_classifier_output(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "the category should be one of the following: question, command",
            "model": "m",
            "tokens_generated": 8,
            "elapsed_ms": 1.0,
        })
        result = client.classify_intent("Can you summarize this status?")
        assert result == "question"

    @patch("urllib.request.urlopen")
    def test_noisy_classifier_label_list_uses_input_heuristic(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "question, command, information, greeting, feedback, unclear",
            "model": "m",
            "tokens_generated": 8,
            "elapsed_ms": 1.0,
        })
        result = client.classify_intent("Delete the temporary file")
        assert result == "command"

    @patch("urllib.request.urlopen")
    def test_defaults_unclear_when_classifier_output_has_no_label(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "I cannot decide",
            "model": "m",
            "tokens_generated": 4,
            "elapsed_ms": 1.0,
        })
        result = client.classify_intent("...")
        assert result == "unclear"

    @patch("urllib.request.urlopen")
    def test_uses_low_temperature(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "question", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.classify_intent("test")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["temperature"] == 0.0
        assert sent["max_tokens"] == 16
        assert sent["stop"] == ["\n"]

    @patch("urllib.request.urlopen")
    def test_prompt_contains_input(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "question", "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.classify_intent("Where is the store?")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert "Where is the store?" in sent["prompt"]


# ===================================================================
# extract_json
# ===================================================================

class TestExtractJson:

    @patch("urllib.request.urlopen")
    def test_returns_parsed_dict(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": '{"name": "John", "age": 30}',
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        result = client.extract_json("John is 30", '{"name": "string", "age": "number"}')
        assert result == {"name": "John", "age": 30}

    @patch("urllib.request.urlopen")
    def test_strips_code_fences(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": '```json\n{"key": "value"}\n```',
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        result = client.extract_json("text", "schema")
        assert result == {"key": "value"}

    @patch("urllib.request.urlopen")
    def test_raises_on_invalid_json(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "not json at all",
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        with pytest.raises(LocalModelError, match="invalid JSON"):
            client.extract_json("text", "schema")

    @patch("urllib.request.urlopen")
    def test_uses_zero_temperature(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": '{"a": 1}',
            "model": "m", "tokens_generated": 3, "elapsed_ms": 1.0,
        })
        client.extract_json("text", "schema")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["temperature"] == 0.0
        assert sent["max_tokens"] == 512

    @patch("urllib.request.urlopen")
    def test_prompt_contains_input_and_schema(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": '{"x": 1}',
            "model": "m", "tokens_generated": 2, "elapsed_ms": 1.0,
        })
        client.extract_json("my input text", "my schema def")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert "my input text" in sent["prompt"]
        assert "my schema def" in sent["prompt"]


# ===================================================================
# summarize
# ===================================================================

class TestSummarize:

    @patch("urllib.request.urlopen")
    def test_returns_summary_text(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "Key points were discussed.",
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        result = client.summarize("Long text about many things...")
        assert result == "Key points were discussed."

    @patch("urllib.request.urlopen")
    def test_strips_whitespace(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "\n  Summary here.  \n",
            "model": "m", "tokens_generated": 3, "elapsed_ms": 1.0,
        })
        result = client.summarize("text")
        assert result == "Summary here."

    @patch("urllib.request.urlopen")
    def test_uses_256_max_tokens(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "summary",
            "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.summarize("text")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["max_tokens"] == 256


# ===================================================================
# redact
# ===================================================================

class TestRedact:

    @patch("urllib.request.urlopen")
    def test_returns_redacted_text(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "[NAME] lives at [ADDRESS].",
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        result = client.redact("John lives at 123 Main St.")
        assert result == "[NAME] lives at [ADDRESS]."

    @patch("urllib.request.urlopen")
    def test_uses_zero_temperature(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "[NAME]",
            "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.redact("John")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["temperature"] == 0.0

    @patch("urllib.request.urlopen")
    def test_uses_512_max_tokens(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "[NAME]",
            "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.redact("text")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["max_tokens"] == 512


# ===================================================================
# rag_rewrite
# ===================================================================

class TestRagRewrite:

    @patch("urllib.request.urlopen")
    def test_returns_rewritten_query(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "machine learning neural network architecture overview",
            "model": "m", "tokens_generated": 5, "elapsed_ms": 1.0,
        })
        result = client.rag_rewrite("what's ML about?")
        assert "machine learning" in result

    @patch("urllib.request.urlopen")
    def test_strips_whitespace(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "\n  rewritten query  \n",
            "model": "m", "tokens_generated": 3, "elapsed_ms": 1.0,
        })
        result = client.rag_rewrite("query")
        assert result == "rewritten query"

    @patch("urllib.request.urlopen")
    def test_returns_first_rewritten_line_when_model_explains(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "scrub latency documentation\n\nOkay, here is why...",
            "model": "m", "tokens_generated": 10, "elapsed_ms": 1.0,
        })
        result = client.rag_rewrite("find docs about scrub latency")
        assert result == "scrub latency documentation"

    @patch("urllib.request.urlopen")
    def test_uses_96_max_tokens(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({
            "text": "rewritten",
            "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        client.rag_rewrite("query")

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert sent["max_tokens"] == 96
        assert sent["temperature"] == 0.0
        assert sent["stop"] == ["\n"]


class TestRoutingAndChatTools:

    @patch("urllib.request.urlopen")
    def test_verify_routing_intent_extracts_allowed_label_and_defaults(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"text": "I recommend a plan."})
        assert client.verify_routing_intent("should we plan this?") == "plan"

        mock_open.return_value = _mock_urlopen({"text": "no clear label"})
        assert client.verify_routing_intent("ambiguous") == "action"

    @patch("urllib.request.urlopen")
    def test_chat_with_tools_sends_tools_and_tool_choice(self, mock_open, client):
        mock_open.return_value = _mock_urlopen({"choices": [{"message": {"content": "ok"}}]})
        response = client.chat_with_tools(
            [{"role": "user", "content": "run tool"}],
            tools=[{"type": "function", "function": {"name": "lookup"}}],
            tool_choice="auto",
        )

        request_obj = mock_open.call_args.args[0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        assert response["choices"][0]["message"]["content"] == "ok"
        assert sent["tools"][0]["function"]["name"] == "lookup"
        assert sent["tool_choice"] == "auto"

    @patch("urllib.request.urlopen")
    def test_get_helper_and_url_policy_failures_are_reported(self, mock_open, client, monkeypatch):
        mock_open.return_value = _mock_urlopen({"ok": True})
        assert client._get("/health") == {"ok": True}

        monkeypatch.setattr(
            local_model_module,
            "assert_local_control_url",
            lambda *args, **kwargs: (_ for _ in ()).throw(local_model_module.LocalControlPlaneError("remote denied")),
        )
        with pytest.raises(LocalModelError, match="remote denied"):
            client._get("/health")
        with pytest.raises(LocalModelError, match="remote denied"):
            client._post("/v1/completions", {})

    @patch("urllib.request.urlopen")
    def test_get_helper_reports_http_url_and_generic_failures(self, mock_open, client):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/health",
            500,
            "Server Error",
            {},
            MagicMock(read=lambda: b"health exploded"),
        )
        with pytest.raises(LocalModelError, match="HTTP 500"):
            client._get("/health")

        mock_open.side_effect = URLError("offline")
        with pytest.raises(LocalModelError, match="Connection failed"):
            client._get("/health")

        mock_open.side_effect = RuntimeError("bad socket")
        with pytest.raises(LocalModelError, match="Request failed"):
            client._get("/health")

    @patch("urllib.request.urlopen")
    def test_post_helper_reports_generic_failure_and_heuristic_labels(self, mock_open, client):
        mock_open.side_effect = RuntimeError("bad socket")
        with pytest.raises(LocalModelError, match="Request failed"):
            client._post("/v1/completions", {})

        assert local_model_module._first_nonempty_line("\n\n") == ""
        assert local_model_module._classify_intent_heuristically("") == "unclear"
        assert local_model_module._classify_intent_heuristically("thank you, that worked") == "feedback"
        assert local_model_module._classify_intent_heuristically("status update") == "information"


# ===================================================================
# Error handling
# ===================================================================

class TestErrorHandling:

    @patch("urllib.request.urlopen")
    def test_remote_control_plane_url_blocked_before_request(self, mock_open):
        with patch("src.core.local_model_client.load_all_prompts", return_value={}):
            client = LocalModelClient(base_url="https://api.openai.com")
        with pytest.raises(LocalModelError, match="local-only URL policy"):
            client.complete("hello")
        mock_open.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_connection_refused_raises_local_model_error(self, mock_open, client):
        mock_open.side_effect = URLError("Connection refused")
        with pytest.raises(LocalModelError, match="Connection failed"):
            client.classify_intent("test")

    @patch("urllib.request.urlopen")
    def test_http_500_raises_local_model_error(self, mock_open, client):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/v1/completions", 500, "Server Error",
            {}, MagicMock(read=lambda: b"internal error"),
        )
        with pytest.raises(LocalModelError, match="500"):
            client.summarize("text")

    @patch("urllib.request.urlopen")
    def test_http_422_raises_local_model_error(self, mock_open, client):
        mock_open.side_effect = HTTPError(
            "http://test-llm:8080/v1/completions", 422, "Validation Error",
            {}, MagicMock(read=lambda: b"missing prompt"),
        )
        with pytest.raises(LocalModelError, match="422"):
            client.redact("text")

    @patch("urllib.request.urlopen")
    def test_timeout_raises_local_model_error(self, mock_open, client):
        import socket
        mock_open.side_effect = URLError(socket.timeout("timed out"))
        with pytest.raises(LocalModelError, match="Connection failed"):
            client.rag_rewrite("query")

    @patch("urllib.request.urlopen")
    def test_http_error_body_decode_failure_logs_warning(self, mock_open, client, caplog):
        err = HTTPError(
            "http://test-llm:8080/v1/completions", 500, "Server Error",
            {}, MagicMock(),
        )
        err.read = MagicMock(side_effect=RuntimeError("read failed"))
        mock_open.side_effect = err
        with caplog.at_level("WARNING"):
            with pytest.raises(LocalModelError, match="500"):
                client.complete("test")
        assert "Failed to decode local model HTTP error body" in caplog.text


# ===================================================================
# All utility methods parametrized
# ===================================================================

class TestAllUtilityMethods:

    @pytest.mark.parametrize("method,args,expected_template", [
        ("classify_intent", ("hello",), "classify_intent"),
        ("summarize", ("long text",), "summarize_internal"),
        ("redact", ("John Doe, 555-1234",), "redact"),
        ("rag_rewrite", ("what is ML?",), "rag_rewrite"),
    ])
    @patch("urllib.request.urlopen")
    def test_each_method_uses_correct_template(
        self, mock_open, method, args, expected_template, client
    ):
        mock_open.return_value = _mock_urlopen({
            "text": "output",
            "model": "m", "tokens_generated": 1, "elapsed_ms": 1.0,
        })
        getattr(client, method)(*args)

        request_obj = mock_open.call_args[0][0]
        sent = json.loads(request_obj.data.decode("utf-8"))
        # The prompt should contain content from the expected template
        template_content = client._get_prompts()[expected_template]
        # Check the prompt starts with the template's first line
        first_line = template_content.split("\n")[0]
        assert first_line in sent["prompt"]

    @pytest.mark.parametrize("method,args", [
        ("classify_intent", ("text",)),
        ("extract_json", ("text", "schema")),
        ("summarize", ("text",)),
        ("redact", ("text",)),
        ("rag_rewrite", ("text",)),
    ])
    @patch("urllib.request.urlopen")
    def test_each_method_raises_on_connection_error(
        self, mock_open, method, args, client
    ):
        mock_open.side_effect = URLError("Connection refused")
        with pytest.raises(LocalModelError):
            getattr(client, method)(*args)


# ===================================================================
# Integration — live Docker service
# ===================================================================

@pytest.mark.docker
class TestLiveLocalModelClient:
    """Integration tests — only run when local-llm container is up."""

    @pytest.fixture(autouse=True)
    def _check_container(self):
        import shutil
        import subprocess
        import urllib.request
        if shutil.which("docker") is None:
            pytest.skip("Docker not available")
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}",
             "lancelot_local_llm"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or "true" not in result.stdout:
            pytest.skip("lancelot_local_llm container not running")
        try:
            resp = urllib.request.urlopen("http://localhost:8080/health", timeout=5)
            if resp.status != 200:
                pytest.skip("lancelot_local_llm not ready for inference")
        except Exception:
            pytest.skip("lancelot_local_llm not ready for inference")

    @pytest.fixture
    def live_client(self):
        client = LocalModelClient(base_url="http://localhost:8080")
        if not client.is_healthy():
            pytest.skip("lancelot_local_llm not ready for inference")
        try:
            client.complete("Health check", max_tokens=4, temperature=0.0, timeout=10)
        except Exception:
            pytest.skip("lancelot_local_llm completion smoke failed")
        return client

    def test_health(self, live_client):
        data = live_client.health()
        assert data["status"] == "ok"

    def test_is_healthy(self, live_client):
        assert live_client.is_healthy() is True

    def test_classify_intent(self, live_client):
        result = live_client.classify_intent("What time does the store close?")
        assert result in ("question", "command", "information", "greeting",
                          "feedback", "unclear")

    def test_summarize(self, live_client):
        result = live_client.summarize(
            "The meeting covered quarterly results. Revenue was up 15%. "
            "The team decided to hire two more engineers."
        )
        assert len(result) > 0

    def test_redact(self, live_client):
        result = live_client.redact(
            "John Smith called from 555-123-4567 about his account."
        )
        assert "[" in result  # Should contain redaction markers

    def test_rag_rewrite(self, live_client):
        result = live_client.rag_rewrite("what's ML about?")
        assert len(result) > 0
