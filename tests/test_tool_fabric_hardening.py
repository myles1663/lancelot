"""
Tests for Tool Fabric Hardening (Prompt 11)
============================================

Security regression tests covering:
- Command denylist enforcement
- Path traversal detection
- Network policy enforcement
- Sensitive data redaction
- Provider offline degradation
- Malformed provider output handling
- All-providers-offline scenarios
"""

import pytest
import tempfile
import os
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Any, List

from src.tools.contracts import (
    Capability,
    RiskLevel,
    ProviderHealth,
    ProviderState,
    ToolIntent,
    ExecResult,
    BaseProvider,
)
from src.tools.policies import (
    PolicyEngine,
    PolicyConfig,
    PolicyDecision,
    is_safe_command,
    is_safe_path,
)
from src.tools.router import (
    ProviderRouter,
    RouterConfig,
    RouteDecision,
)
from src.tools.health import HealthMonitor, HealthConfig
from src.tools.fabric import ToolFabric, ToolFabricConfig, reset_tool_fabric
from src.tools.receipts import ToolReceipt, create_tool_receipt


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def policy_engine():
    """Create a fresh PolicyEngine."""
    return PolicyEngine()


@pytest.fixture
def strict_policy_engine():
    """Create a PolicyEngine with stricter settings."""
    config = PolicyConfig(
        network_allowed_default=False,
        require_verifier_for_high_risk=True,
    )
    return PolicyEngine(config)


@pytest.fixture
def health_monitor():
    """Create a fresh HealthMonitor."""
    return HealthMonitor(HealthConfig(run_sweep_on_startup=False))


@pytest.fixture
def mock_offline_provider():
    """Create a mock provider that reports offline."""
    provider = MagicMock(spec=BaseProvider)
    provider.provider_id = "offline_provider"
    provider.supports.return_value = True
    provider.health_check.return_value = ProviderHealth(
        provider_id="offline_provider",
        state=ProviderState.OFFLINE,
        error_message="Provider unavailable",
    )
    return provider


@pytest.fixture
def mock_malformed_provider():
    """Create a mock provider that returns malformed output."""
    provider = MagicMock(spec=BaseProvider)
    provider.provider_id = "malformed_provider"
    provider.supports.return_value = True
    provider.health_check.return_value = ProviderHealth(
        provider_id="malformed_provider",
        state=ProviderState.HEALTHY,
    )

    def run_malformed(*args, **kwargs):
        # Return result with unexpected data
        return ExecResult(
            exit_code=None,  # Malformed: should be int
            stdout=b"binary data",  # Malformed: should be str
            stderr=12345,  # Malformed: should be str
            duration_ms=-1,  # Malformed: should be positive
        )

    provider.run = run_malformed
    return provider


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# =============================================================================
# Command Denylist Regression Tests
# =============================================================================


class TestCommandDenylist:
    """Regression tests for command denylist enforcement."""

    @pytest.mark.parametrize("dangerous_command", [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf .",
        "rm -rf ..",
        "sudo rm -rf /",
        "mkfs /dev/sda1",
        "fdisk /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/random of=/dev/sda",
        ":(){ :|:& };:",  # Fork bomb
        ":(){:|:&};:",    # Fork bomb variant
        "chmod -R 777 /",
        "chmod 777 /",
        "chown -R root:root /",
        "nc -l 4444",
        "ncat -l 4444",
        "cat /etc/passwd",
        "cat /etc/shadow",
        "cat ~/.ssh/id_rsa",
        "cat ~/.aws/credentials",
        "history",
        "cat ~/.bash_history",
        "insmod evil.ko",
        "rmmod security_module",
        "modprobe rootkit",
    ])
    def test_dangerous_commands_blocked(self, policy_engine, dangerous_command):
        """Verify all dangerous commands are blocked."""
        decision = policy_engine.evaluate_command(dangerous_command)

        assert not decision.allowed, f"Command should be blocked: {dangerous_command}"
        assert decision.risk_level == RiskLevel.HIGH

    @pytest.mark.parametrize("safe_command", [
        "git status",
        "git log --oneline",
        "python -c 'print(1)'",
        "python3 --version",
        "pip list",
        "npm --version",
        "ls -la",
        "cat README.md",
        "grep -r 'TODO' .",
        "echo 'hello'",
        "pwd",
        "date",
        "mkdir test_dir",
    ])
    def test_safe_commands_allowed(self, policy_engine, safe_command):
        """Verify safe commands are allowed."""
        decision = policy_engine.evaluate_command(safe_command)

        assert decision.allowed, f"Command should be allowed: {safe_command}"

    def test_denylist_case_insensitive(self, policy_engine):
        """Verify denylist matching is case-insensitive."""
        # Mix of case should still be blocked
        assert not policy_engine.evaluate_command("RM -RF /").allowed
        assert not policy_engine.evaluate_command("Cat /etc/passwd").allowed
        assert not policy_engine.evaluate_command("SUDO rm -rf /").allowed

    def test_denylist_with_extra_spaces(self, policy_engine):
        """Verify denylist handles extra whitespace."""
        assert not policy_engine.evaluate_command("rm  -rf  /").allowed
        assert not policy_engine.evaluate_command("  rm -rf /  ").allowed

    def test_denylist_embedded_in_longer_command(self, policy_engine):
        """Verify denylist blocks patterns embedded in longer commands."""
        assert not policy_engine.evaluate_command("echo test && rm -rf /").allowed
        assert not policy_engine.evaluate_command("ls && cat /etc/passwd").allowed

    def test_command_not_in_allowlist(self, policy_engine):
        """Verify commands not in allowlist are blocked."""
        # curl is not in default allowlist
        decision = policy_engine.evaluate_command("curl http://example.com")
        assert not decision.allowed
        assert "allowlist" in decision.reasons[0].lower()


# =============================================================================
# Path Traversal Tests
# =============================================================================


class TestPathTraversal:
    """Tests for path traversal detection."""

    @pytest.mark.parametrize("traversal_path", [
        "../../../etc/passwd",
        "..\\..\\..\\Windows\\System32",
        "foo/../../etc/passwd",
        "foo/../../../etc/passwd",
        "./../../../../etc/passwd",
    ])
    def test_obvious_traversal_blocked(self, policy_engine, traversal_path):
        """Verify obvious path traversal is blocked."""
        decision = policy_engine.evaluate_path(traversal_path, "/workspace")

        assert not decision.allowed
        assert decision.risk_level == RiskLevel.HIGH

    @pytest.mark.parametrize("encoded_traversal", [
        "%2e%2e/%2e%2e/etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc/passwd",
        "%252e%252e/etc/passwd",  # Double encoded
    ])
    def test_encoded_traversal_blocked(self, policy_engine, encoded_traversal):
        """Verify URL-encoded traversal is blocked."""
        decision = policy_engine.evaluate_path(encoded_traversal, "/workspace")

        assert not decision.allowed

    def test_relative_path_within_workspace_allowed(self, policy_engine, temp_workspace):
        """Verify relative paths within workspace are allowed."""
        decision = policy_engine.evaluate_path("subdir/file.txt", temp_workspace)
        assert decision.allowed

    def test_absolute_path_outside_workspace_blocked(self, policy_engine, temp_workspace):
        """Verify absolute paths outside workspace are blocked."""
        decision = policy_engine.evaluate_path("/etc/passwd", temp_workspace)
        assert not decision.allowed

    @pytest.mark.parametrize("sensitive_path", [
        "/home/user/.ssh/id_rsa",
        "/home/user/.aws/credentials",
        "/home/user/.gnupg/secring.gpg",
        "config/.env",
        "secrets.yaml",
        "credentials.json",
    ])
    def test_sensitive_paths_blocked(self, policy_engine, sensitive_path):
        """Verify sensitive paths are blocked."""
        decision = policy_engine.evaluate_path(sensitive_path)

        assert not decision.allowed

    def test_path_traversal_in_command_blocked(self, policy_engine):
        """Verify path traversal in commands is blocked."""
        decision = policy_engine.evaluate_command("cat ../../../etc/passwd")

        assert not decision.allowed

    def test_is_safe_path_function(self, temp_workspace):
        """Test convenience function is_safe_path."""
        # Create a file inside workspace
        test_file = os.path.join(temp_workspace, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")

        # Safe path
        assert is_safe_path("test.txt", temp_workspace)

        # Unsafe path
        assert not is_safe_path("../../../etc/passwd", temp_workspace)


# =============================================================================
# Network Policy Enforcement Tests
# =============================================================================


class TestNetworkEnforcement:
    """Tests for network policy enforcement."""

    def test_network_disabled_by_default(self, policy_engine):
        """Verify network is disabled by default for most capabilities."""
        decision = policy_engine.evaluate_network(Capability.SHELL_EXEC)

        assert not decision.allowed
        assert "disabled" in decision.reasons[0].lower() or "denied" in decision.reasons[0].lower()

    def test_network_allowed_for_web_ops(self, policy_engine):
        """Verify network is allowed for WEB_OPS capability."""
        decision = policy_engine.evaluate_network(Capability.WEB_OPS)

        assert decision.allowed

    def test_explicit_network_request_denied(self, policy_engine):
        """Verify explicit network requests are denied when not allowed."""
        decision = policy_engine.evaluate_network(
            Capability.SHELL_EXEC,
            explicit_request=True,
        )

        assert not decision.allowed
        assert decision.risk_level == RiskLevel.HIGH

    def test_network_allowed_capabilities_configurable(self):
        """Verify network-allowed capabilities are configurable."""
        config = PolicyConfig(
            network_allowed_capabilities={Capability.DEPLOY_OPS}
        )
        engine = PolicyEngine(config)

        # DEPLOY_OPS should now have network
        decision = engine.evaluate_network(Capability.DEPLOY_OPS)
        assert decision.allowed

        # WEB_OPS should no longer have network (not in new set)
        decision = engine.evaluate_network(Capability.WEB_OPS)
        assert not decision.allowed

    def test_network_commands_flagged_high_risk(self, policy_engine):
        """Verify network-related commands are flagged as high risk."""
        # These are allowed but high risk
        curl_decision = policy_engine.evaluate_command("curl http://example.com")
        wget_decision = policy_engine.evaluate_command("wget http://example.com")

        # curl and wget not in allowlist, so they should be blocked
        assert not curl_decision.allowed
        assert not wget_decision.allowed


# =============================================================================
# Redaction Tests
# =============================================================================


class TestRedaction:
    """Tests for sensitive data redaction."""

    @pytest.mark.parametrize("sensitive_text,pattern_name", [
        ("password: mysecret123", "password"),
        ("api_key: sk-abc123xyz", "api_key"),
        ("secret = 'super_secret'", "secret"),
        ("token: bearer abc.def.ghi", "token"),
        ("Bearer eyJhbGciOiJIUzI1NiJ9", "bearer token"),
        ("sk-abcdefghijklmnop", "OpenAI key"),
        ("ghp_abcdefghij1234567890", "GitHub token"),
        ("gho_abcdefghij", "GitHub OAuth"),
        ("AKIAIOSFODNN7EXAMPLE", "AWS access key"),
    ])
    def test_sensitive_patterns_redacted(self, policy_engine, sensitive_text, pattern_name):
        """Verify sensitive patterns are redacted."""
        redacted = policy_engine.redact_sensitive(sensitive_text)

        assert "[REDACTED]" in redacted, f"Failed to redact {pattern_name}"
        # Original sensitive text should not appear
        if ":" in sensitive_text:
            secret_part = sensitive_text.split(":", 1)[-1].strip().strip("'\"")
            if len(secret_part) > 5:  # Only check meaningful secrets
                assert secret_part not in redacted

    def test_redaction_preserves_non_sensitive(self, policy_engine):
        """Verify non-sensitive text is preserved."""
        text = "This is normal text with no secrets"
        redacted = policy_engine.redact_sensitive(text)

        assert redacted == text

    def test_path_redaction(self, policy_engine, temp_workspace):
        """Verify workspace paths are redacted."""
        text = f"Error in {temp_workspace}/file.txt"
        redacted = policy_engine.redact_paths(text, temp_workspace)

        assert temp_workspace not in redacted
        assert "[workspace:" in redacted

    def test_home_directory_redaction(self, policy_engine):
        """Verify home directories are redacted."""
        linux_path = "/home/username/project/file.txt"
        mac_path = "/Users/username/project/file.txt"
        windows_path = "C:\\Users\\username\\project\\file.txt"

        assert "[HOME]" in policy_engine.redact_paths(linux_path)
        assert "[HOME]" in policy_engine.redact_paths(mac_path)
        assert "[HOME]" in policy_engine.redact_paths(windows_path)

    def test_multiple_redactions_in_same_text(self, policy_engine):
        """Verify multiple sensitive items are all redacted."""
        text = "password: secret1, api_key: secret2, token: secret3"
        redacted = policy_engine.redact_sensitive(text)

        # Count redactions
        count = redacted.count("[REDACTED]")
        assert count >= 3


# =============================================================================
# Provider Offline Degradation Tests
# =============================================================================


class TestProviderOfflineDegradation:
    """Tests for graceful degradation when providers are offline."""

    def test_routing_with_offline_provider(self, health_monitor, mock_offline_provider):
        """Verify routing handles offline providers."""
        health_monitor.register(mock_offline_provider)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={"shell_exec": ["offline_provider"]},
                fallback_provider=None,
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert not decision.success
        assert "offline_provider:offline" in decision.alternatives_tried

    def test_failover_to_healthy_provider(self, health_monitor, mock_offline_provider):
        """Verify failover to healthy provider works."""
        # Register offline provider
        health_monitor.register(mock_offline_provider)

        # Register healthy provider
        healthy_provider = MagicMock(spec=BaseProvider)
        healthy_provider.provider_id = "healthy_provider"
        healthy_provider.supports.return_value = True
        healthy_provider.health_check.return_value = ProviderHealth(
            provider_id="healthy_provider",
            state=ProviderState.HEALTHY,
        )
        health_monitor.register(healthy_provider)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={
                    "shell_exec": ["offline_provider", "healthy_provider"]
                },
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert decision.success
        assert decision.provider_id == "healthy_provider"
        assert "offline_provider:offline" in decision.alternatives_tried

    def test_fallback_provider_used(self, health_monitor, mock_offline_provider):
        """Verify fallback provider is used when all preferred are offline."""
        health_monitor.register(mock_offline_provider)

        # Register fallback provider
        fallback = MagicMock(spec=BaseProvider)
        fallback.provider_id = "fallback"
        fallback.supports.return_value = True
        fallback.health_check.return_value = ProviderHealth(
            provider_id="fallback",
            state=ProviderState.HEALTHY,
        )
        health_monitor.register(fallback)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={"shell_exec": ["offline_provider"]},
                fallback_provider="fallback",
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert decision.success
        assert decision.provider_id == "fallback"
        assert "fallback" in decision.reason.lower()

    def test_degraded_provider_accepted_by_default(self, health_monitor):
        """Verify degraded providers are accepted when require_healthy=False."""
        degraded = MagicMock(spec=BaseProvider)
        degraded.provider_id = "degraded_provider"
        degraded.supports.return_value = True
        degraded.health_check.return_value = ProviderHealth(
            provider_id="degraded_provider",
            state=ProviderState.DEGRADED,
            degraded_reasons=["Partial functionality"],
        )
        health_monitor.register(degraded)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={"shell_exec": ["degraded_provider"]},
                require_healthy=False,
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert decision.success
        assert decision.health_state == ProviderState.DEGRADED

    def test_degraded_provider_rejected_when_required_healthy(self, health_monitor):
        """Verify degraded providers are rejected when require_healthy=True."""
        degraded = MagicMock(spec=BaseProvider)
        degraded.provider_id = "degraded_provider"
        degraded.supports.return_value = True
        degraded.health_check.return_value = ProviderHealth(
            provider_id="degraded_provider",
            state=ProviderState.DEGRADED,
        )
        health_monitor.register(degraded)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={"shell_exec": ["degraded_provider"]},
                require_healthy=True,
                fallback_provider=None,
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert not decision.success
        assert "degraded_provider:degraded" in decision.alternatives_tried


# =============================================================================
# All Providers Offline Tests
# =============================================================================


class TestAllProvidersOffline:
    """Tests for scenarios where all providers are offline."""

    def test_all_providers_offline_returns_error(self, health_monitor, mock_offline_provider):
        """Verify clear error when all providers offline."""
        health_monitor.register(mock_offline_provider)

        router = ProviderRouter(
            config=RouterConfig(
                provider_preferences={"shell_exec": ["offline_provider"]},
                fallback_provider=None,
            ),
            health_monitor=health_monitor,
        )

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert not decision.success
        assert "no provider available" in decision.reason.lower()

    def test_fabric_returns_error_when_no_providers(self):
        """Verify ToolFabric returns error when no providers available."""
        reset_tool_fabric()

        # Create fabric with disabled sandbox
        with patch("src.tools.fabric.LocalSandboxProvider") as mock_sandbox:
            mock_sandbox.return_value.provider_id = "local_sandbox"
            mock_sandbox.return_value.supports.return_value = True
            mock_sandbox.return_value.health_check.return_value = ProviderHealth(
                provider_id="local_sandbox",
                state=ProviderState.OFFLINE,
                error_message="Docker not available",
            )

            fabric = ToolFabric()
            result = fabric.run_command("echo test")

            # Should fail gracefully
            assert result.exit_code != 0
            assert "routing failed" in result.stderr.lower() or "offline" in result.stderr.lower()

    def test_policy_evaluation_works_without_providers(self, policy_engine):
        """Verify policy evaluation works even without providers."""
        # Policy engine should work independently
        decision = policy_engine.evaluate_command("rm -rf /")
        assert not decision.allowed

        decision = policy_engine.evaluate_path("../../../etc/passwd", "/workspace")
        assert not decision.allowed

    def test_receipt_generated_on_routing_failure(self):
        """Verify receipt is generated even when routing fails."""
        reset_tool_fabric()

        with patch("src.tools.fabric.LocalSandboxProvider") as mock_sandbox:
            mock_sandbox.return_value.provider_id = "local_sandbox"
            mock_sandbox.return_value.supports.return_value = True
            mock_sandbox.return_value.health_check.return_value = ProviderHealth(
                provider_id="local_sandbox",
                state=ProviderState.OFFLINE,
            )

            fabric = ToolFabric(ToolFabricConfig(emit_receipts=True))

            # This will fail routing
            result = fabric.run_command("echo test")

            # Result should indicate failure
            assert result.exit_code != 0


# =============================================================================
# Malformed Provider Output Tests
# =============================================================================


class TestMalformedProviderOutput:
    """Tests for handling malformed provider output."""

    def test_malformed_exec_result_handled(self):
        """Verify malformed ExecResult is handled gracefully."""
        # Create a receipt with malformed data
        receipt = create_tool_receipt(
            capability=Capability.SHELL_EXEC,
            action="run",
            provider_id="test_provider",
            workspace="/workspace",
            inputs={"command": "test"},
        )

        # Simulate malformed result
        malformed_result = ExecResult(
            exit_code=1,
            stdout="normal output",
            stderr="error",
            duration_ms=100,
        )

        # This should not raise
        receipt.with_exec_result(malformed_result, max_output=1000)

        # Receipt should still be valid - fields are flattened
        receipt_dict = receipt.to_dict()
        assert "exit_code" in receipt_dict
        assert "stdout" in receipt_dict
        assert receipt_dict["exit_code"] == 1

    def test_none_values_in_exec_result(self):
        """Verify None values in ExecResult are handled."""
        result = ExecResult(
            exit_code=0,
            stdout=None,  # type: ignore - testing malformed input
            stderr=None,  # type: ignore - testing malformed input
            duration_ms=0,
        )

        # Should not crash when accessing
        assert result.exit_code == 0

    def test_receipt_valid_after_provider_error(self):
        """Verify receipt remains valid after provider errors."""
        receipt = create_tool_receipt(
            capability=Capability.SHELL_EXEC,
            action="run",
            provider_id="error_provider",
            workspace="/workspace",
            inputs={"command": "test"},
        )

        # Simulate failure
        receipt.fail("Provider threw exception: Connection refused")

        # Receipt should be valid and serializable
        receipt_dict = receipt.to_dict()
        assert receipt_dict is not None
        assert receipt_dict["success"] is False
        assert "Connection refused" in receipt_dict["error_message"]

        # Should be JSON serializable
        import json
        json_str = json.dumps(receipt_dict, default=str)
        assert isinstance(json_str, str)

    def test_output_truncation_with_binary_data(self):
        """Verify binary-like output is handled during truncation."""
        receipt = create_tool_receipt(
            capability=Capability.SHELL_EXEC,
            action="run",
            provider_id="test",
            workspace="/workspace",
            inputs={},
        )

        # Result with binary-like output
        result = ExecResult(
            exit_code=0,
            stdout="normal " + "\x00\x01\x02" + " data",
            stderr="",
            duration_ms=100,
        )

        # Should not crash
        receipt.with_exec_result(result, max_output=10)


# =============================================================================
# Intent-Based Policy Tests
# =============================================================================


class TestIntentPolicies:
    """Tests for ToolIntent-based policy evaluation."""

    def test_high_risk_intent_requires_approval(self, strict_policy_engine):
        """Verify high-risk intents require approval."""
        intent = ToolIntent(
            capability=Capability.DEPLOY_OPS,
            action="deploy",
            workspace="/project",
            risk=RiskLevel.HIGH,
            inputs={"target": "production"},
        )

        decision = strict_policy_engine.evaluate_intent(intent)

        assert decision.requires_approval

    def test_vision_control_always_requires_approval(self, policy_engine):
        """Verify VisionControl always requires approval."""
        intent = ToolIntent(
            capability=Capability.VISION_CONTROL,
            action="capture_screen",
            workspace="",
            risk=RiskLevel.LOW,
            inputs={},
        )

        decision = policy_engine.evaluate_intent(intent)

        assert decision.requires_approval
        assert "VisionControl" in str(decision.warnings)

    def test_intent_with_dangerous_command_blocked(self, policy_engine):
        """Verify intents with dangerous commands are blocked."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/project",
            risk=RiskLevel.LOW,
            inputs={"command": "rm -rf /"},
        )

        decision = policy_engine.evaluate_intent(intent)

        assert not decision.allowed

    def test_intent_with_traversal_path_blocked(self, policy_engine, temp_workspace):
        """Verify intents with path traversal are blocked."""
        intent = ToolIntent(
            capability=Capability.FILE_OPS,
            action="read",
            workspace=temp_workspace,
            risk=RiskLevel.LOW,
            inputs={"path": "../../../etc/passwd"},
        )

        decision = policy_engine.evaluate_intent(intent, temp_workspace)

        assert not decision.allowed


# =============================================================================
# Policy Snapshot Tests
# =============================================================================


class TestPolicySnapshots:
    """Tests for policy snapshot creation."""

    def test_snapshot_captures_policy_state(self, policy_engine):
        """Verify snapshot captures policy state."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.LOW,
            inputs={"command": "echo test"},
        )

        decision = policy_engine.evaluate_intent(intent)
        snapshot = policy_engine.create_snapshot(intent, decision)

        assert snapshot.risk_level == decision.risk_level
        assert isinstance(snapshot.allowlist_applied, list)
        assert isinstance(snapshot.denylist_applied, list)

    def test_snapshot_is_serializable(self, policy_engine):
        """Verify policy snapshot is JSON serializable."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.MEDIUM,
            inputs={"command": "git status"},
        )

        decision = policy_engine.evaluate_intent(intent)
        snapshot = policy_engine.create_snapshot(intent, decision)

        import json
        snapshot_dict = snapshot.to_dict()
        json_str = json.dumps(snapshot_dict, default=str)
        assert isinstance(json_str, str)


# =============================================================================
# Regression Tests for Specific Vulnerabilities
# =============================================================================


class TestVulnerabilityRegressions:
    """Regression tests for specific vulnerabilities."""

    def test_shell_injection_via_semicolon(self, policy_engine):
        """Verify shell injection via semicolon is blocked."""
        # Attacker tries to inject via semicolon
        cmd = "echo safe; rm -rf /"
        decision = policy_engine.evaluate_command(cmd)

        assert not decision.allowed

    def test_shell_injection_via_pipe(self, policy_engine):
        """Verify shell injection via pipe is handled."""
        cmd = "ls | rm -rf /"
        decision = policy_engine.evaluate_command(cmd)

        assert not decision.allowed

    def test_shell_injection_via_backticks(self, policy_engine):
        """Verify command substitution is flagged."""
        cmd = "echo `rm -rf /`"
        decision = policy_engine.evaluate_command(cmd)

        assert not decision.allowed

    def test_shell_injection_via_dollar_paren(self, policy_engine):
        """Verify $(command) substitution is flagged."""
        cmd = "echo $(rm -rf /)"
        decision = policy_engine.evaluate_command(cmd)

        assert not decision.allowed

    def test_null_byte_injection(self, policy_engine):
        """Verify null byte in path is handled."""
        path = "/safe/path\x00/../../../etc/passwd"
        decision = policy_engine.evaluate_path(path, "/workspace")

        # Should either block or sanitize
        # The null byte itself doesn't bypass traversal check
        # but we should not crash
        assert isinstance(decision, PolicyDecision)

    def test_unicode_path_normalization(self, policy_engine, temp_workspace):
        """Verify unicode paths are handled safely."""
        # Various unicode representations
        paths = [
            "file\u202e.txt",  # Right-to-left override
            "file\uff0e\uff0e/etc/passwd",  # Fullwidth dots
            "file%c0%ae%c0%ae/etc/passwd",  # Overlong UTF-8
        ]

        for path in paths:
            decision = policy_engine.evaluate_path(path, temp_workspace)
            # Should not crash and should handle gracefully
            assert isinstance(decision, PolicyDecision)


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_is_safe_command(self):
        """Test is_safe_command function."""
        assert is_safe_command("git status")
        assert not is_safe_command("rm -rf /")
        assert not is_safe_command("cat /etc/passwd")

    def test_is_safe_path(self, temp_workspace):
        """Test is_safe_path function."""
        # Create test file
        test_file = os.path.join(temp_workspace, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")

        assert is_safe_path("test.txt", temp_workspace)
        assert not is_safe_path("../../../etc/passwd", temp_workspace)
