"""
Static Analyzer — scans skill source code for dangerous patterns.

Checks for direct network imports, subprocess execution, dynamic code
execution, and other patterns that skills should not use directly
(they must go through ConnectorProxy or ToolFabric).
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class AnalysisFinding:
    """A single finding from static analysis."""
    severity: Severity
    pattern_name: str
    message: str
    file: str
    line_number: int
    line_content: str


@dataclass
class StaticAnalysisResult:
    """Result of scanning a skill's source code."""
    skill_id: str
    total_files_scanned: int
    findings: List[AnalysisFinding] = field(default_factory=list)
    passed: bool = True  # True if no CRITICAL findings
    scanned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.INFO)


# ── Pattern type alias ───────────────────────────────────────────

PatternEntry = Tuple[Severity, str, str, str]  # (severity, name, regex, message)


class StaticAnalyzer:
    """Scans skill source code for dangerous patterns."""

    # Default patterns — class-level list, copied to instances
    _DEFAULT_PATTERNS: List[PatternEntry] = [
        # CRITICAL: Direct network access
        (Severity.CRITICAL, "direct_network_import",
         r"^\s*(import\s+(requests|urllib|httpx|aiohttp|socket)|from\s+(requests|urllib|httpx|aiohttp|socket)\s+import)",
         "Direct network library — must use ConnectorProxy"),
        # CRITICAL: Subprocess execution
        (Severity.CRITICAL, "subprocess_exec",
         r"subprocess\.(run|Popen|call|check_output|check_call)",
         "Subprocess execution — must use ToolFabric"),
        # CRITICAL: OS command execution
        (Severity.CRITICAL, "os_exec",
         r"os\.(system|popen|exec[lv]?[pe]?)\s*\(",
         "OS command execution"),
        # CRITICAL: Dynamic code execution
        (Severity.CRITICAL, "dynamic_exec",
         r"\b(eval|exec|compile)\s*\(",
         "Dynamic code execution"),
        # CRITICAL: Dynamic import
        (Severity.CRITICAL, "dynamic_import",
         r"__import__\s*\(",
         "Dynamic import"),
        # CRITICAL: Socket access
        (Severity.CRITICAL, "socket_access",
         r"socket\.(socket|connect|bind|listen)",
         "Direct socket access"),
        # CRITICAL: ctypes usage
        (Severity.CRITICAL, "ctypes_usage",
         r"(import\s+ctypes|from\s+ctypes\s+import)",
         "ctypes usage — low-level C access"),
        # CRITICAL: Signal handlers
        (Severity.CRITICAL, "signal_handler",
         r"signal\.(signal|alarm)",
         "Signal handler modification"),
        # WARNING: Raw file I/O
        (Severity.WARNING, "raw_file_io",
         r"\bopen\s*\(",
         "Raw file I/O — use ToolFabric"),
        # WARNING: Environment variable access
        (Severity.WARNING, "env_access",
         r"os\.environ|os\.getenv|environ\.get",
         "Env var access — credentials from Vault"),
        # WARNING: Pickle usage
        (Severity.WARNING, "pickle_usage",
         r"pickle\.(loads?|dumps?)",
         "Pickle usage — deserialization risk"),
        # WARNING: Threading usage
        (Severity.WARNING, "threading_usage",
         r"(import\s+threading|threading\.(Thread|Lock))",
         "Threading usage — may cause resource issues"),
        # WARNING: Global state
        (Severity.WARNING, "global_state",
         r"^\s*global\s+",
         "Global state modification"),
        # INFO: Base64 encoding
        (Severity.INFO, "base64_encoding",
         r"(import\s+base64|base64\.(b64encode|b64decode))",
         "Base64 encoding/decoding"),
    ]

    def __init__(self) -> None:
        # Copy default patterns so custom patterns don't affect other instances
        self._patterns: List[PatternEntry] = list(self._DEFAULT_PATTERNS)
        self._compiled = [(sev, name, re.compile(pat), msg) for sev, name, pat, msg in self._patterns]

    def add_custom_pattern(
        self, severity: Severity, name: str, pattern: str, message: str
    ) -> None:
        """Add a custom pattern to scan for."""
        self._patterns.append((severity, name, pattern, message))
        self._compiled.append((severity, name, re.compile(pattern), message))

    def analyze(self, skill_path: Path, skill_id: str = "") -> StaticAnalysisResult:
        """Analyze all .py files in a directory."""
        skill_path = Path(skill_path)
        findings: List[AnalysisFinding] = []
        files_scanned = 0

        if skill_path.is_file():
            source = skill_path.read_text(encoding="utf-8", errors="ignore")
            result = self.analyze_source(source, str(skill_path), skill_id)
            return result

        for py_file in skill_path.rglob("*.py"):
            files_scanned += 1
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("Could not read %s: %s", py_file, e)
                continue

            rel_path = str(py_file.relative_to(skill_path))
            for line_num, line in enumerate(source.splitlines(), start=1):
                for severity, name, compiled_re, msg in self._compiled:
                    if compiled_re.search(line):
                        findings.append(AnalysisFinding(
                            severity=severity,
                            pattern_name=name,
                            message=msg,
                            file=rel_path,
                            line_number=line_num,
                            line_content=line.strip(),
                        ))

        has_critical = any(f.severity == Severity.CRITICAL for f in findings)
        return StaticAnalysisResult(
            skill_id=skill_id,
            total_files_scanned=files_scanned,
            findings=findings,
            passed=not has_critical,
        )

    def analyze_source(
        self, source: str, filename: str = "<string>", skill_id: str = ""
    ) -> StaticAnalysisResult:
        """Analyze a single source string."""
        findings: List[AnalysisFinding] = []

        for line_num, line in enumerate(source.splitlines(), start=1):
            for severity, name, compiled_re, msg in self._compiled:
                if compiled_re.search(line):
                    findings.append(AnalysisFinding(
                        severity=severity,
                        pattern_name=name,
                        message=msg,
                        file=filename,
                        line_number=line_num,
                        line_content=line.strip(),
                    ))

        has_critical = any(f.severity == Severity.CRITICAL for f in findings)
        return StaticAnalysisResult(
            skill_id=skill_id,
            total_files_scanned=1,
            findings=findings,
            passed=not has_critical,
        )
