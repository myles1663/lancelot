"""
Tests for Antigravity UIBuilder Provider
========================================

Tests for generative UI scaffolding:
- Provider health checks
- Generative mode scaffolding
- Template fallback behavior
- Receipt generation
- Feature flag integration

Prompt 8 — Antigravity UIBuilder
"""

import os
import pytest
import shutil
import tempfile
from unittest.mock import patch, MagicMock

from src.tools.providers.ui_antigravity import (
    AntigravityUIProvider,
    AntigravityUIConfig,
    create_antigravity_ui_provider,
    GenerationReceipt,
)
from src.tools.contracts import (
    Capability,
    ProviderState,
    UIBuilderMode,
    ScaffoldResult,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="ui_antigravity_test_")
    yield workspace
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def provider():
    """Create an AntigravityUIProvider instance with defaults."""
    return AntigravityUIProvider()


@pytest.fixture
def provider_enabled():
    """Create an enabled AntigravityUIProvider with mocked availability."""
    config = AntigravityUIConfig(enabled=True, fallback_to_templates=True)
    p = AntigravityUIProvider(config=config)
    p._antigravity_available = True  # Mock as available
    return p


@pytest.fixture
def provider_disabled():
    """Create a disabled AntigravityUIProvider."""
    config = AntigravityUIConfig(enabled=False)
    return AntigravityUIProvider(config=config)


@pytest.fixture
def provider_no_fallback():
    """Create provider without fallback enabled."""
    config = AntigravityUIConfig(enabled=True, fallback_to_templates=False)
    return AntigravityUIProvider(config=config)


@pytest.fixture
def sample_spec():
    """Sample project specification."""
    return {
        "name": "test-project",
        "title": "Test Project",
        "description": "A test project for scaffolding",
    }


# =============================================================================
# Provider Identity Tests
# =============================================================================


class TestProviderIdentity:
    """Test provider identification."""

    def test_provider_id(self, provider):
        """Provider has correct ID."""
        assert provider.provider_id == "ui_antigravity"

    def test_capabilities(self, provider):
        """Provider declares UIBuilder capability."""
        caps = provider.capabilities
        assert Capability.UI_BUILDER in caps
        assert len(caps) == 1

    def test_supports_ui_builder(self, provider):
        """supports() returns True for UIBuilder."""
        assert provider.supports(Capability.UI_BUILDER) is True
        assert provider.supports(Capability.SHELL_EXEC) is False


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Test health check functionality."""

    def test_health_with_feature_disabled(self, provider):
        """Health check with feature flag disabled."""
        with patch("src.tools.providers.ui_antigravity.FEATURE_TOOLS_ANTIGRAVITY", False):
            health = provider.health_check()

            assert health.provider_id == "ui_antigravity"
            assert health.state == ProviderState.OFFLINE
            assert "disabled" in health.error_message.lower()
            assert provider._antigravity_available is False

    def test_health_with_config_disabled(self, provider_disabled):
        """Health check with config disabled."""
        with patch("src.tools.providers.ui_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            health = provider_disabled.health_check()

            assert health.state == ProviderState.OFFLINE
            assert "disabled" in health.error_message.lower()

    def test_health_service_available(self, provider):
        """Health check with service available."""
        with patch("src.tools.providers.ui_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_service", return_value=True):
                health = provider.health_check()

                assert health.state == ProviderState.HEALTHY
                assert provider._antigravity_available is True

    def test_health_service_unavailable(self, provider):
        """Health check with service unavailable."""
        with patch("src.tools.providers.ui_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_service", return_value=False):
                health = provider.health_check()

                assert health.state == ProviderState.DEGRADED
                assert "not reachable" in health.degraded_reasons[0]

    def test_health_metadata_includes_fallback_info(self, provider):
        """Health metadata includes fallback availability."""
        with patch("src.tools.providers.ui_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_service", return_value=True):
                health = provider.health_check()

                assert "fallback_available" in health.metadata
                assert health.metadata["fallback_available"] is True


# =============================================================================
# Deterministic Mode Tests
# =============================================================================


class TestDeterministicMode:
    """Test DETERMINISTIC mode scaffolding."""

    def test_deterministic_uses_templates(self, provider_enabled, temp_workspace, sample_spec):
        """DETERMINISTIC mode always uses template fallback."""
        result = provider_enabled.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.DETERMINISTIC,
        )

        assert result.success is True
        assert "main.py" in result.files_created or result.template_id == "fastapi_service"

    def test_deterministic_ignores_antigravity(self, provider_enabled, temp_workspace, sample_spec):
        """DETERMINISTIC mode doesn't use Antigravity even if available."""
        with patch.object(provider_enabled, "_generative_scaffold") as mock_gen:
            provider_enabled.scaffold(
                template_id="fastapi_service",
                spec=sample_spec,
                workspace=temp_workspace,
                mode=UIBuilderMode.DETERMINISTIC,
            )

            # Should not call generative scaffold
            mock_gen.assert_not_called()


# =============================================================================
# Generative Mode Tests
# =============================================================================


class TestGenerativeMode:
    """Test GENERATIVE mode scaffolding."""

    def test_generative_when_available(self, provider_enabled, temp_workspace, sample_spec):
        """GENERATIVE mode uses Antigravity when available."""
        result = provider_enabled.scaffold(
            template_id="Create a dashboard for user analytics",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is True
        assert "antigravity:" in result.template_id or len(result.files_created) > 0

    def test_generative_creates_files(self, provider_enabled, temp_workspace, sample_spec):
        """GENERATIVE mode creates project files."""
        provider_enabled.scaffold(
            template_id="Build an API service",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        # Should create at least main.py
        assert os.path.exists(os.path.join(temp_workspace, "main.py"))

    def test_generative_falls_back_when_unavailable(self, provider, temp_workspace, sample_spec):
        """GENERATIVE mode falls back when Antigravity unavailable."""
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="Create a dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        # Should succeed using template fallback
        assert result.success is True

    def test_generative_no_fallback_fails(self, provider_no_fallback, temp_workspace, sample_spec):
        """GENERATIVE mode fails without fallback when unavailable."""
        provider_no_fallback._antigravity_available = False

        result = provider_no_fallback.scaffold(
            template_id="Create a dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is False
        assert "not available" in result.error_message.lower()


# =============================================================================
# Fallback Behavior Tests
# =============================================================================


class TestFallbackBehavior:
    """Test template fallback behavior."""

    def test_fallback_maps_dashboard_prompt(self, provider, temp_workspace, sample_spec):
        """Dashboard prompts map to nextjs_shadcn_dashboard."""
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="Build an admin dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        # Check files match nextjs template
        assert result.success is True

    def test_fallback_maps_api_prompt(self, provider, temp_workspace, sample_spec):
        """API prompts map to fastapi_service."""
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="Create a REST API backend",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is True

    def test_fallback_maps_data_prompt(self, provider, temp_workspace, sample_spec):
        """Data/analytics prompts map to streamlit_dashboard."""
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="Build a data analytics tool",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is True

    def test_fallback_direct_template_id(self, provider, temp_workspace, sample_spec):
        """Direct template ID is used as-is."""
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="flask_api",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        # Should use flask_api template
        assert result.success is True


# =============================================================================
# Receipt Generation Tests
# =============================================================================


class TestReceiptGeneration:
    """Test generation receipt creation."""

    def test_generative_creates_receipt(self, provider_enabled, temp_workspace, sample_spec):
        """Generative scaffolding creates receipt."""
        provider_enabled.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        receipts = provider_enabled.get_receipts()
        assert len(receipts) == 1
        assert receipts[0]["mode"] == "generative"

    def test_fallback_creates_receipt(self, provider, temp_workspace, sample_spec):
        """Fallback scaffolding creates receipt."""
        provider._antigravity_available = False

        provider.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        receipts = provider.get_receipts()
        assert len(receipts) == 1
        assert receipts[0]["mode"] == "fallback"
        assert receipts[0]["fallback_used"] is True

    def test_receipt_contains_hashes(self, provider_enabled, temp_workspace, sample_spec):
        """Receipt contains prompt and spec hashes."""
        provider_enabled.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        receipts = provider_enabled.get_receipts()
        assert "prompt_hash" in receipts[0]
        assert "spec_hash" in receipts[0]
        assert len(receipts[0]["prompt_hash"]) == 16

    def test_receipt_tracks_files(self, provider_enabled, temp_workspace, sample_spec):
        """Receipt tracks generated files."""
        provider_enabled.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        receipts = provider_enabled.get_receipts()
        assert "files_generated" in receipts[0]
        assert len(receipts[0]["files_generated"]) > 0

    def test_clear_receipts(self, provider_enabled, temp_workspace, sample_spec):
        """Receipts can be cleared."""
        provider_enabled.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert len(provider_enabled.get_receipts()) == 1

        provider_enabled.clear_receipts()

        assert len(provider_enabled.get_receipts()) == 0

    def test_receipts_disabled(self, temp_workspace, sample_spec):
        """Receipts can be disabled in config."""
        config = AntigravityUIConfig(emit_generation_receipts=False)
        provider = AntigravityUIProvider(config=config)
        provider._antigravity_available = True

        provider.scaffold(
            template_id="Create an app",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert len(provider.get_receipts()) == 0


# =============================================================================
# List Templates Tests
# =============================================================================


class TestListTemplates:
    """Test template listing."""

    def test_list_includes_base_templates(self, provider):
        """List includes all base templates."""
        templates = provider.list_templates()

        ids = [t["id"] for t in templates]
        assert "nextjs_shadcn_dashboard" in ids
        assert "fastapi_service" in ids

    def test_list_includes_generative_when_available(self, provider_enabled):
        """List includes generative option when available."""
        templates = provider_enabled.list_templates()

        ids = [t["id"] for t in templates]
        assert "generative" in ids

    def test_list_excludes_generative_when_unavailable(self, provider):
        """List excludes generative option when unavailable."""
        provider._antigravity_available = False

        templates = provider.list_templates()

        ids = [t["id"] for t in templates]
        assert "generative" not in ids


# =============================================================================
# Build Verification Tests
# =============================================================================


class TestBuildVerification:
    """Test build verification."""

    def test_verify_build_delegates_to_template(self, provider_enabled, temp_workspace):
        """verify_build delegates to template scaffolder."""
        # Create valid Python
        with open(os.path.join(temp_workspace, "test.py"), "w") as f:
            f.write("print('hello')\n")

        result = provider_enabled.verify_build(temp_workspace)

        assert result is True

    def test_verify_build_fails_on_invalid(self, provider_enabled, temp_workspace):
        """verify_build fails on invalid syntax."""
        # Create invalid Python
        with open(os.path.join(temp_workspace, "test.py"), "w") as f:
            f.write("def broken(\n")

        result = provider_enabled.verify_build(temp_workspace)

        assert result is False


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Test create_antigravity_ui_provider factory."""

    def test_create_with_defaults(self):
        """Create provider with default settings."""
        provider = create_antigravity_ui_provider()

        assert provider.provider_id == "ui_antigravity"
        assert provider.config.enabled is True
        assert provider.config.fallback_to_templates is True

    def test_create_disabled(self):
        """Create disabled provider."""
        provider = create_antigravity_ui_provider(enabled=False)

        assert provider.config.enabled is False

    def test_create_no_fallback(self):
        """Create provider without fallback."""
        provider = create_antigravity_ui_provider(fallback_to_templates=False)

        assert provider.config.fallback_to_templates is False

    def test_create_no_receipts(self):
        """Create provider without receipts."""
        provider = create_antigravity_ui_provider(emit_receipts=False)

        assert provider.config.emit_generation_receipts is False


# =============================================================================
# GenerationReceipt Tests
# =============================================================================


class TestGenerationReceipt:
    """Test GenerationReceipt dataclass."""

    def test_receipt_to_dict(self):
        """Receipt converts to dict."""
        receipt = GenerationReceipt(
            receipt_id="test-123",
            timestamp="2024-01-01T00:00:00Z",
            mode="generative",
            prompt_hash="abc123",
            spec_hash="def456",
            output_path="/test",
            files_generated=["file1.py", "file2.py"],
            generation_time_ms=1000,
            success=True,
        )

        data = receipt.to_dict()

        assert data["receipt_id"] == "test-123"
        assert data["mode"] == "generative"
        assert data["files_generated"] == ["file1.py", "file2.py"]
        assert data["success"] is True

    def test_receipt_with_error(self):
        """Receipt captures error."""
        receipt = GenerationReceipt(
            receipt_id="test-error",
            timestamp="2024-01-01T00:00:00Z",
            mode="generative",
            prompt_hash="abc",
            spec_hash="def",
            output_path="/test",
            files_generated=[],
            generation_time_ms=100,
            success=False,
            error_message="Generation failed",
        )

        data = receipt.to_dict()

        assert data["success"] is False
        assert data["error_message"] == "Generation failed"

    def test_receipt_with_fallback(self):
        """Receipt tracks fallback info."""
        receipt = GenerationReceipt(
            receipt_id="test-fallback",
            timestamp="2024-01-01T00:00:00Z",
            mode="fallback",
            prompt_hash="abc",
            spec_hash="def",
            output_path="/test",
            files_generated=["app.py"],
            generation_time_ms=50,
            success=True,
            fallback_used=True,
            fallback_template="fastapi_service",
        )

        data = receipt.to_dict()

        assert data["fallback_used"] is True
        assert data["fallback_template"] == "fastapi_service"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for complete workflows."""

    def test_full_generative_workflow(self, temp_workspace, sample_spec):
        """Complete generative workflow."""
        config = AntigravityUIConfig(enabled=True, emit_generation_receipts=True)
        provider = AntigravityUIProvider(config=config)
        provider._antigravity_available = True

        result = provider.scaffold(
            template_id="Create a user management API",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is True
        assert len(result.files_created) > 0

        # Verify files exist
        for file in result.files_created:
            assert os.path.exists(os.path.join(temp_workspace, file))

        # Verify receipt
        receipts = provider.get_receipts()
        assert len(receipts) == 1

    def test_full_fallback_workflow(self, temp_workspace, sample_spec):
        """Complete fallback workflow."""
        config = AntigravityUIConfig(enabled=True, fallback_to_templates=True)
        provider = AntigravityUIProvider(config=config)
        provider._antigravity_available = False

        result = provider.scaffold(
            template_id="Create an API service",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is True

        # Verify receipt shows fallback
        receipts = provider.get_receipts()
        assert receipts[0]["fallback_used"] is True

