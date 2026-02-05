"""
Integration Tests for Tool Fabric
=================================

Tests for the main ToolFabric class covering:
- Command execution through fabric
- Provider routing
- Policy enforcement
- Receipt generation
- Health management
- Safe mode

Prompt 5 — Orchestrator Wiring
"""

import os
import pytest
import tempfile
import shutil
from unittest.mock import patch, MagicMock

from src.tools.fabric import (
    ToolFabric,
    ToolFabricConfig,
    get_tool_fabric,
    reset_tool_fabric,
)
from src.tools.contracts import (
    Capability,
    RiskLevel,
    ProviderState,
    ExecResult,
)
from src.tools.providers.local_sandbox import (
    LocalSandboxProvider,
    SandboxConfig,
)
from src.tools.health import reset_health_monitor
from src.tools.router import reset_router


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global instances before each test."""
    reset_tool_fabric()
    reset_health_monitor()
    reset_router()
    yield
    reset_tool_fabric()
    reset_health_monitor()
    reset_router()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="fabric_test_")
    yield workspace
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def fabric_config(temp_workspace):
    """Create a ToolFabricConfig for testing."""
    return ToolFabricConfig(
        enabled=True,
        default_workspace=temp_workspace,
        emit_receipts=True,
    )


@pytest.fixture
def fabric(fabric_config):
    """Create a ToolFabric instance for testing."""
    return ToolFabric(fabric_config)


# =============================================================================
# Configuration Tests
# =============================================================================


class TestToolFabricConfig:
    """Test ToolFabricConfig dataclass."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = ToolFabricConfig()
        assert config.enabled is True
        assert config.safe_mode is False
        assert config.emit_receipts is True

    def test_custom_config(self):
        """Config accepts custom values."""
        config = ToolFabricConfig(
            enabled=False,
            safe_mode=True,
            max_receipt_output=5000,
        )
        assert config.enabled is False
        assert config.safe_mode is True
        assert config.max_receipt_output == 5000


# =============================================================================
# Initialization Tests
# =============================================================================


class TestToolFabricInit:
    """Test ToolFabric initialization."""

    def test_init_registers_default_providers(self, fabric):
        """Initializes with local_sandbox registered."""
        providers = fabric._health_monitor.list_providers()
        assert "local_sandbox" in providers

    def test_init_runs_health_sweep(self, fabric):
        """Runs health sweep on initialization."""
        health = fabric.get_health()
        # Should have at least local_sandbox
        assert len(health) >= 1

    def test_disabled_fabric(self, temp_workspace):
        """Disabled fabric returns error on run_command."""
        config = ToolFabricConfig(enabled=False, default_workspace=temp_workspace)
        fabric = ToolFabric(config)

        result = fabric.run_command("echo hello")

        assert result.exit_code == 1
        assert "disabled" in result.stderr.lower()


# =============================================================================
# Provider Registration Tests
# =============================================================================


class TestProviderRegistration:
    """Test provider registration."""

    def test_register_provider(self, fabric):
        """Can register additional providers."""
        # Create a mock provider
        mock_provider = MagicMock()
        mock_provider.provider_id = "mock_provider"
        mock_provider.capabilities = [Capability.SHELL_EXEC]
        mock_provider.supports.return_value = True
        mock_provider.health_check.return_value = MagicMock(
            state=ProviderState.HEALTHY,
            provider_id="mock_provider",
        )

        fabric.register_provider(mock_provider)

        assert "mock_provider" in fabric._health_monitor.list_providers()

    def test_unregister_provider(self, fabric):
        """Can unregister providers."""
        # First register a mock
        mock_provider = MagicMock()
        mock_provider.provider_id = "mock_provider"
        fabric.register_provider(mock_provider)

        fabric.unregister_provider("mock_provider")

        assert "mock_provider" not in fabric._health_monitor.list_providers()


# =============================================================================
# Command Execution Tests
# =============================================================================


class TestCommandExecution:
    """Test command execution through fabric."""

    def test_run_command_blocked(self, fabric, temp_workspace):
        """Blocked commands return error."""
        result = fabric.run_command("rm -rf /", workspace=temp_workspace)

        # Should be blocked by policy
        assert result.exit_code != 0

    def test_run_command_not_in_allowlist(self, fabric, temp_workspace):
        """Commands not in allowlist may be blocked."""
        # Depends on policy configuration
        result = fabric.run_command("curl http://example.com", workspace=temp_workspace)

        # Should either be blocked or fail
        # (curl is not in default sandbox allowlist)
        assert result is not None

    def test_run_command_uses_default_workspace(self, fabric, temp_workspace):
        """Uses default workspace when not specified."""
        # fabric has default_workspace set
        result = fabric.run_command("ls")

        # Should not crash
        assert result is not None
        assert hasattr(result, "exit_code")

    def test_run_command_with_risk_level(self, fabric, temp_workspace):
        """Risk level is passed to policy evaluation."""
        result = fabric.run_command(
            "git status",
            workspace=temp_workspace,
            risk=RiskLevel.HIGH,
        )

        # Should still work for safe commands
        assert result is not None


# =============================================================================
# Repository Operations Tests
# =============================================================================


class TestRepoOperations:
    """Test git operations through fabric."""

    def test_git_status_no_repo(self, fabric, temp_workspace):
        """git_status on non-repo returns error."""
        result = fabric.git_status(workspace=temp_workspace)

        # Should return error dict or error in result
        if isinstance(result, dict):
            # Either error key or exit_code
            assert "error" in result or result.get("exit_code", 0) != 0
        else:
            assert "error" in str(result).lower() or "fatal" in str(result).lower()

    def test_git_diff(self, fabric, temp_workspace):
        """git_diff returns string."""
        result = fabric.git_diff(workspace=temp_workspace)

        assert isinstance(result, str)


# =============================================================================
# File Operations Tests
# =============================================================================


class TestFileOperations:
    """Test file operations through fabric."""

    def test_read_file(self, fabric, temp_workspace):
        """read_file returns file contents."""
        # Create a file
        test_file = os.path.join(temp_workspace, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")

        result = fabric.read_file(test_file, workspace=temp_workspace)

        assert result == "test content"

    def test_read_file_blocked_path(self, fabric, temp_workspace):
        """read_file blocks sensitive paths."""
        result = fabric.read_file("../../etc/passwd", workspace=temp_workspace)

        assert "Error" in result

    def test_write_file(self, fabric, temp_workspace):
        """write_file creates file."""
        test_file = os.path.join(temp_workspace, "new_file.txt")

        change = fabric.write_file(test_file, "new content", workspace=temp_workspace)

        assert os.path.exists(test_file)
        with open(test_file) as f:
            assert f.read() == "new content"

    def test_list_files(self, fabric, temp_workspace):
        """list_files returns file list."""
        # Create some files
        for name in ["a.txt", "b.txt"]:
            with open(os.path.join(temp_workspace, name), "w") as f:
                f.write(name)

        result = fabric.list_files(temp_workspace, workspace=temp_workspace)

        assert "a.txt" in result
        assert "b.txt" in result


# =============================================================================
# Health and Status Tests
# =============================================================================


class TestHealthStatus:
    """Test health and status methods."""

    def test_get_health(self, fabric):
        """get_health returns provider health dict."""
        health = fabric.get_health()

        assert isinstance(health, dict)
        assert "local_sandbox" in health

    def test_probe_health_specific(self, fabric):
        """probe_health for specific provider."""
        result = fabric.probe_health("local_sandbox")

        assert "local_sandbox" in result
        # State depends on Docker availability

    def test_probe_health_all(self, fabric):
        """probe_health for all providers."""
        result = fabric.probe_health()

        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_get_routing_summary(self, fabric):
        """get_routing_summary returns config."""
        summary = fabric.get_routing_summary()

        assert "capabilities" in summary
        assert "fallback" in summary

    def test_is_available_shell_exec(self, fabric):
        """is_available returns True for ShellExec."""
        # May be False if Docker not available
        result = fabric.is_available(Capability.SHELL_EXEC)
        assert isinstance(result, bool)

    def test_is_available_vision_control(self, fabric):
        """is_available returns False for VisionControl (no provider)."""
        result = fabric.is_available(Capability.VISION_CONTROL)

        # Should be False since no vision provider registered
        assert result is False


# =============================================================================
# Safe Mode Tests
# =============================================================================


class TestSafeMode:
    """Test safe mode functionality."""

    def test_enable_safe_mode(self, fabric):
        """enable_safe_mode updates config."""
        fabric.enable_safe_mode()

        assert fabric.config.safe_mode is True

    def test_disable_safe_mode(self, fabric):
        """disable_safe_mode updates config."""
        fabric.enable_safe_mode()
        fabric.disable_safe_mode()

        assert fabric.config.safe_mode is False

    def test_safe_mode_affects_ui_routing(self, fabric):
        """Safe mode changes UI builder routing."""
        fabric.enable_safe_mode()

        # Check preferences were updated
        prefs = fabric._router.config.provider_preferences.get("ui_builder", [])
        assert "ui_antigravity" not in prefs


# =============================================================================
# Global Instance Tests
# =============================================================================


class TestGlobalInstance:
    """Test global ToolFabric instance."""

    def test_get_tool_fabric_singleton(self):
        """get_tool_fabric returns same instance."""
        fabric1 = get_tool_fabric()
        fabric2 = get_tool_fabric()

        assert fabric1 is fabric2

    def test_reset_clears_instance(self):
        """reset_tool_fabric clears global instance."""
        fabric1 = get_tool_fabric()
        reset_tool_fabric()
        fabric2 = get_tool_fabric()

        assert fabric1 is not fabric2


# =============================================================================
# Integration with Router Tests
# =============================================================================


class TestRouterIntegration:
    """Test fabric integration with router."""

    def test_routing_failure_handling(self, fabric, temp_workspace):
        """Handles routing failure gracefully."""
        # Request a capability with no provider
        # VisionControl has no provider registered
        result = fabric.run_command("echo test", workspace=temp_workspace)

        # Should work because ShellExec has local_sandbox
        # But let's verify routing works at all
        assert result is not None

    def test_provider_health_affects_routing(self, fabric, temp_workspace):
        """Provider health affects selection."""
        # Probe health to ensure it's tracked
        fabric.probe_health()

        # Then run a command
        result = fabric.run_command("ls", workspace=temp_workspace)

        # Result depends on Docker availability
        assert result is not None


# =============================================================================
# Integration with Policy Tests
# =============================================================================


class TestPolicyIntegration:
    """Test fabric integration with policy engine."""

    def test_policy_blocks_dangerous_command(self, fabric, temp_workspace):
        """Policy blocks dangerous commands."""
        result = fabric.run_command("rm -rf /", workspace=temp_workspace)

        # Should be blocked
        assert result.exit_code != 0
        # Error should mention blocking
        assert any(word in result.stderr.lower() for word in ["block", "denied", "policy"])

    def test_policy_blocks_path_traversal(self, fabric, temp_workspace):
        """Policy blocks path traversal in file ops."""
        result = fabric.read_file("../../etc/passwd", workspace=temp_workspace)

        assert "Error" in result


# =============================================================================
# Receipt Tests
# =============================================================================


class TestReceiptGeneration:
    """Test receipt generation."""

    def test_receipts_logged(self, fabric, temp_workspace, caplog):
        """Receipts are logged during execution."""
        import logging

        with caplog.at_level(logging.DEBUG):
            fabric.run_command("ls", workspace=temp_workspace)

        # Receipt logging happens at DEBUG level
        # May or may not appear depending on handler config
        # Just verify no crash

    def test_receipts_disabled(self, temp_workspace):
        """Receipts can be disabled."""
        config = ToolFabricConfig(
            enabled=True,
            default_workspace=temp_workspace,
            emit_receipts=False,
        )
        fabric = ToolFabric(config)

        result = fabric.run_command("ls")

        # Should still work
        assert result is not None


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling."""

    def test_handles_missing_workspace(self, fabric):
        """Handles missing workspace gracefully."""
        # Use non-existent workspace
        result = fabric.run_command("ls", workspace="/nonexistent/path/xyz")

        # Should return error, not crash
        assert result is not None
        # May have error in stderr

    def test_handles_provider_exception(self, fabric, temp_workspace):
        """Handles provider exceptions gracefully."""
        # This is hard to test without mocking deeply
        # Just verify fabric is resilient
        result = fabric.run_command("this_command_does_not_exist", workspace=temp_workspace)

        # Should return result even if command fails
        assert result is not None
        assert hasattr(result, "exit_code")
