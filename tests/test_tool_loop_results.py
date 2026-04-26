from tool_loop_results import (
    FAILED_TOOL_RETRY_INSTRUCTION,
    normalize_tool_failure,
    normalize_tool_success,
)


def test_normalize_tool_success_adds_document_download_url(monkeypatch):
    monkeypatch.setenv("LANCELOT_WORKSPACE", "/workspace")

    record = normalize_tool_success(
        "document_creator",
        {"path": "report.md"},
        {"path": "/workspace/reports/summary.pdf"},
        max_result_chars=8000,
    )

    assert record.success is True
    assert record.result_label == "SUCCESS"
    assert record.result_data["download_url"] == "/api/files/reports/summary.pdf"
    assert "[Download summary.pdf](/api/files/reports/summary.pdf)" in record.result_data["download_note"]
    assert record.receipt == {
        "skill": "document_creator",
        "inputs": {"path": "report.md"},
        "result": "SUCCESS",
        "outputs": record.result_data,
    }


def test_normalize_flagship_success_truncates_large_outputs():
    record = normalize_tool_success(
        "network_client",
        {"url": "https://example.com"},
        {"body": "x" * 9000},
        max_result_chars=8000,
    )

    assert record.result_data["truncated"].endswith("... [truncated]")
    assert len(record.result_data["truncated"]) == 8015
    assert record.receipt["outputs"] == record.result_data


def test_normalize_local_success_truncates_content_but_keeps_receipt_outputs():
    outputs = {"body": "x" * 5000}

    record = normalize_tool_success(
        "network_client",
        {"url": "https://example.com"},
        outputs,
        max_result_chars=4000,
    )

    assert record.result_content.endswith("... [truncated]")
    assert len(record.result_content) == 4015
    assert record.result_data == outputs
    assert record.receipt["outputs"] == outputs


def test_normalize_structured_tool_failure_adds_retry_instruction():
    record = normalize_tool_failure(
        "repo_writer",
        {"path": "README.md"},
        "permission denied",
        structured_result=True,
    )

    assert record.success is False
    assert record.result_label == "FAILED: permission denied"
    assert record.result_data == {
        "error": "permission denied",
        "instruction": FAILED_TOOL_RETRY_INSTRUCTION,
    }
    assert record.result_content == "Error: permission denied"
    assert record.receipt == {
        "skill": "repo_writer",
        "inputs": {"path": "README.md"},
        "result": "FAILED: permission denied",
    }


def test_normalize_exception_result_uses_exception_label_and_content():
    record = normalize_tool_failure(
        "command_runner",
        {"command": "bad"},
        RuntimeError("boom"),
        exception=True,
        structured_result=False,
    )

    assert record.result_label == "EXCEPTION: boom"
    assert record.result_data == {"error": "boom"}
    assert record.result_content == "Exception: boom"
