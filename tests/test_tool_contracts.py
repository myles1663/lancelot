"""
Unit Tests for Tool Fabric Contracts and Receipts
==================================================

Tests for:
- Contracts: ExecResult, FileChange, PatchResult, ScaffoldResult, VisionResult
- Contracts: ProviderHealth, ToolIntent, PolicySnapshot
- Contracts: Capability enums and Protocol definitions
- Receipts: ToolReceipt, VisionReceipt
- Receipts: Factory functions and helpers

Prompt 1 — Foundation types + receipts
"""

import pytest
import json
from datetime import datetime, timezone

from src.tools.contracts import (
    # Enums
    RiskLevel,
    ProviderState,
    Capability,
    UIBuilderMode,
    # Result types
    ExecResult,
    FileChange,
    PatchResult,
    ScaffoldResult,
    VisionResult,
    # Health and intent
    ProviderHealth,
    ToolIntent,
    PolicySnapshot,
    # Protocols
    ShellExecCapability,
    RepoOpsCapability,
    FileOpsCapability,
    WebOpsCapability,
    UIBuilderCapability,
    DeployOpsCapability,
    VisionControlCapability,
    BaseProvider,
)

from src.tools.receipts import (
    ToolReceipt,
    VisionReceipt,
    create_tool_receipt,
    create_vision_receipt,
    _summarize_inputs,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestEnums:
    """Test enum definitions and values."""

    def test_risk_level_values(self):
        """RiskLevel has correct string values."""
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"

    def test_provider_state_values(self):
        """ProviderState has correct string values."""
        assert ProviderState.HEALTHY.value == "healthy"
        assert ProviderState.DEGRADED.value == "degraded"
        assert ProviderState.OFFLINE.value == "offline"

    def test_capability_values(self):
        """Capability enum covers all expected capabilities."""
        expected = {
            "shell_exec", "repo_ops", "file_ops", "web_ops",
            "ui_builder", "deploy_ops", "vision_control"
        }
        actual = {c.value for c in Capability}
        assert actual == expected

    def test_ui_builder_mode_values(self):
        """UIBuilderMode has correct string values."""
        assert UIBuilderMode.DETERMINISTIC.value == "deterministic"
        assert UIBuilderMode.GENERATIVE.value == "generative"


# =============================================================================
# ExecResult Tests
# =============================================================================


class TestExecResult:
    """Test ExecResult dataclass."""

    def test_exec_result_creation(self):
        """ExecResult can be created with required fields."""
        result = ExecResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            duration_ms=100,
        )
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.duration_ms == 100
        assert result.truncated is False

    def test_exec_result_success_property(self):
        """success property returns True for exit code 0."""
        success = ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0)
        failure = ExecResult(exit_code=1, stdout="", stderr="error", duration_ms=0)

        assert success.success is True
        assert failure.success is False

    def test_exec_result_serialization(self):
        """ExecResult serializes to and from dict."""
        original = ExecResult(
            exit_code=0,
            stdout="output",
            stderr="",
            duration_ms=50,
            command="echo hello",
            working_dir="/tmp",
            timed_out=False,
        )
        d = original.to_dict()
        restored = ExecResult.from_dict(d)

        assert restored.exit_code == original.exit_code
        assert restored.stdout == original.stdout
        assert restored.command == original.command
        assert restored.working_dir == original.working_dir

    def test_exec_result_json_serializable(self):
        """ExecResult dict is JSON serializable."""
        result = ExecResult(
            exit_code=0,
            stdout="test",
            stderr="",
            duration_ms=10,
        )
        json_str = json.dumps(result.to_dict())
        assert "exit_code" in json_str


# =============================================================================
# FileChange Tests
# =============================================================================


class TestFileChange:
    """Test FileChange dataclass."""

    def test_file_change_creation(self):
        """FileChange can be created with required fields."""
        change = FileChange(
            path="/workspace/file.py",
            action="modified",
            hash_before="abc123",
            hash_after="def456",
        )
        assert change.path == "/workspace/file.py"
        assert change.action == "modified"
        assert change.hash_before == "abc123"
        assert change.hash_after == "def456"

    def test_file_change_created_action(self):
        """FileChange for created file has no hash_before."""
        change = FileChange(
            path="/workspace/new.py",
            action="created",
            hash_before=None,
            hash_after="abc123",
        )
        assert change.hash_before is None
        assert change.hash_after is not None

    def test_file_change_deleted_action(self):
        """FileChange for deleted file has no hash_after."""
        change = FileChange(
            path="/workspace/old.py",
            action="deleted",
            hash_before="abc123",
            hash_after=None,
        )
        assert change.hash_before is not None
        assert change.hash_after is None

    def test_file_change_serialization(self):
        """FileChange serializes to and from dict."""
        original = FileChange(
            path="/test.py",
            action="modified",
            hash_before="a" * 64,
            hash_after="b" * 64,
            size_before=100,
            size_after=150,
        )
        d = original.to_dict()
        restored = FileChange.from_dict(d)

        assert restored.path == original.path
        assert restored.size_before == original.size_before
        assert restored.size_after == original.size_after


# =============================================================================
# PatchResult Tests
# =============================================================================


class TestPatchResult:
    """Test PatchResult dataclass."""

    def test_patch_result_success(self):
        """PatchResult for successful patch."""
        result = PatchResult(
            success=True,
            files_changed=[
                FileChange(path="a.py", action="modified"),
                FileChange(path="b.py", action="created"),
            ],
        )
        assert result.success is True
        assert len(result.files_changed) == 2
        assert result.rejected_hunks == []

    def test_patch_result_partial_failure(self):
        """PatchResult with rejected hunks."""
        result = PatchResult(
            success=False,
            files_changed=[FileChange(path="a.py", action="modified")],
            rejected_hunks=["@@ -10,5 +10,6 @@"],
            error_message="Could not apply hunk",
        )
        assert result.success is False
        assert len(result.rejected_hunks) == 1
        assert result.error_message is not None

    def test_patch_result_serialization(self):
        """PatchResult serializes to and from dict with nested FileChange."""
        original = PatchResult(
            success=True,
            files_changed=[
                FileChange(path="test.py", action="modified", hash_after="abc"),
            ],
        )
        d = original.to_dict()
        restored = PatchResult.from_dict(d)

        assert restored.success == original.success
        assert len(restored.files_changed) == 1
        assert restored.files_changed[0].path == "test.py"


# =============================================================================
# ProviderHealth Tests
# =============================================================================


class TestProviderHealth:
    """Test ProviderHealth dataclass."""

    def test_provider_health_healthy(self):
        """ProviderHealth for healthy provider."""
        health = ProviderHealth(
            provider_id="local_sandbox",
            state=ProviderState.HEALTHY,
            version="1.0.0",
            capabilities=["shell_exec", "repo_ops", "file_ops"],
        )
        assert health.is_healthy is True
        assert health.is_available is True

    def test_provider_health_degraded(self):
        """ProviderHealth for degraded provider."""
        health = ProviderHealth(
            provider_id="aider",
            state=ProviderState.DEGRADED,
            degraded_reasons=["Network timeout"],
        )
        assert health.is_healthy is False
        assert health.is_available is True

    def test_provider_health_offline(self):
        """ProviderHealth for offline provider."""
        health = ProviderHealth(
            provider_id="opencode",
            state=ProviderState.OFFLINE,
            error_message="Binary not found",
        )
        assert health.is_healthy is False
        assert health.is_available is False

    def test_provider_health_serialization(self):
        """ProviderHealth serializes to and from dict."""
        original = ProviderHealth(
            provider_id="test",
            state=ProviderState.HEALTHY,
            version="2.0",
            capabilities=["shell_exec"],
            metadata={"extra": "data"},
        )
        d = original.to_dict()

        # State is serialized as string value
        assert d["state"] == "healthy"

        restored = ProviderHealth.from_dict(d)
        assert restored.provider_id == original.provider_id
        assert restored.state == ProviderState.HEALTHY


# =============================================================================
# ToolIntent Tests
# =============================================================================


class TestToolIntent:
    """Test ToolIntent dataclass."""

    def test_tool_intent_creation(self):
        """ToolIntent can be created with required fields."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.LOW,
            inputs={"command": "ls -la"},
        )
        assert intent.capability == Capability.SHELL_EXEC
        assert intent.action == "run"
        assert intent.risk == RiskLevel.LOW
        assert intent.inputs["command"] == "ls -la"

    def test_tool_intent_auto_id(self):
        """ToolIntent generates unique ID automatically."""
        intent1 = ToolIntent(capability=Capability.FILE_OPS, action="read", workspace="/")
        intent2 = ToolIntent(capability=Capability.FILE_OPS, action="read", workspace="/")
        assert intent1.id != intent2.id

    def test_tool_intent_verification_steps(self):
        """ToolIntent can include verification steps."""
        intent = ToolIntent(
            capability=Capability.REPO_OPS,
            action="apply_patch",
            workspace="/repo",
            risk=RiskLevel.MEDIUM,
            verify=["run_tests", "git_diff_clean"],
        )
        assert len(intent.verify) == 2
        assert "run_tests" in intent.verify

    def test_tool_intent_serialization(self):
        """ToolIntent serializes to and from dict."""
        original = ToolIntent(
            capability=Capability.DEPLOY_OPS,
            action="build",
            workspace="/app",
            risk=RiskLevel.HIGH,
            inputs={"tag": "latest"},
            verify=["test"],
            provider_hint="local_sandbox",
        )
        d = original.to_dict()

        # Enums serialized as string values
        assert d["capability"] == "deploy_ops"
        assert d["risk"] == "high"

        restored = ToolIntent.from_dict(d)
        assert restored.capability == Capability.DEPLOY_OPS
        assert restored.risk == RiskLevel.HIGH
        assert restored.provider_hint == "local_sandbox"


# =============================================================================
# PolicySnapshot Tests
# =============================================================================


class TestPolicySnapshot:
    """Test PolicySnapshot dataclass."""

    def test_policy_snapshot_defaults(self):
        """PolicySnapshot has secure defaults."""
        policy = PolicySnapshot()
        assert policy.network_allowed is False
        assert policy.workspace_restricted is True
        assert policy.risk_level == RiskLevel.LOW
        assert policy.verifier_approved is False

    def test_policy_snapshot_with_allowlist(self):
        """PolicySnapshot can have allowlist/denylist."""
        policy = PolicySnapshot(
            network_allowed=False,
            allowlist_applied=["git", "python", "pytest"],
            denylist_applied=["rm", "curl"],
            risk_level=RiskLevel.MEDIUM,
        )
        assert len(policy.allowlist_applied) == 3
        assert len(policy.denylist_applied) == 2

    def test_policy_snapshot_serialization(self):
        """PolicySnapshot serializes to and from dict."""
        original = PolicySnapshot(
            network_allowed=True,
            allowlist_applied=["curl"],
            risk_level=RiskLevel.HIGH,
            verifier_approved=True,
        )
        d = original.to_dict()

        assert d["risk_level"] == "high"

        restored = PolicySnapshot.from_dict(d)
        assert restored.network_allowed is True
        assert restored.risk_level == RiskLevel.HIGH


# =============================================================================
# ToolReceipt Tests
# =============================================================================


class TestToolReceipt:
    """Test ToolReceipt dataclass."""

    def test_tool_receipt_creation(self):
        """ToolReceipt can be created with minimal fields."""
        receipt = ToolReceipt(
            capability=Capability.SHELL_EXEC.value,
            action="run",
            provider_id="local_sandbox",
        )
        assert receipt.receipt_id is not None
        assert receipt.timestamp is not None
        assert receipt.capability == "shell_exec"
        assert receipt.success is False  # Default pending state

    def test_tool_receipt_with_exec_result(self):
        """ToolReceipt can be updated with ExecResult."""
        receipt = ToolReceipt(
            capability=Capability.SHELL_EXEC.value,
            action="run",
            provider_id="local_sandbox",
        )
        result = ExecResult(
            exit_code=0,
            stdout="hello world",
            stderr="",
            duration_ms=50,
        )
        receipt.with_exec_result(result)

        assert receipt.exit_code == 0
        assert receipt.stdout == "hello world"
        assert receipt.duration_ms == 50
        assert receipt.success is True

    def test_tool_receipt_output_truncation(self):
        """ToolReceipt truncates long output."""
        receipt = ToolReceipt(
            capability=Capability.SHELL_EXEC.value,
            action="run",
            provider_id="local_sandbox",
        )
        long_output = "x" * 20000
        result = ExecResult(
            exit_code=0,
            stdout=long_output,
            stderr="",
            duration_ms=100,
        )
        receipt.with_exec_result(result, max_output=1000)

        assert len(receipt.stdout) < 20000
        assert receipt.stdout_truncated is True
        assert "truncated" in receipt.stdout

    def test_tool_receipt_with_file_changes(self):
        """ToolReceipt can track file changes."""
        receipt = ToolReceipt(
            capability=Capability.FILE_OPS.value,
            action="write",
            provider_id="local_sandbox",
        )
        changes = [
            FileChange(path="a.py", action="modified", hash_after="abc"),
            FileChange(path="b.py", action="created", hash_after="def"),
        ]
        receipt.with_file_changes(changes)

        assert len(receipt.changed_files) == 2

    def test_tool_receipt_with_policy(self):
        """ToolReceipt can capture policy snapshot."""
        receipt = ToolReceipt(
            capability=Capability.SHELL_EXEC.value,
            action="run",
            provider_id="local_sandbox",
        )
        policy = PolicySnapshot(
            network_allowed=True,
            risk_level=RiskLevel.HIGH,
        )
        receipt.with_policy(policy)

        assert receipt.policy_snapshot["network_allowed"] is True
        assert receipt.risk_level == "high"

    def test_tool_receipt_failure(self):
        """ToolReceipt can mark failure."""
        receipt = ToolReceipt(
            capability=Capability.SHELL_EXEC.value,
            action="run",
            provider_id="local_sandbox",
        )
        receipt.fail("Command timed out")

        assert receipt.success is False
        assert receipt.error_message == "Command timed out"

    def test_tool_receipt_serialization(self):
        """ToolReceipt serializes to and from dict."""
        original = ToolReceipt(
            capability=Capability.REPO_OPS.value,
            action="commit",
            provider_id="local_sandbox",
            provider_version="1.0",
            exit_code=0,
            success=True,
        )
        d = original.to_dict()
        json_str = json.dumps(d)  # Must be JSON serializable

        restored = ToolReceipt.from_dict(d)
        assert restored.capability == original.capability
        assert restored.provider_id == original.provider_id


# =============================================================================
# VisionReceipt Tests
# =============================================================================


class TestVisionReceipt:
    """Test VisionReceipt dataclass."""

    def test_vision_receipt_creation(self):
        """VisionReceipt can be created with minimal fields."""
        receipt = VisionReceipt(action="capture_screen")
        assert receipt.receipt_id is not None
        assert receipt.provider_id == "antigravity_vision"
        assert receipt.action == "capture_screen"

    def test_vision_receipt_with_screenshots(self):
        """VisionReceipt hashes screenshots."""
        receipt = VisionReceipt(action="perform_action")
        before = b"screenshot_before_bytes"
        after = b"screenshot_after_bytes"
        receipt.with_screenshots(before, after)

        assert receipt.screenshot_before_hash is not None
        assert receipt.screenshot_after_hash is not None
        assert receipt.screenshot_before_hash != receipt.screenshot_after_hash
        assert len(receipt.screenshot_before_hash) == 64  # SHA-256 hex

    def test_vision_receipt_with_result(self):
        """VisionReceipt can be updated with VisionResult."""
        receipt = VisionReceipt(action="locate_element")
        result = VisionResult(
            success=True,
            screenshot_hash="abc123",
            elements_detected=[{"x": 100, "y": 200, "label": "button"}],
            confidence=0.95,
        )
        receipt.with_vision_result(result)

        assert receipt.success is True
        assert receipt.confidence_score == 0.95
        assert len(receipt.elements_detected) == 1

    def test_vision_receipt_failure(self):
        """VisionReceipt can mark failure."""
        receipt = VisionReceipt(action="perform_action")
        receipt.fail("Element not found")

        assert receipt.success is False
        assert receipt.error_message == "Element not found"

    def test_vision_receipt_serialization(self):
        """VisionReceipt serializes to and from dict."""
        original = VisionReceipt(
            action="verify_state",
            screenshot_before_hash="abc",
            screenshot_after_hash="def",
            confidence_score=0.9,
            success=True,
        )
        d = original.to_dict()
        json_str = json.dumps(d)

        restored = VisionReceipt.from_dict(d)
        assert restored.action == original.action
        assert restored.confidence_score == original.confidence_score


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestReceiptFactories:
    """Test receipt factory functions."""

    def test_create_tool_receipt_basic(self):
        """create_tool_receipt creates receipt with required fields."""
        receipt = create_tool_receipt(
            capability=Capability.SHELL_EXEC,
            action="run",
            provider_id="local_sandbox",
        )
        assert receipt.capability == "shell_exec"
        assert receipt.action == "run"
        assert receipt.provider_id == "local_sandbox"
        assert receipt.receipt_id is not None

    def test_create_tool_receipt_workspace_redaction(self):
        """create_tool_receipt redacts workspace path."""
        receipt = create_tool_receipt(
            capability=Capability.FILE_OPS,
            action="write",
            provider_id="local_sandbox",
            workspace="/home/user/secret_project/code",
        )
        # Workspace ID is a hash
        assert receipt.workspace_id is not None
        assert len(receipt.workspace_id) == 12  # First 12 chars of hash

        # Path is redacted to just the last component
        assert receipt.workspace_path_redacted == ".../code"
        assert "secret_project" not in receipt.workspace_path_redacted

    def test_create_tool_receipt_input_redaction(self):
        """create_tool_receipt redacts sensitive input keys."""
        receipt = create_tool_receipt(
            capability=Capability.SHELL_EXEC,
            action="run",
            provider_id="local_sandbox",
            inputs={
                "command": "curl http://example.com",
                "password": "super_secret_123",
                "api_key": "sk-1234567890",
                "token": "bearer_token_value",
            },
        )
        summary = receipt.inputs_summary

        # Command is preserved
        assert summary["command"] == "curl http://example.com"

        # Sensitive keys are redacted
        assert summary["password"] == "[REDACTED]"
        assert summary["api_key"] == "[REDACTED]"
        assert summary["token"] == "[REDACTED]"

    def test_create_tool_receipt_with_session(self):
        """create_tool_receipt can include session and parent IDs."""
        receipt = create_tool_receipt(
            capability=Capability.REPO_OPS,
            action="commit",
            provider_id="local_sandbox",
            session_id="session-123",
            parent_receipt_id="parent-456",
        )
        assert receipt.session_id == "session-123"
        assert receipt.parent_receipt_id == "parent-456"

    def test_create_vision_receipt(self):
        """create_vision_receipt creates receipt with defaults."""
        receipt = create_vision_receipt(action="capture_screen")
        assert receipt.action == "capture_screen"
        assert receipt.provider_id == "antigravity_vision"
        assert receipt.receipt_id is not None


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Test helper functions."""

    def test_summarize_inputs_basic(self):
        """_summarize_inputs handles basic types."""
        inputs = {
            "string_val": "hello",
            "int_val": 42,
            "bool_val": True,
        }
        summary = _summarize_inputs(inputs)

        assert summary["string_val"] == "hello"
        assert summary["int_val"] == 42
        assert summary["bool_val"] is True

    def test_summarize_inputs_long_string(self):
        """_summarize_inputs truncates long strings."""
        inputs = {"long": "x" * 500}
        summary = _summarize_inputs(inputs, max_length=100)

        assert len(summary["long"]) < 500
        assert "500 chars" in summary["long"]

    def test_summarize_inputs_binary(self):
        """_summarize_inputs handles binary data."""
        inputs = {"data": b"binary content"}
        summary = _summarize_inputs(inputs)

        assert "binary" in summary["data"]
        assert "hash=" in summary["data"]

    def test_summarize_inputs_sensitive_keys(self):
        """_summarize_inputs redacts sensitive keys."""
        inputs = {
            "password": "secret",
            "api_secret": "hidden",
            "auth_token": "bearer",
            "credential": "private",
            "command": "visible",  # Normal key without sensitive patterns
        }
        summary = _summarize_inputs(inputs)

        assert summary["password"] == "[REDACTED]"
        assert summary["api_secret"] == "[REDACTED]"
        assert summary["auth_token"] == "[REDACTED]"
        assert summary["credential"] == "[REDACTED]"
        assert summary["command"] == "visible"

    def test_summarize_inputs_nested_dict(self):
        """_summarize_inputs handles nested dicts."""
        inputs = {
            "outer": {
                "inner": "value",
                "secret_key": "hidden",
            }
        }
        summary = _summarize_inputs(inputs)

        assert summary["outer"]["inner"] == "value"
        assert summary["outer"]["secret_key"] == "[REDACTED]"

    def test_summarize_inputs_list(self):
        """_summarize_inputs summarizes lists."""
        inputs = {"items": [1, 2, 3, 4, 5]}
        summary = _summarize_inputs(inputs)

        assert "5 items" in summary["items"]


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestProtocolCompliance:
    """Test that protocols are properly defined as runtime_checkable."""

    def test_shell_exec_is_protocol(self):
        """ShellExecCapability is a runtime-checkable Protocol."""
        from typing import runtime_checkable, Protocol

        assert hasattr(ShellExecCapability, "__protocol_attrs__") or \
               hasattr(ShellExecCapability, "_is_protocol")

    def test_all_capability_protocols_defined(self):
        """All capability protocols are importable and defined."""
        protocols = [
            ShellExecCapability,
            RepoOpsCapability,
            FileOpsCapability,
            WebOpsCapability,
            UIBuilderCapability,
            DeployOpsCapability,
            VisionControlCapability,
        ]
        for proto in protocols:
            assert proto is not None


# =============================================================================
# Feature Flag Tests
# =============================================================================


class TestToolFabricFeatureFlags:
    """Test Tool Fabric feature flags are defined."""

    def test_tool_fabric_flags_exist(self):
        """Tool Fabric feature flags are defined in feature_flags module."""
        from src.core.feature_flags import (
            FEATURE_TOOLS_FABRIC,
            FEATURE_TOOLS_CLI_PROVIDERS,
            FEATURE_TOOLS_ANTIGRAVITY,
            FEATURE_TOOLS_NETWORK,
            FEATURE_TOOLS_HOST_EXECUTION,
        )

        # Check they are boolean
        assert isinstance(FEATURE_TOOLS_FABRIC, bool)
        assert isinstance(FEATURE_TOOLS_CLI_PROVIDERS, bool)
        assert isinstance(FEATURE_TOOLS_ANTIGRAVITY, bool)
        assert isinstance(FEATURE_TOOLS_NETWORK, bool)
        assert isinstance(FEATURE_TOOLS_HOST_EXECUTION, bool)

    def test_tool_fabric_flags_defaults(self):
        """Tool Fabric feature flags have correct defaults."""
        import os
        from src.core.feature_flags import reload_flags, _env_bool

        # Clear any existing env vars
        for key in ["FEATURE_TOOLS_FABRIC", "FEATURE_TOOLS_CLI_PROVIDERS",
                    "FEATURE_TOOLS_ANTIGRAVITY", "FEATURE_TOOLS_NETWORK",
                    "FEATURE_TOOLS_HOST_EXECUTION"]:
            os.environ.pop(key, None)

        reload_flags()

        from src.core.feature_flags import (
            FEATURE_TOOLS_FABRIC,
            FEATURE_TOOLS_CLI_PROVIDERS,
            FEATURE_TOOLS_ANTIGRAVITY,
            FEATURE_TOOLS_NETWORK,
            FEATURE_TOOLS_HOST_EXECUTION,
        )

        # TOOLS_FABRIC defaults to true
        assert FEATURE_TOOLS_FABRIC is True

        # Optional providers default to false
        assert FEATURE_TOOLS_CLI_PROVIDERS is False
        assert FEATURE_TOOLS_ANTIGRAVITY is False
        assert FEATURE_TOOLS_NETWORK is False
        assert FEATURE_TOOLS_HOST_EXECUTION is False


# =============================================================================
# JSON Serialization Round-Trip Tests
# =============================================================================


class TestJsonRoundTrip:
    """Test JSON serialization round-trips for all types."""

    def test_exec_result_json_roundtrip(self):
        """ExecResult survives JSON round-trip."""
        original = ExecResult(exit_code=1, stdout="out", stderr="err", duration_ms=100)
        json_str = json.dumps(original.to_dict())
        restored = ExecResult.from_dict(json.loads(json_str))
        assert original.to_dict() == restored.to_dict()

    def test_file_change_json_roundtrip(self):
        """FileChange survives JSON round-trip."""
        original = FileChange(path="/test", action="modified", hash_before="a", hash_after="b")
        json_str = json.dumps(original.to_dict())
        restored = FileChange.from_dict(json.loads(json_str))
        assert original.to_dict() == restored.to_dict()

    def test_provider_health_json_roundtrip(self):
        """ProviderHealth survives JSON round-trip."""
        original = ProviderHealth(
            provider_id="test",
            state=ProviderState.HEALTHY,
            capabilities=["shell_exec"],
        )
        json_str = json.dumps(original.to_dict())
        restored = ProviderHealth.from_dict(json.loads(json_str))
        assert restored.provider_id == original.provider_id
        assert restored.state == original.state

    def test_tool_intent_json_roundtrip(self):
        """ToolIntent survives JSON round-trip."""
        original = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/ws",
            risk=RiskLevel.MEDIUM,
        )
        json_str = json.dumps(original.to_dict())
        restored = ToolIntent.from_dict(json.loads(json_str))
        assert restored.capability == original.capability
        assert restored.risk == original.risk

    def test_tool_receipt_json_roundtrip(self):
        """ToolReceipt survives JSON round-trip."""
        original = ToolReceipt(
            capability="shell_exec",
            action="run",
            provider_id="local_sandbox",
            exit_code=0,
            success=True,
        )
        json_str = json.dumps(original.to_dict())
        restored = ToolReceipt.from_dict(json.loads(json_str))
        assert restored.capability == original.capability
        assert restored.success == original.success

    def test_vision_receipt_json_roundtrip(self):
        """VisionReceipt survives JSON round-trip."""
        original = VisionReceipt(
            action="capture_screen",
            screenshot_before_hash="abc",
            confidence_score=0.95,
        )
        json_str = json.dumps(original.to_dict())
        restored = VisionReceipt.from_dict(json.loads(json_str))
        assert restored.action == original.action
        assert restored.confidence_score == original.confidence_score
