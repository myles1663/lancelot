"""
Tests for UIBuilder Templates Provider
======================================

Tests for template-based UI scaffolding:
- Template listing
- Project scaffolding
- Build verification
- File content generation
- Error handling

Prompt 7 — UIBuilder Templates
"""

import json
import os
import pytest
import shutil
import tempfile

from src.tools.providers.ui_templates import (
    TemplateScaffolder,
    TemplateConfig,
    create_template_scaffolder,
    TEMPLATES,
    _get_template_content,
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
    workspace = tempfile.mkdtemp(prefix="ui_templates_test_")
    yield workspace
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def scaffolder():
    """Create a TemplateScaffolder instance."""
    return TemplateScaffolder()


@pytest.fixture
def scaffolder_no_verify():
    """Create a TemplateScaffolder without build verification."""
    config = TemplateConfig(verify_builds=False)
    return TemplateScaffolder(config=config)


@pytest.fixture
def sample_spec():
    """Sample project specification."""
    return {
        "name": "test-project",
        "title": "Test Project",
        "description": "A test project for scaffolding",
        "author": "Test Author",
    }


# =============================================================================
# Provider Identity Tests
# =============================================================================


class TestProviderIdentity:
    """Test provider identification."""

    def test_provider_id(self, scaffolder):
        """Provider has correct ID."""
        assert scaffolder.provider_id == "ui_templates"

    def test_capabilities(self, scaffolder):
        """Provider declares UIBuilder capability."""
        caps = scaffolder.capabilities
        assert Capability.UI_BUILDER in caps
        assert len(caps) == 1

    def test_supports_ui_builder(self, scaffolder):
        """supports() returns True for UIBuilder."""
        assert scaffolder.supports(Capability.UI_BUILDER) is True
        assert scaffolder.supports(Capability.SHELL_EXEC) is False


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Test health check functionality."""

    def test_health_always_healthy(self, scaffolder):
        """Templates provider is always healthy."""
        health = scaffolder.health_check()

        assert health.provider_id == "ui_templates"
        assert health.state == ProviderState.HEALTHY
        assert health.is_healthy is True
        assert health.is_available is True

    def test_health_metadata(self, scaffolder):
        """Health includes template metadata."""
        health = scaffolder.health_check()

        assert "templates_available" in health.metadata
        assert health.metadata["templates_available"] == len(TEMPLATES)
        assert health.metadata["mode"] == "deterministic"


# =============================================================================
# Template Listing Tests
# =============================================================================


class TestListTemplates:
    """Test template listing functionality."""

    def test_list_templates_returns_all(self, scaffolder):
        """list_templates returns all templates."""
        templates = scaffolder.list_templates()

        assert len(templates) == len(TEMPLATES)

    def test_list_templates_structure(self, scaffolder):
        """Templates have required metadata."""
        templates = scaffolder.list_templates()

        for template in templates:
            assert "id" in template
            assert "name" in template
            assert "description" in template
            assert "framework" in template

    def test_list_templates_includes_nextjs(self, scaffolder):
        """Templates include nextjs_shadcn_dashboard."""
        templates = scaffolder.list_templates()
        ids = [t["id"] for t in templates]

        assert "nextjs_shadcn_dashboard" in ids

    def test_list_templates_includes_fastapi(self, scaffolder):
        """Templates include fastapi_service."""
        templates = scaffolder.list_templates()
        ids = [t["id"] for t in templates]

        assert "fastapi_service" in ids

    def test_list_templates_includes_streamlit(self, scaffolder):
        """Templates include streamlit_dashboard."""
        templates = scaffolder.list_templates()
        ids = [t["id"] for t in templates]

        assert "streamlit_dashboard" in ids

    def test_list_templates_includes_flask(self, scaffolder):
        """Templates include flask_api."""
        templates = scaffolder.list_templates()
        ids = [t["id"] for t in templates]

        assert "flask_api" in ids


# =============================================================================
# Scaffold Next.js Tests
# =============================================================================


class TestScaffoldNextjs:
    """Test Next.js template scaffolding."""

    def test_scaffold_nextjs_dashboard(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold nextjs_shadcn_dashboard creates valid project."""
        result = scaffolder_no_verify.scaffold(
            template_id="nextjs_shadcn_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert isinstance(result, ScaffoldResult)
        assert result.success is True
        assert result.template_id == "nextjs_shadcn_dashboard"
        assert len(result.files_created) > 0

    def test_nextjs_creates_package_json(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Next.js scaffold creates package.json."""
        scaffolder_no_verify.scaffold(
            template_id="nextjs_shadcn_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        package_path = os.path.join(temp_workspace, "package.json")
        assert os.path.exists(package_path)

        with open(package_path) as f:
            package = json.load(f)
            assert package["name"] == "test-project"
            assert "next" in package["dependencies"]

    def test_nextjs_creates_layout(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Next.js scaffold creates app/layout.tsx."""
        scaffolder_no_verify.scaffold(
            template_id="nextjs_shadcn_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        layout_path = os.path.join(temp_workspace, "app", "layout.tsx")
        assert os.path.exists(layout_path)

        with open(layout_path) as f:
            content = f.read()
            assert "Test Project" in content

    def test_nextjs_creates_components(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Next.js scaffold creates components."""
        scaffolder_no_verify.scaffold(
            template_id="nextjs_shadcn_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        button_path = os.path.join(temp_workspace, "components", "ui", "button.tsx")
        assert os.path.exists(button_path)

        sidebar_path = os.path.join(temp_workspace, "components", "dashboard", "sidebar.tsx")
        assert os.path.exists(sidebar_path)


# =============================================================================
# Scaffold FastAPI Tests
# =============================================================================


class TestScaffoldFastAPI:
    """Test FastAPI template scaffolding."""

    def test_scaffold_fastapi_service(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold fastapi_service creates valid project."""
        result = scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is True
        assert result.template_id == "fastapi_service"

    def test_fastapi_creates_main(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """FastAPI scaffold creates main.py."""
        scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        main_path = os.path.join(temp_workspace, "main.py")
        assert os.path.exists(main_path)

        with open(main_path) as f:
            content = f.read()
            assert "FastAPI" in content
            assert "Test Project" in content

    def test_fastapi_creates_routes(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """FastAPI scaffold creates routes."""
        scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        routes_path = os.path.join(temp_workspace, "app", "api", "routes.py")
        assert os.path.exists(routes_path)

    def test_fastapi_creates_dockerfile(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """FastAPI scaffold creates Dockerfile."""
        scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        dockerfile_path = os.path.join(temp_workspace, "Dockerfile")
        assert os.path.exists(dockerfile_path)

    def test_fastapi_python_syntax_valid(self, scaffolder, temp_workspace, sample_spec):
        """FastAPI Python files have valid syntax."""
        result = scaffolder.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        # Build verification checks Python syntax
        assert result.build_verified is True


# =============================================================================
# Scaffold Streamlit Tests
# =============================================================================


class TestScaffoldStreamlit:
    """Test Streamlit template scaffolding."""

    def test_scaffold_streamlit_dashboard(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold streamlit_dashboard creates valid project."""
        result = scaffolder_no_verify.scaffold(
            template_id="streamlit_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is True
        assert result.template_id == "streamlit_dashboard"

    def test_streamlit_creates_app(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Streamlit scaffold creates app.py."""
        scaffolder_no_verify.scaffold(
            template_id="streamlit_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        app_path = os.path.join(temp_workspace, "app.py")
        assert os.path.exists(app_path)

        with open(app_path) as f:
            content = f.read()
            assert "streamlit" in content
            assert "Test Project" in content

    def test_streamlit_creates_pages(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Streamlit scaffold creates pages."""
        scaffolder_no_verify.scaffold(
            template_id="streamlit_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        overview_path = os.path.join(temp_workspace, "pages", "overview.py")
        assert os.path.exists(overview_path)

        analytics_path = os.path.join(temp_workspace, "pages", "analytics.py")
        assert os.path.exists(analytics_path)

    def test_streamlit_creates_config(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Streamlit scaffold creates .streamlit/config.toml."""
        scaffolder_no_verify.scaffold(
            template_id="streamlit_dashboard",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        config_path = os.path.join(temp_workspace, ".streamlit", "config.toml")
        assert os.path.exists(config_path)


# =============================================================================
# Scaffold Flask Tests
# =============================================================================


class TestScaffoldFlask:
    """Test Flask template scaffolding."""

    def test_scaffold_flask_api(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold flask_api creates valid project."""
        result = scaffolder_no_verify.scaffold(
            template_id="flask_api",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is True
        assert result.template_id == "flask_api"

    def test_flask_creates_app(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Flask scaffold creates app.py."""
        scaffolder_no_verify.scaffold(
            template_id="flask_api",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        app_path = os.path.join(temp_workspace, "app.py")
        assert os.path.exists(app_path)

        with open(app_path) as f:
            content = f.read()
            assert "Flask" in content

    def test_flask_creates_routes(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Flask scaffold creates routes."""
        scaffolder_no_verify.scaffold(
            template_id="flask_api",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        routes_path = os.path.join(temp_workspace, "routes", "api.py")
        assert os.path.exists(routes_path)


# =============================================================================
# Build Verification Tests
# =============================================================================


class TestBuildVerification:
    """Test build verification functionality."""

    def test_verify_build_valid_python(self, scaffolder, temp_workspace):
        """verify_build returns True for valid Python."""
        # Create valid Python file
        py_file = os.path.join(temp_workspace, "valid.py")
        with open(py_file, "w") as f:
            f.write("def hello():\n    print('hello')\n")

        result = scaffolder.verify_build(temp_workspace)

        assert result is True

    def test_verify_build_invalid_python(self, scaffolder, temp_workspace):
        """verify_build returns False for invalid Python."""
        # Create invalid Python file
        py_file = os.path.join(temp_workspace, "invalid.py")
        with open(py_file, "w") as f:
            f.write("def hello(\n    print('hello')\n")  # Missing closing paren

        result = scaffolder.verify_build(temp_workspace)

        assert result is False

    def test_verify_build_valid_package_json(self, scaffolder, temp_workspace):
        """verify_build returns True for valid package.json."""
        # Create valid package.json
        package_path = os.path.join(temp_workspace, "package.json")
        with open(package_path, "w") as f:
            json.dump({"name": "test", "version": "1.0.0"}, f)

        result = scaffolder.verify_build(temp_workspace)

        assert result is True

    def test_verify_build_invalid_package_json(self, scaffolder, temp_workspace):
        """verify_build returns False for invalid package.json."""
        # Create invalid package.json
        package_path = os.path.join(temp_workspace, "package.json")
        with open(package_path, "w") as f:
            f.write('{"name": "test",}')  # Invalid JSON

        result = scaffolder.verify_build(temp_workspace)

        assert result is False


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling in scaffolding."""

    def test_unknown_template_error(self, scaffolder, temp_workspace, sample_spec):
        """Unknown template returns error."""
        result = scaffolder.scaffold(
            template_id="nonexistent_template",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is False
        assert "Unknown template" in result.error_message

    def test_generative_mode_error(self, scaffolder, temp_workspace, sample_spec):
        """Generative mode returns error for templates provider."""
        result = scaffolder.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
            mode=UIBuilderMode.GENERATIVE,
        )

        assert result.success is False
        assert "DETERMINISTIC" in result.error_message

    def test_scaffold_with_empty_spec(self, scaffolder_no_verify, temp_workspace):
        """Scaffold works with empty spec (uses defaults)."""
        result = scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec={},
            workspace=temp_workspace,
        )

        assert result.success is True


# =============================================================================
# Template Content Tests
# =============================================================================


class TestTemplateContent:
    """Test template content generation."""

    def test_get_template_content_with_spec(self):
        """Template content includes spec values."""
        spec = {
            "name": "my-app",
            "title": "My App Title",
            "description": "My app description",
        }

        content = _get_template_content("fastapi_main", spec)

        assert "My App Title" in content
        assert "My app description" in content

    def test_get_template_content_defaults(self):
        """Template content uses defaults for missing spec."""
        content = _get_template_content("fastapi_main", {})

        assert "My Project" in content  # Default title

    def test_get_template_unknown_returns_placeholder(self):
        """Unknown template key returns placeholder."""
        content = _get_template_content("nonexistent_key", {})

        assert "Template not found" in content


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Test create_template_scaffolder factory."""

    def test_create_with_defaults(self):
        """Create scaffolder with default settings."""
        scaffolder = create_template_scaffolder()

        assert scaffolder.provider_id == "ui_templates"
        assert scaffolder.config.verify_builds is True

    def test_create_without_verification(self):
        """Create scaffolder without build verification."""
        scaffolder = create_template_scaffolder(verify_builds=False)

        assert scaffolder.config.verify_builds is False

    def test_create_with_custom_timeout(self):
        """Create scaffolder with custom timeout."""
        scaffolder = create_template_scaffolder(build_timeout_s=600)

        assert scaffolder.config.build_timeout_s == 600


# =============================================================================
# Integration Tests
# =============================================================================


class TestScaffoldIntegration:
    """Integration tests for complete scaffolding workflows."""

    def test_scaffold_and_verify_fastapi(self, scaffolder, temp_workspace, sample_spec):
        """Complete scaffold + verify workflow for FastAPI."""
        result = scaffolder.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is True
        assert result.build_verified is True
        assert "main.py" in result.files_created
        assert "requirements.txt" in result.files_created

    def test_scaffold_output_path_correct(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold result includes correct output path."""
        result = scaffolder_no_verify.scaffold(
            template_id="flask_api",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.output_path == temp_workspace

    def test_scaffold_creates_all_files(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """Scaffold creates all expected files."""
        template_id = "streamlit_dashboard"
        expected_files = list(TEMPLATES[template_id]["files"].keys())

        result = scaffolder_no_verify.scaffold(
            template_id=template_id,
            spec=sample_spec,
            workspace=temp_workspace,
        )

        assert result.success is True
        for expected_file in expected_files:
            assert expected_file in result.files_created


# =============================================================================
# ScaffoldResult Tests
# =============================================================================


class TestScaffoldResult:
    """Test ScaffoldResult structure."""

    def test_result_to_dict(self, scaffolder_no_verify, temp_workspace, sample_spec):
        """ScaffoldResult converts to dict."""
        result = scaffolder_no_verify.scaffold(
            template_id="fastapi_service",
            spec=sample_spec,
            workspace=temp_workspace,
        )

        data = result.to_dict()

        assert "success" in data
        assert "output_path" in data
        assert "template_id" in data
        assert "files_created" in data
        assert data["success"] is True

    def test_result_from_dict(self):
        """ScaffoldResult creates from dict."""
        data = {
            "success": True,
            "output_path": "/test/path",
            "template_id": "test_template",
            "files_created": ["file1.py", "file2.py"],
            "build_verified": True,
        }

        from src.tools.contracts import ScaffoldResult

        result = ScaffoldResult.from_dict(data)

        assert result.success is True
        assert result.template_id == "test_template"
        assert len(result.files_created) == 2

