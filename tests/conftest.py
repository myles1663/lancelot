"""
Lancelot Test Configuration
Provides shared fixtures and automatic marker handling for the test suite.
"""
import os
import sys
import json
import shutil
import tempfile
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Auto-skip integration tests when env vars are missing
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(config, items):
    """Skip integration-marked tests if required env vars are absent."""
    skip_integration = pytest.mark.skip(reason="Integration env vars not set")
    skip_docker = pytest.mark.skip(reason="Docker runtime not available")

    skip_local_model = pytest.mark.skip(
        reason="Local model weights or llama-cpp-python not available"
    )

    for item in items:
        if "integration" in item.keywords:
            if not os.environ.get("GEMINI_API_KEY"):
                item.add_marker(skip_integration)
        if "docker" in item.keywords:
            if shutil.which("docker") is None:
                item.add_marker(skip_docker)
        if "local_model" in item.keywords:
            try:
                import llama_cpp  # noqa: F401
                from local_models.fetch_model import is_model_present
                if not is_model_present():
                    item.add_marker(skip_local_model)
            except ImportError:
                item.add_marker(skip_local_model)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide an isolated temporary data directory for a test."""
    data_dir = tmp_path / "lancelot_data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def env_override(monkeypatch):
    """Factory fixture to set env vars scoped to a single test.

    Usage:
        def test_something(env_override):
            env_override(GEMINI_API_KEY="test-key-123")
    """
    def _set(**kwargs):
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)
    return _set


@pytest.fixture
def snapshot_file(tmp_data_dir):
    """Provide a path to a clean onboarding snapshot JSON file."""
    path = tmp_data_dir / "onboarding_snapshot.json"
    return path
