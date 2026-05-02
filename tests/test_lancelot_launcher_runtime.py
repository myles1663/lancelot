import types


def test_launcher_initialization_docker_and_engine_paths(tmp_path, monkeypatch):
    from src.ui import lancelot_gui

    monkeypatch.chdir(tmp_path)
    launcher = lancelot_gui.LancelotLauncher()
    assert launcher.first_run is True

    profile = tmp_path / "lancelot_data" / "USER.md"
    profile.parent.mkdir()
    profile.write_text("OnboardingComplete: True", encoding="utf-8")
    (tmp_path / "first_run.flag").write_text("legacy", encoding="utf-8")
    complete = lancelot_gui.LancelotLauncher()
    assert complete.first_run is False
    assert not (tmp_path / "first_run.flag").exists()

    monkeypatch.setattr(
        lancelot_gui.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0),
    )
    assert complete.check_docker() is True

    class CalledProcess:
        pass

    monkeypatch.setattr(
        lancelot_gui.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(lancelot_gui.subprocess.CalledProcessError(1, "docker")),
    )
    assert complete.check_docker() is False
    monkeypatch.setattr(
        lancelot_gui.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )
    assert complete.check_docker() is False

    popen_calls = []
    monkeypatch.setattr(lancelot_gui.subprocess, "Popen", lambda cmd, shell: popen_calls.append((cmd, shell)) or object())
    complete.start_engine()
    assert popen_calls == [(lancelot_gui.COMPOSE_UP_CMD, True)]
    monkeypatch.setattr(lancelot_gui.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no compose")))
    complete.start_engine()


def test_launcher_monitor_timeout_and_restart_signal(tmp_path, monkeypatch):
    from src.ui import lancelot_gui

    monkeypatch.chdir(tmp_path)
    flags = tmp_path / "lancelot_data" / "FLAGS"
    flags.mkdir(parents=True)
    restart = flags / "RESTART_REQUIRED"
    restart.write_text("1", encoding="utf-8")
    run_calls = []
    monkeypatch.setattr(lancelot_gui.subprocess, "run", lambda cmd, shell=True: run_calls.append(cmd))
    monkeypatch.setattr(lancelot_gui.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        lancelot_gui.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(lancelot_gui.requests.RequestException("down")),
    )
    html = []
    launcher = lancelot_gui.LancelotLauncher()
    launcher.window = types.SimpleNamespace(load_html=lambda content: html.append(content), load_url=lambda url: None)

    launcher.monitor_health()

    assert "docker-compose restart" in run_calls
    assert any("Connection Failed" in content for content in html)


def test_js_api_and_start_launcher_paths(monkeypatch):
    from src.ui import lancelot_gui

    monitor_calls = []
    opened = []
    launcher = types.SimpleNamespace(monitor_health=lambda: monitor_calls.append("monitor"))
    api = lancelot_gui.JS_API(launcher)
    monkeypatch.setattr(lancelot_gui.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=lambda: target()))
    monkeypatch.setattr(lancelot_gui.webbrowser, "open", lambda url: opened.append(url))
    api.retry_connection()
    api.open_external("https://example.com")
    assert monitor_calls == ["monitor"]
    assert opened == ["https://example.com"]

    windows = []
    starts = []

    class Launcher:
        def __init__(self):
            self.window = None

        def check_docker(self):
            return False

        def start_engine(self):
            raise AssertionError("should not start without docker")

        def monitor_health(self):
            monitor_calls.append("started")

    monkeypatch.setattr(lancelot_gui, "LancelotLauncher", Launcher)
    monkeypatch.setattr(lancelot_gui.webview, "create_window", lambda *args, **kwargs: windows.append((args, kwargs)) or object())
    monkeypatch.setattr(lancelot_gui.webview, "start", lambda **kwargs: starts.append(kwargs))
    lancelot_gui.start_launcher()
    assert "Docker Required" in windows[-1][1]["html"]
    assert starts[-1] == {}

    class DockerLauncher(Launcher):
        def check_docker(self):
            return True

        def start_engine(self):
            monitor_calls.append("engine")

    monkeypatch.setattr(lancelot_gui, "LancelotLauncher", DockerLauncher)
    lancelot_gui.start_launcher()
    assert monitor_calls[-2:] == ["engine", "started"]
    assert starts[-1] == {"debug": False}
