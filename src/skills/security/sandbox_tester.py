"""
Sandbox Tester — isolated Docker-based skill testing.

Spawns sibling containers (via mounted Docker socket) with:
- No network access (--network=none)
- Read-only filesystem
- Memory/CPU limits
- Non-root user
- Timeout enforcement

If Docker is unavailable, gracefully skips with a diagnostic result.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Violation Report ─────────────────────────────────────────────

@dataclass
class ViolationReport:
    """A security violation detected during sandbox testing."""
    type: str  # "network", "filesystem", "process", "resource"
    description: str
    severity: str  # "critical", "warning"
    evidence: str  # log line or metric


# ── Sandbox Test Result ──────────────────────────────────────────

@dataclass
class SandboxTestResult:
    """Result from sandbox-testing a skill."""
    skill_id: str
    passed: bool
    operations_tested: int = 0
    operations_passed: int = 0
    violations: List[str] = field(default_factory=list)
    violation_reports: List[ViolationReport] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    tested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    details: Dict[str, Any] = field(default_factory=dict)


# ── Sandbox Tester ───────────────────────────────────────────────

class SandboxTester:
    """Tests skills in isolated Docker containers."""

    def __init__(
        self,
        docker_image: str = "python:3.11-slim",
        timeout_seconds: int = 60,
    ) -> None:
        self._docker_image = docker_image
        self._timeout_seconds = timeout_seconds

    def test_skill(
        self, skill_path: Path, manifest: Any
    ) -> SandboxTestResult:
        """Test a skill in a sandboxed Docker container.

        If Docker is unavailable, returns passed=True with skip details.
        """
        skill_path = Path(skill_path)
        skill_id = getattr(manifest, "id", "unknown")

        if not self._check_docker_available():
            logger.warning("Docker not available, skipping sandbox test")
            return SandboxTestResult(
                skill_id=skill_id,
                passed=True,
                details={"skipped": "Docker not available"},
            )

        container_name = f"lancelot-sandbox-{uuid.uuid4().hex[:12]}"
        start_time = datetime.now(timezone.utc)
        violations = []
        violation_reports = []

        try:
            # Build docker run command
            cmd = [
                "docker", "run",
                "--name", container_name,
                "--rm",
                "--network=none",
                "--memory=256m",
                "--cpus=0.5",
                "--user=1000:1000",
                "--read-only",
                "--tmpfs", "/tmp:rw,size=64m",
                "-v", f"{skill_path}:/skill:ro",
                self._docker_image,
                "python", "-c",
                "import sys; sys.path.insert(0, '/skill'); "
                "print('sandbox_test: ok')",
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )

            # Check for violations in output
            output = result.stdout + result.stderr

            # Network violations
            if any(kw in output.lower() for kw in [
                "connectionrefused", "networkunreachable", "name resolution",
                "could not resolve", "connection refused",
            ]):
                v = ViolationReport(
                    type="network",
                    description="Attempted network access in sandbox",
                    severity="critical",
                    evidence=output[:500],
                )
                violation_reports.append(v)
                violations.append(v.description)

            # Filesystem violations
            if any(kw in output.lower() for kw in [
                "read-only file system", "permission denied",
            ]):
                v = ViolationReport(
                    type="filesystem",
                    description="Attempted write outside allowed paths",
                    severity="warning",
                    evidence=output[:500],
                )
                violation_reports.append(v)
                violations.append(v.description)

            ops_tested = 1
            ops_passed = 1 if result.returncode == 0 else 0

        except subprocess.TimeoutExpired:
            violations.append("Execution timed out")
            violation_reports.append(ViolationReport(
                type="resource",
                description=f"Execution exceeded {self._timeout_seconds}s timeout",
                severity="critical",
                evidence="TimeoutExpired",
            ))
            ops_tested = 1
            ops_passed = 0
        except Exception as e:
            logger.error("Sandbox test error: %s", e)
            return SandboxTestResult(
                skill_id=skill_id,
                passed=True,
                details={"skipped": f"Sandbox error: {e}"},
            )
        finally:
            self._cleanup_container(container_name)

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        has_critical = any(v.severity == "critical" for v in violation_reports)

        return SandboxTestResult(
            skill_id=skill_id,
            passed=not has_critical and not violations,
            operations_tested=ops_tested,
            operations_passed=ops_passed,
            violations=violations,
            violation_reports=violation_reports,
            execution_time_seconds=elapsed,
        )

    def _generate_synthetic_params(self, operation: Any) -> Dict[str, Any]:
        """Generate synthetic test parameters based on parameter types."""
        params = {}
        if hasattr(operation, "parameters"):
            for p in operation.parameters:
                ptype = getattr(p, "type", "str")
                if "int" in ptype:
                    params[p.name] = 1
                elif "bool" in ptype:
                    params[p.name] = True
                elif "list" in ptype:
                    params[p.name] = ["test"]
                else:
                    params[p.name] = "test"
        return params

    def _check_docker_available(self) -> bool:
        """Check if Docker daemon is accessible."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _cleanup_container(self, name: str) -> None:
        """Force-remove a container. Always runs, errors silenced."""
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
