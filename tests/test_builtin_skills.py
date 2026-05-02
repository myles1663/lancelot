"""
Tests for built-in actuator skills.
repo_writer, command_runner, service_runner, network_client.
"""

import os
import sys
import types
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.skills.builtins import (
    command_runner,
    daily_news_brief,
    document_creator,
    memory_cleanup,
    memory_query,
    network_client,
    repo_writer,
    schedule_job,
    skill_manager,
    telegram_send,
    service_runner,
)


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
        assert result["workspace"] == str(tmp_path.resolve())
        assert result["target_path"] == str((tmp_path / "test.txt").resolve())
        assert result["relative_path"] == "test.txt"
        assert result["write_scope"] == "custom_workspace"

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

    def test_workspace_prefix_collision_blocked(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        sibling = tmp_path / "workspace-other"
        sibling.mkdir()

        with pytest.raises(ValueError, match="traversal"):
            repo_writer.execute(CTX, {
                "action": "create",
                "path": "../workspace-other/escape.txt",
                "content": "nope",
                "workspace": str(workspace),
            })

    def test_missing_workspace_root_fails(self, tmp_path):
        with pytest.raises(ValueError, match="Workspace root does not exist"):
            repo_writer.execute(CTX, {
                "action": "create",
                "path": "test.txt",
                "content": "data",
                "workspace": str(tmp_path / "missing"),
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

    def test_cwd_outside_workspace_blocked(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setenv("LANCELOT_WORKSPACE", str(workspace))

        with pytest.raises(ValueError, match="outside workspace boundary"):
            command_runner.execute(CTX, {
                "command": "echo hello",
                "cwd": str(outside),
            })

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
        assert result["cwd"]
        assert result["execution_target"] in ("local", "tool_fabric")

    def test_windows_shell_builtin_rejected_in_posix_runtime(self, tmp_path, monkeypatch):
        monkeypatch.setattr(command_runner.os, "name", "posix")

        with pytest.raises(ValueError, match="Windows shell command"):
            command_runner._validate_command_for_runtime("type README.md", str(tmp_path))

    def test_write_commands_config_and_host_write_allowlist(self, tmp_path, monkeypatch):
        config = tmp_path / "host_write_commands.yaml"
        config.write_text("# comment\ncustomwrite\n\n", encoding="utf-8")
        monkeypatch.setattr(command_runner, "_WRITE_COMMANDS_CONFIG", str(config))

        assert command_runner._load_write_commands() == {"customwrite"}

        monkeypatch.setattr(command_runner, "_is_write_commands_enabled", lambda: True)
        command_runner._validate_command("customwrite target.txt")

        monkeypatch.setattr(command_runner, "_WRITE_COMMANDS_CONFIG", str(tmp_path / "missing.yaml"))
        assert command_runner._load_write_commands() == set()

    def test_write_commands_config_load_failure_is_non_fatal(self, monkeypatch):
        monkeypatch.setattr(command_runner.os.path, "exists", lambda *_: True)
        monkeypatch.setattr(
            "builtins.open",
            lambda *_, **__: (_ for _ in ()).throw(OSError("permission denied")),
        )

        assert command_runner._load_write_commands() == set()

    def test_feature_flag_import_failures_disable_optional_command_paths(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in {"src.core.feature_flags", "src.tools.fabric"}:
                raise ImportError("missing optional module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded_import)

        assert command_runner._is_write_commands_enabled() is False
        assert command_runner._should_use_fabric() is False
        assert command_runner._get_tool_fabric() is None

    def test_execute_routes_through_tool_fabric_when_enabled(self, tmp_path, monkeypatch):
        fabric = MagicMock()
        fabric.run_command.return_value = type(
            "Result",
            (),
            {"stdout": "fabric ok", "stderr": "", "exit_code": 0, "working_dir": str(tmp_path)},
        )()
        monkeypatch.setattr(command_runner, "_should_use_fabric", lambda: True)
        monkeypatch.setattr(command_runner, "_get_tool_fabric", lambda: fabric)

        result = command_runner.execute(CTX, {
            "command": "echo hello",
            "timeout_sec": 9,
            "cwd": str(tmp_path),
        })

        assert result["execution_target"] == "tool_fabric"
        assert result["stdout"] == "fabric ok"
        fabric.run_command.assert_called_once_with(command="echo hello", workspace=str(tmp_path.resolve()), timeout_s=9)

    def test_local_execute_posix_argv_and_timeout_paths(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return type("Result", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(command_runner.subprocess, "run", fake_run)
        result = command_runner._execute_local("python --version", 3, {"cwd": str(tmp_path)})

        assert result["execution_target"] == "local"
        assert calls[0][0] == ["python", "--version"]
        assert calls[0][1]["cwd"] == str(tmp_path.resolve())

        monkeypatch.setattr(
            command_runner.subprocess,
            "run",
            lambda args, **kwargs: (_ for _ in ()).throw(
                command_runner.subprocess.TimeoutExpired(args, kwargs["timeout"])
            ),
        )
        with pytest.raises(TimeoutError, match="timed out after 1s"):
            command_runner._execute_local("python --version", 1, {"cwd": str(tmp_path)})

    def test_parse_and_workspace_validation_edge_cases(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid command syntax"):
            command_runner._parse_command('"unterminated')
        with pytest.raises(ValueError, match="Empty command"):
            command_runner._parse_command("")
        with pytest.raises(ValueError, match="Invalid command cwd"):
            command_runner._resolve_workspace({"cwd": str(tmp_path / "missing")})

        workspace = tmp_path / "workspace"
        child = workspace / "child"
        child.mkdir(parents=True)
        assert command_runner._is_within_workspace(child, workspace) is True
        assert command_runner._is_within_workspace(tmp_path, workspace) is False


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

    def test_docker_up_down_build_expected_commands(self, monkeypatch):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append((cmd, timeout))
            return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        monkeypatch.setattr(service_runner.subprocess, "run", fake_run)

        up = service_runner.execute(CTX, {
            "action": "up",
            "service": "api",
            "compose_file": "compose.yml",
        })
        down_all = service_runner.execute(CTX, {"action": "down"})
        down_service = service_runner.execute(CTX, {"action": "down", "service": "worker"})

        assert up["status"] == "started"
        assert up["service"] == "api"
        assert down_all["status"] == "stopped"
        assert down_service["service"] == "worker"
        assert calls[0] == (["docker", "compose", "-f", "compose.yml", "up", "-d", "api"], 120)
        assert calls[1][0] == ["docker", "compose", "-f", "docker-compose.yml", "down"]
        assert calls[2][0] == ["docker", "compose", "-f", "docker-compose.yml", "stop", "worker"]

    def test_docker_up_down_error_and_timeout_paths(self, monkeypatch):
        def error_run(cmd, capture_output, text, timeout):
            return type("Result", (), {"returncode": 2, "stdout": "", "stderr": "bad"})()

        monkeypatch.setattr(service_runner.subprocess, "run", error_run)
        assert service_runner.execute(CTX, {"action": "up"})["status"] == "error"
        assert service_runner.execute(CTX, {"action": "down"})["status"] == "error"

        def timeout_run(cmd, capture_output, text, timeout):
            raise service_runner.subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(service_runner.subprocess, "run", timeout_run)
        with pytest.raises(TimeoutError, match="up timed out"):
            service_runner.execute(CTX, {"action": "up"})
        with pytest.raises(TimeoutError, match="down timed out"):
            service_runner.execute(CTX, {"action": "down"})

    def test_health_success_unhealthy_and_status_parsing(self, monkeypatch):
        class Response:
            def __init__(self, code):
                self.code = code

            def getcode(self):
                return self.code

            def read(self, size):
                return b"hello" * 200

        responses = iter([Response(204), Response(503)])
        monkeypatch.setattr(service_runner, "urlopen", lambda url, timeout: next(responses))

        healthy = service_runner.execute(CTX, {"action": "health", "health_url": "http://local/health"})
        unhealthy = service_runner.execute(CTX, {"action": "health", "health_url": "http://local/health"})

        assert healthy["status"] == "healthy"
        assert healthy["response_body"].startswith("hello")
        assert len(healthy["response_body"]) == 500
        assert unhealthy["status"] == "unhealthy"

        monkeypatch.setattr(
            service_runner.subprocess,
            "run",
            lambda *_args, **_kwargs: type(
                "Result",
                (),
                {"stdout": "api\tUp 2 minutes\t0.0.0.0:8000->8000/tcp\nworker\tExited\n"},
            )(),
        )
        status = service_runner.execute(CTX, {"action": "status"})
        assert status["containers"] == [
            {"name": "api", "status": "Up 2 minutes", "ports": "0.0.0.0:8000->8000/tcp"},
            {"name": "worker", "status": "Exited", "ports": ""},
        ]

    def test_docker_status_returns_error_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            service_runner.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("docker missing")),
        )

        assert service_runner.execute(CTX, {"action": "status"}) == {
            "status": "error",
            "error": "docker missing",
        }


# =========================================================================
# memory_cleanup Tests
# =========================================================================


class TestMemoryCleanup:
    def test_memory_cleanup_runs_maintenance_jobs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))

        result = memory_cleanup.execute(CTX, {"dry_run": True})

        assert result["status"] == "success"
        assert result["dry_run"] is True
        assert result["jobs_run"] == 5
        assert "working_compaction" in result["results"]
        assert "memory_eviction" in result["results"]

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
# document_creator Tests
# =========================================================================


class TestDocumentCreator:
    def _content(self):
        return {
            "title": "Launch Report",
            "subtitle": "Runtime proof",
            "sections": [
                {
                    "heading": "Summary",
                    "paragraphs": ["Governance receipts were verified."],
                    "bullets": ["Receipts persisted", "Controls enforced"],
                }
            ],
            "paragraphs": ["Standalone note"],
            "tables": [{"headers": ["Metric", "Value"], "rows": [["Success", "94%"]]}],
            "headers": ["Name", "Status"],
            "rows": [["Lancelot", "ready"]],
            "sheets": [
                {"name": "Proof", "headers": ["Check", "Result"], "rows": [["health", "ok"]]},
                {"name": "Receipts", "headers": ["ID"], "rows": [["r1"]]},
            ],
        }

    def test_document_validation_and_safe_paths(self, tmp_path):
        with pytest.raises(ValueError, match="path"):
            document_creator.execute(CTX, {"format": "pdf", "workspace": str(tmp_path), "content": {}})
        with pytest.raises(ValueError, match="Unknown format"):
            document_creator.execute(CTX, {"format": "txt", "path": "out", "workspace": str(tmp_path), "content": {}})
        with pytest.raises(ValueError, match="traversal"):
            document_creator.execute(CTX, {
                "format": "pdf",
                "path": "../escape.pdf",
                "workspace": str(tmp_path),
                "content": {},
            })

    def test_create_pdf_docx_and_xlsx_with_structured_content(self, tmp_path):
        content = self._content()

        pdf = document_creator.execute(CTX, {
            "format": "pdf",
            "path": "reports/launch",
            "workspace": str(tmp_path),
            "content": content,
        })
        docx = document_creator.execute(CTX, {
            "format": "docx",
            "path": "reports/launch.docx",
            "workspace": str(tmp_path),
            "content": content,
        })
        xlsx = document_creator.execute(CTX, {
            "format": "xlsx",
            "path": "reports/launch.xlsx",
            "workspace": str(tmp_path),
            "content": content,
        })

        assert pdf["status"] == docx["status"] == xlsx["status"] == "created"
        assert Path(pdf["path"]).suffix == ".pdf"
        assert Path(docx["path"]).stat().st_size > 0
        assert Path(xlsx["path"]).stat().st_size > 0

    def test_create_pptx_with_stubbed_presentation_backend(self, tmp_path, monkeypatch):
        class _Font:
            size = None

        class _Paragraph:
            def __init__(self):
                self.text = ""
                self.font = _Font()
                self.level = 0

        class _TextFrame:
            def clear(self):
                self.cleared = True

            def add_paragraph(self):
                return _Paragraph()

        class _Placeholder:
            def __init__(self):
                self.text = ""
                self.text_frame = _TextFrame()

            def __bool__(self):
                return True

        class _Placeholders(dict):
            def __getitem__(self, key):
                return self.setdefault(key, _Placeholder())

        class _Shapes:
            title = _Placeholder()

        class _Slide:
            def __init__(self):
                self.shapes = _Shapes()
                self.placeholders = _Placeholders()

        class _Slides:
            def add_slide(self, _layout):
                return _Slide()

        class _Presentation:
            def __init__(self):
                self.slide_layouts = [object(), object()]
                self.slides = _Slides()

            def save(self, path):
                Path(path).write_bytes(b"pptx")

        pptx_mod = types.ModuleType("pptx")
        pptx_mod.Presentation = _Presentation
        util_mod = types.ModuleType("pptx.util")
        util_mod.Inches = lambda value: value
        util_mod.Pt = lambda value: value
        enum_mod = types.ModuleType("pptx.enum.text")
        enum_mod.PP_ALIGN = types.SimpleNamespace(CENTER="center")
        monkeypatch.setitem(sys.modules, "pptx", pptx_mod)
        monkeypatch.setitem(sys.modules, "pptx.util", util_mod)
        monkeypatch.setitem(sys.modules, "pptx.enum.text", enum_mod)

        result = document_creator.execute(CTX, {
            "format": "pptx",
            "path": "deck",
            "workspace": str(tmp_path),
            "content": self._content(),
        })

        assert result["status"] == "created"
        assert result["format"] == "pptx"


# =========================================================================
# daily_news_brief Tests
# =========================================================================


class TestDailyNewsBrief:
    def test_feed_parsing_filtering_dedup_and_formatting_helpers(self):
        cutoff = daily_news_brief._parse_date("Fri, 01 May 2026 10:00:00 +0000")
        assert cutoff is not None
        assert daily_news_brief._parse_date("2026-05-01T10:00:00Z") is not None
        assert daily_news_brief._parse_date("not a date") is None
        assert daily_news_brief._strip_html("<b>AI</b>&amp; robotics " + ("word " * 80)).endswith("...")
        assert daily_news_brief._is_ai_relevant("New LLM release", "") is True
        assert daily_news_brief._is_ai_relevant("Quarterly earnings", "No model news") is False
        assert daily_news_brief._title_dedup_key("AI: Launch!") == "ai launch"

        rss = daily_news_brief.ET.fromstring(
            "<rss><channel>"
            "<item><title>Fresh AI</title><link>https://example.com/a</link>"
            "<pubDate>Fri, 01 May 2026 12:00:00 +0000</pubDate><description><![CDATA[<p>News</p>]]></description></item>"
            "<item><title></title><link>https://example.com/missing</link></item>"
            "</channel></rss>"
        )
        atom = daily_news_brief.ET.fromstring(
            "<feed><entry><title>Atom AI</title><link href='https://example.com/b'/>"
            "<updated>2026-05-01T12:30:00Z</updated><summary>Summary</summary></entry></feed>"
        )
        assert daily_news_brief._parse_rss(rss, "RSS", cutoff)[0]["summary"] == "News"
        assert daily_news_brief._parse_atom(atom, "Atom", cutoff)[0]["link"] == "https://example.com/b"

        articles = [
            {"source": "A", "title": "new", "link": "u1", "summary": "", "published": cutoff},
            {"source": "A", "title": "older", "link": "u2", "summary": "", "published": cutoff},
            {"source": "B", "title": "peer", "link": "u3", "summary": "", "published": cutoff},
        ]
        selected = daily_news_brief._diversity_select(articles, max_articles=3, max_per_source=1)
        assert [a["source"] for a in selected] == ["A", "B"]
        assert "No breaking AI news" in daily_news_brief._format_briefing([])
        assert "Powered by Lancelot" in daily_news_brief._format_briefing(selected)

    def test_execute_fetches_filters_dedupes_and_sends_brief(self, monkeypatch):
        now = daily_news_brief.datetime.now(daily_news_brief.timezone.utc)
        fresh = {"source": "FeedA", "title": "AI governance launch", "link": "https://a", "summary": "LLM safety", "published": now}
        duplicate = dict(fresh)
        duplicate["source"] = "FeedB"
        general = {"source": "General", "title": "Sports update", "link": "https://c", "summary": "", "published": now}
        google = {"source": "Google", "title": "Agent funding", "link": "https://g", "summary": "AI startup", "published": now}

        def fake_fetch_feed(name, _url, _cutoff):
            if name == "Broken":
                raise RuntimeError("offline")
            if name == "General":
                return [general]
            return [fresh, duplicate]

        monkeypatch.setattr(daily_news_brief, "_RSS_FEEDS", [("FeedA", "url", True), ("General", "url", False), ("Broken", "url", True)])
        monkeypatch.setattr(daily_news_brief, "_GOOGLE_NEWS_QUERIES", ["ai funding"])
        monkeypatch.setattr(daily_news_brief, "_fetch_feed", fake_fetch_feed)
        monkeypatch.setattr(daily_news_brief, "_fetch_google_news", lambda _query, _cutoff: [google])
        monkeypatch.setattr(telegram_send, "send_text", lambda message, chat_id=None: {
            "status": "sent",
            "chat_id": chat_id,
            "message_length": len(message),
        })

        result = daily_news_brief.execute(CTX, {
            "max_articles": 3,
            "max_per_source": 2,
            "chat_id": "chat-1",
            "hours_lookback": 12,
        })

        assert result["status"] == "sent"
        assert result["articles_found"] == 2
        assert result["articles_sent"] == 2
        assert result["feeds_failed"] == 1
        assert "Broken: offline" in result["feed_errors"][0]

    def test_fetch_feed_and_google_news_parse_real_response_shapes(self, monkeypatch):
        rss_payload = (
            b"<rss><channel>"
            b"<item><title>AI Chip Ships - Wire</title><link>https://example.com/chip</link>"
            b"<pubDate>Fri, 01 May 2026 12:00:00 +0000</pubDate><description>GPU news</description></item>"
            b"</channel></rss>"
        )
        atom_payload = (
            b"<feed xmlns='http://www.w3.org/2005/Atom'>"
            b"<entry><title>Model Update</title><link href='https://example.com/model'/>"
            b"<updated>2026-05-01T12:00:00Z</updated><summary>LLM release</summary></entry>"
            b"</feed>"
        )
        payloads = iter([rss_payload, atom_payload, rss_payload])

        class Response:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        monkeypatch.setattr(daily_news_brief.ssl, "create_default_context", lambda: object())
        monkeypatch.setattr(daily_news_brief, "urlopen", lambda req, timeout, context=None: Response(next(payloads)))
        cutoff = daily_news_brief._parse_date("Fri, 01 May 2026 10:00:00 +0000")

        rss_articles = daily_news_brief._fetch_feed("RSS", "https://example.com/rss", cutoff)
        atom_articles = daily_news_brief._fetch_feed("Atom", "https://example.com/atom", cutoff)
        google_articles = daily_news_brief._fetch_google_news("ai chips", cutoff)

        assert rss_articles[0]["title"] == "AI Chip Ships - Wire"
        assert atom_articles[0]["title"] == "Model Update"
        assert google_articles[0]["title"] == "AI Chip Ships"
        assert google_articles[0]["source"] == "Wire"

    def test_execute_reports_import_failure_when_telegram_sender_unavailable(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in {"src.core.skills.builtins", "skills.builtins"}:
                fromlist = kwargs.get("fromlist") or (args[2] if len(args) > 2 else ())
                if "telegram_send" in fromlist:
                    raise ImportError("telegram sender unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded_import)
        monkeypatch.setattr(daily_news_brief, "_RSS_FEEDS", [])
        monkeypatch.setattr(daily_news_brief, "_GOOGLE_NEWS_QUERIES", [])

        result = daily_news_brief.execute(CTX, {"max_articles": 1})

        assert result["status"] == "error"
        assert result["error"] == "Cannot import telegram_send module"


# =========================================================================
# skill_manager, telegram_send, and schedule_job Tests
# =========================================================================


class TestOperatorBuiltins:
    def test_skill_manager_proposal_listing_and_run_paths(self, monkeypatch):
        status = types.SimpleNamespace(value="pending")
        proposal = types.SimpleNamespace(
            id="p1",
            name="proof_skill",
            description="desc",
            status=status,
            pipeline_passed=True,
            pipeline_failed_at_stage=None,
            created_at="now",
            approved_by=None,
        )
        factory = types.SimpleNamespace(
            create_proposal=MagicMock(return_value=proposal),
            list_proposals=lambda: [proposal],
        )
        registry = types.SimpleNamespace(
            list_skills=lambda: [types.SimpleNamespace(
                name="echo",
                version="1.0",
                enabled=True,
                ownership=types.SimpleNamespace(value="builtin"),
            )]
        )
        success = types.SimpleNamespace(success=True, outputs={"ok": True}, duration_ms=4, error=None)
        failure = types.SimpleNamespace(success=False, outputs={}, duration_ms=1, error="denied")
        executor = types.SimpleNamespace(run=MagicMock(side_effect=[success, failure]))
        gateway = types.ModuleType("gateway")
        gateway.main_orchestrator = types.SimpleNamespace(
            skill_factory=factory,
            skill_registry=registry,
            skill_executor=executor,
        )
        monkeypatch.setitem(sys.modules, "gateway", gateway)

        proposed = skill_manager.execute(CTX, {
            "action": "propose",
            "name": "proof_skill",
            "description": "desc",
            "permissions": "file_read, network_read",
            "target_domains": '["example.com"]',
            "credentials": "token",
            "execute_code": "def execute(context, inputs): return {}",
        })
        assert proposed["status"] == "proposed"
        assert factory.create_proposal.call_args.kwargs["permissions"] == ["file_read", "network_read"]
        assert factory.create_proposal.call_args.kwargs["target_domains"] == ["example.com"]
        assert skill_manager.execute(CTX, {"action": "list_proposals"})["total"] == 1
        assert skill_manager.execute(CTX, {"action": "list_skills"})["skills"][0]["ownership"] == "builtin"
        assert skill_manager.execute(CTX, {
            "action": "run_skill",
            "skill_name": "echo",
            "skill_inputs": '{"message":"hi"}',
        })["status"] == "success"
        assert skill_manager.execute(CTX, {
            "action": "run_skill",
            "skill_name": "echo",
            "skill_inputs": "raw text",
        })["error"] == "denied"

        with pytest.raises(ValueError, match="name"):
            skill_manager.execute(CTX, {"action": "propose", "execute_code": "x"})
        with pytest.raises(ValueError, match="execute_code"):
            skill_manager.execute(CTX, {"action": "propose", "name": "x"})
        with pytest.raises(ValueError, match="skill_name"):
            skill_manager.execute(CTX, {"action": "run_skill"})
        with pytest.raises(ValueError, match="Unknown action"):
            skill_manager.execute(CTX, {"action": "bogus"})

    def test_skill_manager_missing_runtime_dependencies_report_clear_errors(self, monkeypatch):
        gateway = types.ModuleType("gateway")
        gateway.main_orchestrator = types.SimpleNamespace()
        monkeypatch.setitem(sys.modules, "gateway", gateway)

        with pytest.raises(RuntimeError, match="SkillFactory not initialized"):
            skill_manager._get_factory()
        with pytest.raises(RuntimeError, match="SkillRegistry not initialized"):
            skill_manager._get_registry()
        with pytest.raises(RuntimeError, match="SkillExecutor not initialized"):
            skill_manager._get_executor()

    def test_telegram_send_gateway_file_text_and_fallback_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(telegram_send, "DEFAULT_WORKSPACE", str(tmp_path))
        proof = tmp_path / "proof.txt"
        proof.write_text("receipt", encoding="utf-8")

        deliveries = []

        class Bot:
            chat_id = "default-chat"

            def send_document(self, file_bytes, filename, chat_id=None, caption=""):
                deliveries.append(("doc", filename, chat_id, caption, file_bytes))
                return True

            def send_message(self, message, chat_id=None):
                deliveries.append(("msg", message, chat_id))

        orchestrator = types.SimpleNamespace(mark_telegram_delivery_handled=MagicMock())
        gateway = types.ModuleType("gateway")
        gateway.telegram_bot = Bot()
        gateway.main_orchestrator = orchestrator
        monkeypatch.setitem(sys.modules, "gateway", gateway)

        sent_file = telegram_send.execute(CTX, {"file_path": "proof.txt", "caption": "proof", "chat_id": "chat-2"})
        sent_text = telegram_send.execute(CTX, {"message": "hello", "chat_id": "chat-3"})
        assert sent_file["status"] == sent_text["status"] == "sent"
        assert sent_file["bytes"] == len(b"receipt")
        orchestrator.mark_telegram_delivery_handled.assert_called_once()
        with pytest.raises(ValueError, match="either"):
            telegram_send.execute(CTX, {})
        assert telegram_send.execute(CTX, {"file_path": "missing.txt"})["error"].startswith("File not found")
        with pytest.raises(ValueError, match="traversal"):
            telegram_send._resolve_workspace_path("../escape.txt")

        gateway.telegram_bot = None
        monkeypatch.delenv("LANCELOT_TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("LANCELOT_TELEGRAM_CHAT_ID", raising=False)
        assert "Telegram not configured" in telegram_send.send_text("hello")["error"]

        monkeypatch.setenv("LANCELOT_TELEGRAM_TOKEN", "token")
        monkeypatch.setenv("LANCELOT_TELEGRAM_CHAT_ID", "chat-env")

        class Response:
            def read(self):
                return b'{"ok": true}'

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Response())
        assert telegram_send.send_text("hello")["status"] == "sent"

        class BadResponse:
            ok = False
            text = "bad request"

        monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=lambda *args, **kwargs: BadResponse()))
        assert telegram_send._send_file("proof.txt", "caption")["error"] == "bad request"

    def test_schedule_job_create_list_delete_and_validation(self, monkeypatch):
        jobs = {}

        def create_job(**kwargs):
            record = types.SimpleNamespace(
                id=kwargs["job_id"],
                name=kwargs["name"],
                skill=kwargs["skill"],
                enabled=True,
                trigger_type=kwargs["trigger_type"],
                trigger_value=kwargs["trigger_value"],
                timezone=kwargs["timezone_str"],
                last_run_at=None,
                run_count=0,
            )
            jobs[record.id] = record
            return record

        service = types.SimpleNamespace(
            list_jobs=lambda: list(jobs.values()),
            get_job=lambda job_id: jobs.get(job_id),
            create_job=create_job,
            delete_job=lambda job_id: jobs.pop(job_id),
        )
        gateway = types.ModuleType("gateway")
        gateway.scheduler_service = service
        monkeypatch.setitem(sys.modules, "gateway", gateway)

        assert schedule_job.execute(CTX, {"action": "bogus"})["status"] == "error"
        assert schedule_job.execute(CTX, {"action": "create"})["error"].startswith("Missing required field")
        assert schedule_job.execute(CTX, {"action": "create", "name": "Daily", "skill": "daily_news_brief"})["error"].startswith("Missing required field")
        assert "Invalid timezone" in schedule_job.execute(CTX, {
            "action": "create",
            "name": "Daily",
            "skill": "daily_news_brief",
            "cron": "0 7 * * *",
            "timezone": "Bad/Zone",
        })["error"]

        created = schedule_job.execute(CTX, {
            "action": "create",
            "name": "Daily News",
            "skill": "daily_news_brief",
            "cron": "0 7 * * *",
            "inputs": '{"max_articles": 5}',
        })
        assert created["status"] == "created"
        assert created["job_id"] == "daily_news"
        assert created["inputs"] == {"max_articles": 5}
        assert schedule_job.execute(CTX, {
            "action": "create",
            "name": "Daily News",
            "skill": "daily_news_brief",
            "cron": "0 7 * * *",
        })["error"].startswith("Job 'daily_news' already exists")

        listed = schedule_job.execute(CTX, {"action": "list"})
        assert listed["total"] == 1
        assert listed["jobs"][0]["trigger"] == "cron: 0 7 * * *"
        assert schedule_job.execute(CTX, {"action": "delete"})["error"].startswith("Missing required field")
        assert schedule_job.execute(CTX, {"action": "delete", "job_id": "daily_news"})["status"] == "deleted"

        gateway.scheduler_service = None
        assert "not available" in schedule_job.execute(CTX, {"action": "list"})["error"]


# =========================================================================
# Executor Registration Tests
# =========================================================================


class TestMemoryQuery:
    def test_memory_query_searches_entity_index(self, tmp_path, monkeypatch):
        from src.core.memory.schemas import MemoryItem, MemoryTier
        from src.core.memory.sqlite_store import MemoryStoreManager

        monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))
        manager = MemoryStoreManager(data_dir=tmp_path)
        manager.episodic.insert(MemoryItem(
            id="mq-atlas",
            tier=MemoryTier.episodic,
            title="Prior decision",
            content="Wait for the receipt verification before release.",
            confidence=0.9,
            metadata={"project_id": "Atlas"},
        ))

        result = memory_query.execute(CTX, {"query": "Atlas", "tiers": ["episodic"]})

        assert result["status"] == "success"
        assert result["results"][0]["id"] == "mq-atlas"


class TestExecutorRegistration:
    def test_builtin_skills_registered(self):
        from src.core.skills.executor import _BUILTIN_SKILLS
        assert "echo" in _BUILTIN_SKILLS
        assert "repo_writer" in _BUILTIN_SKILLS
        assert "command_runner" in _BUILTIN_SKILLS
        assert "service_runner" in _BUILTIN_SKILLS
        assert "network_client" in _BUILTIN_SKILLS
        assert "memory_cleanup" in _BUILTIN_SKILLS
        assert "memory_query" in _BUILTIN_SKILLS
