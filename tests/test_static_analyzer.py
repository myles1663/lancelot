"""
Tests for Prompts 50-51: StaticAnalyzer Core + Extended Patterns.
"""

import os
import tempfile
import pytest
from pathlib import Path
from src.skills.security.static_analyzer import (
    StaticAnalyzer,
    Severity,
    StaticAnalysisResult,
)


@pytest.fixture
def analyzer():
    return StaticAnalyzer()


# ── Prompt 50: Core Patterns ────────────────────────────────────

class TestCleanCode:
    def test_clean_code_passes(self, analyzer):
        result = analyzer.analyze_source("x = 1 + 2\nprint(x)\n", skill_id="test")
        assert result.passed is True
        assert result.critical_count == 0


class TestCriticalPatterns:
    def test_import_requests(self, analyzer):
        result = analyzer.analyze_source("import requests\n")
        assert result.passed is False
        assert result.critical_count >= 1
        assert any(f.pattern_name == "direct_network_import" for f in result.findings)

    def test_from_urllib_import(self, analyzer):
        result = analyzer.analyze_source("from urllib import request\n")
        assert result.passed is False

    def test_subprocess_run(self, analyzer):
        result = analyzer.analyze_source("subprocess.run(['ls'])\n")
        assert result.passed is False
        assert any(f.pattern_name == "subprocess_exec" for f in result.findings)

    def test_os_system(self, analyzer):
        result = analyzer.analyze_source("os.system('ls')\n")
        assert result.passed is False
        assert any(f.pattern_name == "os_exec" for f in result.findings)

    def test_eval(self, analyzer):
        result = analyzer.analyze_source("eval('1+1')\n")
        assert result.passed is False
        assert any(f.pattern_name == "dynamic_exec" for f in result.findings)

    def test_dynamic_import(self, analyzer):
        result = analyzer.analyze_source("__import__('os')\n")
        assert result.passed is False
        assert any(f.pattern_name == "dynamic_import" for f in result.findings)


class TestWarningPatterns:
    def test_open_file(self, analyzer):
        result = analyzer.analyze_source("f = open('file.txt')\n")
        assert result.passed is True  # Only WARNING
        assert result.warning_count >= 1

    def test_env_access(self, analyzer):
        result = analyzer.analyze_source("key = os.environ['KEY']\n")
        assert result.passed is True
        assert result.warning_count >= 1


class TestPassedLogic:
    def test_only_warnings_passes(self, analyzer):
        result = analyzer.analyze_source("f = open('x')\nos.environ['Y']\n")
        assert result.passed is True

    def test_critical_fails(self, analyzer):
        result = analyzer.analyze_source("eval('bad')\n")
        assert result.passed is False


class TestMultipleFindings:
    def test_all_reported(self, analyzer):
        source = "import requests\neval('x')\nopen('f')\n"
        result = analyzer.analyze_source(source)
        assert len(result.findings) >= 3

    def test_counts_correct(self, analyzer):
        source = "import requests\neval('x')\nopen('f')\nimport base64\n"
        result = analyzer.analyze_source(source)
        assert result.critical_count >= 2
        assert result.warning_count >= 1
        assert result.info_count >= 1


class TestDirectoryAnalysis:
    def test_scans_multiple_files(self, analyzer):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "clean.py").write_text("x = 1\n")
            (p / "dirty.py").write_text("import requests\n")
            result = analyzer.analyze(p, skill_id="multi")
            assert result.total_files_scanned == 2
            assert result.passed is False


# ── Prompt 51: Extended Patterns ─────────────────────────────────

class TestExtendedCritical:
    def test_socket_connect(self, analyzer):
        result = analyzer.analyze_source("socket.connect(('host', 80))\n")
        assert result.passed is False
        assert any(f.pattern_name == "socket_access" for f in result.findings)

    def test_import_ctypes(self, analyzer):
        result = analyzer.analyze_source("import ctypes\n")
        assert result.passed is False
        assert any(f.pattern_name == "ctypes_usage" for f in result.findings)

    def test_signal_handler(self, analyzer):
        result = analyzer.analyze_source("signal.signal(signal.SIGINT, handler)\n")
        assert result.passed is False
        assert any(f.pattern_name == "signal_handler" for f in result.findings)


class TestExtendedWarning:
    def test_pickle_loads(self, analyzer):
        result = analyzer.analyze_source("pickle.loads(data)\n")
        assert result.warning_count >= 1
        assert any(f.pattern_name == "pickle_usage" for f in result.findings)

    def test_import_threading(self, analyzer):
        result = analyzer.analyze_source("import threading\n")
        assert result.warning_count >= 1
        assert any(f.pattern_name == "threading_usage" for f in result.findings)

    def test_global_state(self, analyzer):
        result = analyzer.analyze_source("global x\n")
        assert result.warning_count >= 1
        assert any(f.pattern_name == "global_state" for f in result.findings)


class TestExtendedInfo:
    def test_base64(self, analyzer):
        result = analyzer.analyze_source("import base64\n")
        assert result.info_count >= 1
        assert any(f.pattern_name == "base64_encoding" for f in result.findings)


class TestCustomPattern:
    def test_custom_detected(self, analyzer):
        analyzer.add_custom_pattern(
            Severity.CRITICAL, "hardcoded_password",
            r"password\s*=\s*['\"]", "Hardcoded password detected"
        )
        result = analyzer.analyze_source("password = 'secret123'\n")
        assert result.passed is False
        assert any(f.pattern_name == "hardcoded_password" for f in result.findings)


class TestMixedSeverityCounts:
    def test_full_scan_correct_counts(self, analyzer):
        source = "\n".join([
            "import requests",      # CRITICAL
            "socket.connect(x)",    # CRITICAL
            "open('f')",            # WARNING
            "global state_var",     # WARNING
            "import base64",        # INFO
        ])
        result = analyzer.analyze_source(source)
        assert result.critical_count == 2
        assert result.warning_count == 2
        assert result.info_count == 1
        assert result.passed is False
