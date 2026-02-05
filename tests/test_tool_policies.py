"""
Unit Tests for Tool Fabric Policies
====================================

Tests for the policy engine covering:
- Command allowlist/denylist evaluation
- Path security (traversal detection, workspace boundary)
- Network policy enforcement
- Risk level assessment
- Sensitive data redaction
- Policy snapshots

Prompt 3 — Policies
"""

import os
import pytest
import tempfile

from src.tools.policies import (
    PolicyEngine,
    PolicyConfig,
    PolicyDecision,
    create_policy_engine,
    is_safe_path,
    is_safe_command,
)
from src.tools.contracts import (
    Capability,
    RiskLevel,
    ToolIntent,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def policy_engine():
    """Create a PolicyEngine with default config."""
    return PolicyEngine()


@pytest.fixture
def strict_policy_engine():
    """Create a PolicyEngine with strict config."""
    config = PolicyConfig(
        network_allowed_default=False,
        command_allowlist=["git", "python", "pytest"],
        require_verifier_for_high_risk=True,
    )
    return PolicyEngine(config)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace."""
    workspace = tempfile.mkdtemp(prefix="policy_test_")
    yield workspace
    import shutil
    shutil.rmtree(workspace, ignore_errors=True)


# =============================================================================
# PolicyConfig Tests
# =============================================================================


class TestPolicyConfig:
    """Test PolicyConfig dataclass."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = PolicyConfig()
        assert config.network_allowed_default is False
        assert len(config.command_allowlist) > 0
        assert len(config.command_denylist) > 0
        assert "rm -rf /" in config.command_denylist

    def test_custom_config(self):
        """Config accepts custom values."""
        config = PolicyConfig(
            network_allowed_default=True,
            command_allowlist=["custom", "commands"],
        )
        assert config.network_allowed_default is True
        assert "custom" in config.command_allowlist


# =============================================================================
# Command Denylist Tests
# =============================================================================


class TestCommandDenylist:
    """Test command denylist enforcement."""

    def test_rm_rf_root_denied(self, policy_engine):
        """rm -rf / is denied."""
        decision = policy_engine.evaluate_command("rm -rf /")
        assert decision.allowed is False
        assert decision.risk_level == RiskLevel.HIGH

    def test_rm_rf_wildcard_denied(self, policy_engine):
        """rm -rf /* is denied."""
        decision = policy_engine.evaluate_command("rm -rf /*")
        assert decision.allowed is False

    def test_mkfs_denied(self, policy_engine):
        """mkfs commands are denied."""
        decision = policy_engine.evaluate_command("mkfs.ext4 /dev/sda1")
        assert decision.allowed is False

    def test_fork_bomb_denied(self, policy_engine):
        """Fork bomb is denied."""
        decision = policy_engine.evaluate_command(":(){:|:&};:")
        assert decision.allowed is False

    def test_sudo_denied(self, policy_engine):
        """sudo commands are denied."""
        decision = policy_engine.evaluate_command("sudo rm file")
        assert decision.allowed is False

    def test_credential_read_denied(self, policy_engine):
        """Reading credentials is denied."""
        decision = policy_engine.evaluate_command("cat ~/.ssh/id_rsa")
        assert decision.allowed is False

        decision = policy_engine.evaluate_command("cat /etc/shadow")
        assert decision.allowed is False


# =============================================================================
# Command Allowlist Tests
# =============================================================================


class TestCommandAllowlist:
    """Test command allowlist enforcement."""

    def test_git_allowed(self, policy_engine):
        """git commands are allowed."""
        decision = policy_engine.evaluate_command("git status")
        assert decision.allowed is True

        decision = policy_engine.evaluate_command("git commit -m 'test'")
        assert decision.allowed is True

    def test_python_allowed(self, policy_engine):
        """python commands are allowed."""
        decision = policy_engine.evaluate_command("python script.py")
        assert decision.allowed is True

        decision = policy_engine.evaluate_command("python -c 'print(1)'")
        assert decision.allowed is True

    def test_pytest_allowed(self, policy_engine):
        """pytest is allowed."""
        decision = policy_engine.evaluate_command("pytest tests/")
        assert decision.allowed is True

    def test_unknown_command_denied(self, strict_policy_engine):
        """Unknown commands are denied with strict config."""
        decision = strict_policy_engine.evaluate_command("unknown_binary arg1")
        assert decision.allowed is False

    def test_curl_not_in_strict_allowlist(self, strict_policy_engine):
        """curl is not in strict allowlist."""
        decision = strict_policy_engine.evaluate_command("curl http://example.com")
        assert decision.allowed is False


# =============================================================================
# Risk Assessment Tests
# =============================================================================


class TestRiskAssessment:
    """Test risk level assessment."""

    def test_read_commands_low_risk(self, policy_engine):
        """Read-only commands are low risk."""
        decision = policy_engine.evaluate_command("ls -la")
        assert decision.risk_level == RiskLevel.LOW

        decision = policy_engine.evaluate_command("cat file.txt")
        assert decision.risk_level == RiskLevel.LOW

    def test_pip_install_medium_risk(self, policy_engine):
        """pip install is medium risk."""
        decision = policy_engine.evaluate_command("pip install package")
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_npm_install_medium_risk(self, policy_engine):
        """npm install is medium risk."""
        decision = policy_engine.evaluate_command("npm install")
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_git_commit_medium_risk(self, policy_engine):
        """git commit is medium risk."""
        decision = policy_engine.evaluate_command("git commit -m 'test'")
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_curl_blocked_not_in_allowlist(self, policy_engine):
        """curl is blocked when not in allowlist."""
        decision = policy_engine.evaluate_command("curl http://example.com")
        # curl is not in default allowlist, so it's blocked
        assert decision.allowed is False
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_curl_high_risk_when_allowed(self):
        """curl is high risk when in allowlist."""
        config = PolicyConfig(command_allowlist=["curl"])
        engine = PolicyEngine(config)
        decision = engine.evaluate_command("curl http://example.com")
        # When allowed, curl is assessed as high risk due to network
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.HIGH

    def test_git_push_high_risk(self, policy_engine):
        """git push is high risk."""
        decision = policy_engine.evaluate_command("git push origin main")
        assert decision.risk_level == RiskLevel.HIGH

    def test_docker_push_high_risk(self, policy_engine):
        """docker push is high risk."""
        decision = policy_engine.evaluate_command("docker push myimage:latest")
        assert decision.risk_level == RiskLevel.HIGH


# =============================================================================
# Path Traversal Tests
# =============================================================================


class TestPathTraversal:
    """Test path traversal detection."""

    def test_simple_traversal_detected(self, policy_engine):
        """Simple .. traversal is detected."""
        decision = policy_engine.evaluate_path("../../etc/passwd", "/workspace")
        assert decision.allowed is False

    def test_deep_traversal_detected(self, policy_engine):
        """Deep traversal is detected."""
        decision = policy_engine.evaluate_path("../../../../../../../etc/passwd", "/workspace")
        assert decision.allowed is False

    def test_mixed_traversal_detected(self, policy_engine):
        """Mixed traversal is detected."""
        decision = policy_engine.evaluate_path("subdir/../../other/../../etc/passwd", "/workspace")
        assert decision.allowed is False

    def test_encoded_traversal_detected(self, policy_engine):
        """URL-encoded traversal is detected."""
        decision = policy_engine.evaluate_path("%2e%2e/etc/passwd", "/workspace")
        assert decision.allowed is False

    def test_double_encoded_traversal_detected(self, policy_engine):
        """Double URL-encoded traversal is detected."""
        decision = policy_engine.evaluate_path("%252e%252e/etc/passwd", "/workspace")
        assert decision.allowed is False

    def test_relative_path_within_workspace_allowed(self, policy_engine, temp_workspace):
        """Relative paths within workspace are allowed."""
        decision = policy_engine.evaluate_path("subdir/file.txt", temp_workspace)
        assert decision.allowed is True

    def test_safe_relative_path(self, policy_engine, temp_workspace):
        """Safe relative paths are allowed."""
        decision = policy_engine.evaluate_path("./src/main.py", temp_workspace)
        assert decision.allowed is True


# =============================================================================
# Workspace Boundary Tests
# =============================================================================


class TestWorkspaceBoundary:
    """Test workspace boundary enforcement."""

    def test_absolute_path_outside_workspace_denied(self, policy_engine, temp_workspace):
        """Absolute paths outside workspace are denied."""
        decision = policy_engine.evaluate_path("/etc/passwd", temp_workspace)
        assert decision.allowed is False

    def test_path_within_workspace_allowed(self, policy_engine, temp_workspace):
        """Paths within workspace are allowed."""
        inner_path = os.path.join(temp_workspace, "src", "main.py")
        decision = policy_engine.evaluate_path(inner_path, temp_workspace)
        assert decision.allowed is True

    def test_command_with_outside_path_warns(self, policy_engine, temp_workspace):
        """Commands with paths outside workspace produce warnings."""
        decision = policy_engine.evaluate_command(
            "cat /etc/hosts",
            workspace=temp_workspace,
        )
        # Command may still be allowed but should warn
        if decision.allowed:
            assert len(decision.warnings) > 0


# =============================================================================
# Sensitive Path Tests
# =============================================================================


class TestSensitivePaths:
    """Test sensitive path patterns."""

    def test_env_file_denied(self, policy_engine, temp_workspace):
        """Access to .env files is denied."""
        decision = policy_engine.evaluate_path(".env", temp_workspace)
        assert decision.allowed is False

        decision = policy_engine.evaluate_path(".env.local", temp_workspace)
        assert decision.allowed is False

    def test_ssh_keys_denied(self, policy_engine):
        """Access to SSH keys is denied."""
        decision = policy_engine.evaluate_path("/home/user/.ssh/id_rsa")
        assert decision.allowed is False

    def test_aws_credentials_denied(self, policy_engine):
        """Access to AWS credentials is denied."""
        decision = policy_engine.evaluate_path("/home/user/.aws/credentials")
        assert decision.allowed is False

    def test_secrets_yaml_denied(self, policy_engine, temp_workspace):
        """Access to secrets.yaml is denied."""
        decision = policy_engine.evaluate_path("secrets.yaml", temp_workspace)
        assert decision.allowed is False

        decision = policy_engine.evaluate_path("secret.yml", temp_workspace)
        assert decision.allowed is False


# =============================================================================
# Network Policy Tests
# =============================================================================


class TestNetworkPolicy:
    """Test network policy enforcement."""

    def test_network_denied_by_default(self, policy_engine):
        """Network is denied by default."""
        decision = policy_engine.evaluate_network(Capability.SHELL_EXEC)
        assert decision.allowed is False

    def test_network_allowed_for_web_ops(self, policy_engine):
        """Network is allowed for WebOps capability."""
        decision = policy_engine.evaluate_network(Capability.WEB_OPS)
        assert decision.allowed is True

    def test_network_explicit_request_denied(self, policy_engine):
        """Explicit network request denied for shell."""
        decision = policy_engine.evaluate_network(
            Capability.SHELL_EXEC,
            explicit_request=True,
        )
        assert decision.allowed is False

    def test_network_allowed_with_config(self):
        """Network allowed when configured."""
        config = PolicyConfig(network_allowed_default=True)
        engine = PolicyEngine(config)

        decision = engine.evaluate_network(Capability.SHELL_EXEC)
        assert decision.allowed is True
        assert decision.requires_approval is True  # High risk


# =============================================================================
# Tool Intent Tests
# =============================================================================


class TestToolIntent:
    """Test tool intent evaluation."""

    def test_safe_intent_allowed(self, policy_engine, temp_workspace):
        """Safe intents are allowed."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace=temp_workspace,
            risk=RiskLevel.LOW,
            inputs={"command": "git status"},
        )
        decision = policy_engine.evaluate_intent(intent, temp_workspace)
        assert decision.allowed is True

    def test_intent_with_denied_command(self, policy_engine, temp_workspace):
        """Intents with denied commands are blocked."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace=temp_workspace,
            risk=RiskLevel.LOW,
            inputs={"command": "rm -rf /"},
        )
        decision = policy_engine.evaluate_intent(intent, temp_workspace)
        assert decision.allowed is False

    def test_intent_with_path_traversal(self, policy_engine, temp_workspace):
        """Intents with path traversal are blocked."""
        intent = ToolIntent(
            capability=Capability.FILE_OPS,
            action="read",
            workspace=temp_workspace,
            risk=RiskLevel.LOW,
            inputs={"path": "../../etc/passwd"},
        )
        decision = policy_engine.evaluate_intent(intent, temp_workspace)
        assert decision.allowed is False

    def test_vision_control_requires_approval(self, policy_engine):
        """VisionControl always requires approval."""
        intent = ToolIntent(
            capability=Capability.VISION_CONTROL,
            action="capture_screen",
            workspace="/workspace",
            risk=RiskLevel.LOW,
            inputs={},
        )
        decision = policy_engine.evaluate_intent(intent)
        assert decision.requires_approval is True


# =============================================================================
# Redaction Tests
# =============================================================================


class TestRedaction:
    """Test sensitive data redaction."""

    def test_redact_password(self, policy_engine):
        """Passwords are redacted."""
        text = "password='secret123'"
        redacted = policy_engine.redact_sensitive(text)
        assert "secret123" not in redacted
        assert "REDACTED" in redacted

    def test_redact_api_key(self, policy_engine):
        """API keys are redacted."""
        text = "api_key: sk-1234567890abcdef"
        redacted = policy_engine.redact_sensitive(text)
        assert "sk-1234567890" not in redacted

    def test_redact_bearer_token(self, policy_engine):
        """Bearer tokens are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted = policy_engine.redact_sensitive(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    def test_redact_openai_key(self, policy_engine):
        """OpenAI keys are redacted."""
        text = "OPENAI_API_KEY=sk-abc123def456ghi789"
        redacted = policy_engine.redact_sensitive(text)
        assert "sk-abc123" not in redacted

    def test_redact_github_token(self, policy_engine):
        """GitHub tokens are redacted."""
        text = "token: ghp_abcdefghijklmnop123456789"
        redacted = policy_engine.redact_sensitive(text)
        assert "ghp_abcdefghij" not in redacted

    def test_redact_paths(self, policy_engine, temp_workspace):
        """Paths are redacted."""
        text = f"Working in {temp_workspace}/project"
        redacted = policy_engine.redact_paths(text, temp_workspace)
        assert temp_workspace not in redacted
        assert "workspace:" in redacted


# =============================================================================
# Policy Snapshot Tests
# =============================================================================


class TestPolicySnapshot:
    """Test policy snapshot creation."""

    def test_snapshot_creation(self, policy_engine, temp_workspace):
        """Policy snapshot captures current state."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace=temp_workspace,
            risk=RiskLevel.LOW,
            inputs={"command": "ls"},
        )
        decision = policy_engine.evaluate_intent(intent, temp_workspace)
        snapshot = policy_engine.create_snapshot(intent, decision)

        assert snapshot.network_allowed is False
        assert len(snapshot.allowlist_applied) > 0
        assert snapshot.risk_level == RiskLevel.LOW

    def test_snapshot_serialization(self, policy_engine, temp_workspace):
        """Policy snapshot serializes correctly."""
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace=temp_workspace,
            risk=RiskLevel.MEDIUM,
            inputs={},
        )
        decision = PolicyDecision(allowed=True, risk_level=RiskLevel.MEDIUM)
        snapshot = policy_engine.create_snapshot(intent, decision)

        d = snapshot.to_dict()
        assert "network_allowed" in d
        assert "risk_level" in d
        assert d["risk_level"] == "medium"


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_create_policy_engine_defaults(self):
        """create_policy_engine with defaults."""
        engine = create_policy_engine()
        assert engine.config.network_allowed_default is False

    def test_create_policy_engine_custom(self):
        """create_policy_engine with custom settings."""
        engine = create_policy_engine(
            network_allowed=True,
            command_allowlist=["custom"],
            require_approval_for_high_risk=False,
        )
        assert engine.config.network_allowed_default is True
        assert "custom" in engine.config.command_allowlist

    def test_is_safe_path_true(self, temp_workspace):
        """is_safe_path returns True for safe paths."""
        result = is_safe_path("src/main.py", temp_workspace)
        assert result is True

    def test_is_safe_path_false(self, temp_workspace):
        """is_safe_path returns False for unsafe paths."""
        result = is_safe_path("../../etc/passwd", temp_workspace)
        assert result is False

    def test_is_safe_command_true(self):
        """is_safe_command returns True for safe commands."""
        result = is_safe_command("git status")
        assert result is True

    def test_is_safe_command_false(self):
        """is_safe_command returns False for unsafe commands."""
        result = is_safe_command("rm -rf /")
        assert result is False


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_command(self, policy_engine):
        """Empty command is handled."""
        decision = policy_engine.evaluate_command("")
        assert decision.allowed is False  # No executable found

    def test_whitespace_command(self, policy_engine):
        """Whitespace-only command is handled."""
        decision = policy_engine.evaluate_command("   ")
        assert decision.allowed is False

    def test_very_long_command(self, policy_engine):
        """Very long commands are handled."""
        long_cmd = "echo " + "x" * 10000
        decision = policy_engine.evaluate_command(long_cmd)
        assert decision is not None  # Should not crash

    def test_unicode_in_command(self, policy_engine):
        """Unicode in commands is handled."""
        decision = policy_engine.evaluate_command("echo '你好世界'")
        # Should not crash, echo is allowed
        assert decision.allowed is True

    def test_null_workspace(self, policy_engine):
        """None workspace is handled."""
        decision = policy_engine.evaluate_path("file.txt", None)
        # Should work without workspace check
        assert decision is not None

    def test_mixed_case_denylist(self, policy_engine):
        """Denylist is case-insensitive."""
        decision = policy_engine.evaluate_command("RM -RF /")
        assert decision.allowed is False

        decision = policy_engine.evaluate_command("MKFS.ext4 /dev/sda1")
        assert decision.allowed is False
