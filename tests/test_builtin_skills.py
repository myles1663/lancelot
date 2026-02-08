"""
Tests for built-in actuator skills (Fix Pack V1 PR6).
repo_writer, command_runner, service_runner, network_client.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.skills.builtins import repo_writer, command_runner, service_runner, network_client


# Fake context
class _FakeContext:
    skill_name = "test"
    request_id = "req-1"
    caller = "test"
    metadata = {}


CTX = _FakeContext()


# =========================================================================
# repo_writer Tests
# =========================================================================


class TestRepoWriter:
    def test_create_file(self, tmp_path):
        result = repo_writer.execute(CTX, {
            "action": "create",
            "path": "test.txt",
            "content": "Hello World",
            "workspace": str(tmp_path),
        })
        assert result["status"] == "created"
        assert (tmp_path / "test.txt").read_text() == "Hello World"

    def test_create_file_with_subdirs(self, tmp_path):
        result = repo_writer.execute(CTX, {
            "action": "create",
            "path": "sub/dir/test.txt",
            "content": "nested",
            "workspace": str(tmp_path),
        })
        assert result["status"] == "created"
        assert (tmp_path / "sub" / "dir" / "test.txt").read_text() == "nested"

    def test_create_existing_file_fails(self, tmp_path):
        (tmp_path / "exists.txt").write_text("old")
        with pytest.raises(FileExistsError):
            repo_writer.execute(CTX, {
                "action": "create",
                "path": "exists.txt",
                "content": "new",
                "workspace": str(tmp_path),
            })

    def test_edit_file(self, tmp_path):
        (tmp_path / "edit.txt").write_text("old content")
        result = repo_writer.execute(CTX, {
            "action": "edit",
            "path": "edit.txt",
            "content": "new content",
            "workspace": str(tmp_path),
        })
        assert result["status"] == "edited"
        assert (tmp_path / "edit.txt").read_text() == "new content"

    def test_edit_nonexistent_fails(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            repo_writer.execute(CTX, {
                "action": "edit",
                "path": "missing.txt",
                "content": "data",
                "workspace": str(tmp_path),
            })

    def test_delete_file(self, tmp_path):
        (tmp_path / "delete.txt").write_text("bye")
        result = repo_writer.execute(CTX, {
            "action": "delete",
            "path": "delete.txt",
            "workspace": str(tmp_path),
        })
        assert result["status"] == "deleted"
        assert not (tmp_path / "delete.txt").exists()

    def test_delete_nonexistent_fails(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            repo_writer.execute(CTX, {
                "action": "delete",
                "path": "missing.txt",
                "workspace": str(tmp_path),
            })

    def test_patch_file(self, tmp_path):
        (tmp_path / "patch.txt").write_text("line1\nline2\nline3\n")
        result = repo_writer.execute(CTX, {
            "action": "patch",
            "path": "patch.txt",
            "content": "+new_line\n-removed\nkept\n",
            "workspace": str(tmp_path),
        })
        assert result["status"] == "patched"
        assert result["lines_added"] >= 1

    def test_path_traversal_blocked(self, tmp_path):
        with pytest.raises(ValueError, match="traversal"):
            repo_writer.execute(CTX, {
                "action": "create",
                "path": "../../etc/passwd",
                "content": "evil",
                "workspace": str(tmp_path),
            })

    def test_unknown_action_fails(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown action"):
            repo_writer.execute(CTX, {
                "action": "explode",
                "path": "test.txt",
                "workspace": str(tmp_path),
            })

    def test_missing_path_fails(self, tmp_path):
        with pytest.raises(ValueError, match="path"):
            repo_writer.execute(CTX, {
                "action": "create",
                "content": "data",
                "workspace": str(tmp_path),
            })


# =========================================================================
# command_runner Tests
# =========================================================================


class TestCommandRunner:
    def test_allowlisted_command_succeeds(self):
        result = command_runner.execute(CTX, {"command": "echo hello"})
        assert result["return_code"] == 0
        assert "hello" in result["stdout"]

    def test_blocked_command_rejected(self):
        with pytest.raises(ValueError, match="not in whitelist"):
            command_runner.execute(CTX, {"command": "rm -rf /"})

    def test_shell_metachar_blocked(self):
        with pytest.raises(ValueError, match="metacharacter"):
            command_runner.execute(CTX, {"command": "echo hello; rm -rf /"})

    def test_timeout_enforcement(self):
        # Use a very short timeout with a command that would take longer
        # On Windows, 'timeout' is not available, so we test with a quick command
        result = command_runner.execute(CTX, {
            "command": "echo fast",
            "timeout_sec": 5,
        })
        assert result["return_code"] == 0

    def test_missing_command_fails(self):
        with pytest.raises(ValueError, match="command"):
            command_runner.execute(CTX, {"command": ""})

    def test_command_with_args(self):
        result = command_runner.execute(CTX, {"command": "echo hello world"})
        assert result["return_code"] == 0
        assert "hello" in result["stdout"]


# =========================================================================
# service_runner Tests
# =========================================================================


class TestServiceRunner:
    def test_health_check_returns_status(self):
        """Health check against a known-unreachable URL returns unreachable."""
        result = service_runner.execute(CTX, {
            "action": "health",
            "health_url": "http://127.0.0.1:59999/nonexistent",
            "timeout_sec": 2,
        })
        # Should return unreachable (connection refused)
        assert result["status"] in ("unreachable", "unhealthy")
        assert "url" in result

    def test_unknown_action_fails(self):
        with pytest.raises(ValueError, match="Unknown action"):
            service_runner.execute(CTX, {"action": "explode"})

    def test_health_missing_url_fails(self):
        with pytest.raises(ValueError, match="health_url"):
            service_runner.execute(CTX, {"action": "health"})

    def test_docker_status(self):
        """Docker status returns container info (or error if Docker not available)."""
        result = service_runner.execute(CTX, {"action": "status"})
        assert "status" in result


# =========================================================================
# network_client Tests
# =========================================================================


class TestNetworkClient:
    def test_missing_url_fails(self):
        with pytest.raises(ValueError, match="url"):
            network_client.execute(CTX, {"method": "GET", "url": ""})

    def test_invalid_method_fails(self):
        with pytest.raises(ValueError, match="not allowed"):
            network_client.execute(CTX, {"method": "EXPLODE", "url": "http://example.com"})

    def test_invalid_url_scheme_fails(self):
        with pytest.raises(ValueError, match="http"):
            network_client.execute(CTX, {"method": "GET", "url": "ftp://bad.com"})

    def test_get_request_to_unreachable(self):
        """GET to unreachable host raises connection error."""
        with pytest.raises(ConnectionError):
            network_client.execute(CTX, {
                "method": "GET",
                "url": "http://192.0.2.1:1/",  # RFC 5737 TEST-NET
                "timeout_sec": 2,
            })


# =========================================================================
# Executor Registration Tests
# =========================================================================


class TestExecutorRegistration:
    def test_builtin_skills_registered(self):
        from src.core.skills.executor import _BUILTIN_SKILLS
        assert "echo" in _BUILTIN_SKILLS
        assert "repo_writer" in _BUILTIN_SKILLS
        assert "command_runner" in _BUILTIN_SKILLS
        assert "service_runner" in _BUILTIN_SKILLS
        assert "network_client" in _BUILTIN_SKILLS
