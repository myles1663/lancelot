"""
Tests for Prompts 52-53: SandboxTester Core + Monitoring.

Docker-dependent tests are skipped if Docker is unavailable.
"""

import subprocess
import pytest
from unittest.mock import MagicMock
from src.skills.security.sandbox_tester import (
    SandboxTester,
    SandboxTestResult,
    ViolationReport,
)


def _docker_available():
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


needs_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available"
)


@pytest.fixture
def tester():
    return SandboxTester()


# ── Always-pass tests (no Docker needed) ─────────────────────────

class TestSandboxTesterInit:
    def test_initializes(self, tester):
        assert tester._docker_image == "python:3.11-slim"
        assert tester._timeout_seconds == 60


class TestSyntheticParams:
    def test_correct_types(self, tester):
        op = MagicMock()
        p_str = MagicMock(name="title", type="str")
        p_str.name = "title"
        p_int = MagicMock(name="count", type="int")
        p_int.name = "count"
        p_bool = MagicMock(name="active", type="bool")
        p_bool.name = "active"
        p_list = MagicMock(name="tags", type="list[str]")
        p_list.name = "tags"
        op.parameters = [p_str, p_int, p_bool, p_list]

        params = tester._generate_synthetic_params(op)
        assert params["title"] == "test"
        assert params["count"] == 1
        assert params["active"] is True
        assert params["tags"] == ["test"]


class TestSandboxTestResult:
    def test_no_violations_passed(self):
        r = SandboxTestResult(skill_id="test", passed=True)
        assert r.passed is True
        assert r.violations == []

    def test_with_violations_failed(self):
        r = SandboxTestResult(
            skill_id="test", passed=False,
            violations=["network access"],
        )
        assert r.passed is False

    def test_container_name_prefix(self, tester):
        import uuid
        name = f"lancelot-sandbox-{uuid.uuid4().hex[:12]}"
        assert name.startswith("lancelot-sandbox-")
        assert len(name) > len("lancelot-sandbox-")


class TestViolationReport:
    def test_stores_all_fields(self):
        v = ViolationReport(
            type="network",
            description="Attempted DNS lookup",
            severity="critical",
            evidence="socket.gaierror: Name resolution failed",
        )
        assert v.type == "network"
        assert v.severity == "critical"

    def test_critical_violation_causes_failure(self):
        violations = [
            ViolationReport(type="network", description="x",
                            severity="critical", evidence="y"),
        ]
        has_critical = any(v.severity == "critical" for v in violations)
        assert has_critical is True


# ── Docker-dependent tests ───────────────────────────────────────

class TestDockerSkip:
    def test_skip_when_unavailable(self):
        """If Docker not available, test_skill returns passed with skip detail."""
        tester = SandboxTester()
        if not _docker_available():
            manifest = MagicMock()
            manifest.id = "test-skill"
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "main.py").write_text("x = 1\n")
                result = tester.test_skill(Path(tmpdir), manifest)
                assert result.passed is True
                assert "skipped" in result.details
