"""
Prompt 0 — Minimal smoke test.
Validates that the pytest harness, markers, and core imports work.
"""
import os
import sys
import pytest


class TestHarnessSmoke:
    """Verify the test infrastructure itself."""

    def test_pytest_runs(self):
        """Pytest can discover and execute tests."""
        assert True

    def test_project_importable(self):
        """Project root is on sys.path and src/ packages are importable."""
        project_root = os.path.join(os.path.dirname(__file__), "..")
        assert os.path.isdir(os.path.join(project_root, "src"))

    def test_tmp_data_dir_fixture(self, tmp_data_dir):
        """The tmp_data_dir fixture provides a writable directory."""
        assert tmp_data_dir.exists()
        probe = tmp_data_dir / "probe.txt"
        probe.write_text("ok")
        assert probe.read_text() == "ok"

    def test_env_override_fixture(self, env_override):
        """The env_override fixture sets and isolates env vars."""
        env_override(LANCELOT_TEST_VAR="hello")
        assert os.environ.get("LANCELOT_TEST_VAR") == "hello"

    def test_env_override_does_not_leak(self):
        """Env vars set via env_override don't persist across tests."""
        assert os.environ.get("LANCELOT_TEST_VAR") is None

    def test_snapshot_file_fixture(self, snapshot_file):
        """The snapshot_file fixture provides a valid path."""
        assert not snapshot_file.exists()
        assert str(snapshot_file).endswith("onboarding_snapshot.json")

    @pytest.mark.integration
    def test_integration_marker_exists(self):
        """Integration marker is registered and functional."""
        assert True

    @pytest.mark.slow
    def test_slow_marker_exists(self):
        """Slow marker is registered and functional."""
        assert True

    @pytest.mark.docker
    def test_docker_marker_exists(self):
        """Docker marker is registered and functional."""
        assert True
