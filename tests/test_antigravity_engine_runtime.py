import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

from src.agents import antigravity_engine as ag
from src.agents.antigravity_engine import AntigravityEngine, EngineMode


class _Page:
    def __init__(self, url="https://example.com", title="Example", fail_goto=False, fail_title=False):
        self.url = url
        self._title = title
        self.fail_goto = fail_goto
        self.fail_title = fail_title
        self.goto_calls = []
        self.screenshots = []
        self.closed = False

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self.fail_goto:
            raise RuntimeError("navigation failed")
        self.url = url

    async def title(self):
        if self.fail_title:
            raise RuntimeError("title failed")
        return self._title

    async def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)
        if kwargs.get("path") == "explode":
            raise RuntimeError("screenshot failed")
        return b"png"

    async def close(self):
        self.closed = True


class _Context:
    def __init__(self, pages=None, fail_storage=False):
        self.pages = pages or []
        self.fail_storage = fail_storage
        self.new_context_kwargs = []
        self.storage_paths = []
        self.closed = False

    async def new_page(self):
        page = _Page()
        self.pages.append(page)
        return page

    async def storage_state(self, path):
        if self.fail_storage:
            raise RuntimeError("storage failed")
        self.storage_paths.append(path)

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self, contexts=None, fail_new_context=False):
        self.contexts = contexts if contexts is not None else []
        self.fail_new_context = fail_new_context
        self.new_context_calls = []
        self.closed = False

    async def new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        if self.fail_new_context and "storage_state" in kwargs:
            raise RuntimeError("bad storage")
        context = _Context()
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, browser=None, cdp_browser=None, fail_cdp=False):
        self.browser = browser or _Browser()
        self.cdp_browser = cdp_browser or _Browser()
        self.fail_cdp = fail_cdp
        self.launch_calls = []
        self.cdp_calls = []

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser

    async def connect_over_cdp(self, cdp_url):
        self.cdp_calls.append(cdp_url)
        if self.fail_cdp:
            raise RuntimeError("cdp refused")
        return self.cdp_browser


class _Playwright:
    def __init__(self, chromium=None):
        self.chromium = chromium or _Chromium()
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _PlaywrightStarter:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


def _patch_playwright(monkeypatch, playwright):
    monkeypatch.setattr(ag, "async_playwright", lambda: _PlaywrightStarter(playwright))


def test_engine_initializes_directories_and_normalizes_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_BROWSER_MODE", "unsupported")
    monkeypatch.setenv("LANCELOT_CDP_URL", "ws://chrome")

    engine = AntigravityEngine(data_dir=str(tmp_path), headless=False)

    assert engine.mode is EngineMode.ISOLATED
    assert engine.cdp_url == "ws://chrome"
    assert (tmp_path / "chrome_session").is_dir()
    assert (tmp_path / "artifacts" / "browser_proof").is_dir()
    assert engine.get_status()["running"] is False


@pytest.mark.asyncio
async def test_start_isolated_restores_saved_session_and_is_idempotent(monkeypatch, tmp_path):
    storage = tmp_path / "chrome_session" / "storage_state.json"
    storage.parent.mkdir(parents=True)
    storage.write_text("{}", encoding="utf-8")
    browser = _Browser()
    playwright = _Playwright(_Chromium(browser=browser))
    _patch_playwright(monkeypatch, playwright)
    engine = AntigravityEngine(data_dir=str(tmp_path), headless=True, mode="isolated")

    await engine.start()
    await engine.start()

    assert engine.context is browser.contexts[0]
    assert browser.new_context_calls == [
        {"storage_state": str(storage), "viewport": {"width": 1280, "height": 800}}
    ]
    assert playwright.chromium.launch_calls[0]["headless"] is True


@pytest.mark.asyncio
async def test_start_isolated_falls_back_when_saved_session_is_bad(monkeypatch, tmp_path):
    storage = tmp_path / "chrome_session" / "storage_state.json"
    storage.parent.mkdir(parents=True)
    storage.write_text("{}", encoding="utf-8")
    browser = _Browser(fail_new_context=True)
    _patch_playwright(monkeypatch, _Playwright(_Chromium(browser=browser)))
    engine = AntigravityEngine(data_dir=str(tmp_path), mode="isolated")

    await engine.start()

    assert browser.new_context_calls == [
        {"storage_state": str(storage), "viewport": {"width": 1280, "height": 800}},
        {"viewport": {"width": 1280, "height": 800}},
    ]
    assert engine.context is browser.contexts[0]


@pytest.mark.asyncio
async def test_bridge_mode_uses_existing_context_creates_one_or_falls_back(monkeypatch, tmp_path):
    existing = _Context(pages=[_Page(url="https://app.local")])
    cdp_browser = _Browser(contexts=[existing])
    chromium = _Chromium(cdp_browser=cdp_browser)
    _patch_playwright(monkeypatch, _Playwright(chromium))
    bridge = AntigravityEngine(data_dir=str(tmp_path / "bridge"), mode="bridge", cdp_url="http://127.0.0.1:9222")

    await bridge.start()

    assert bridge.context is existing
    assert chromium.cdp_calls == ["http://127.0.0.1:9222"]

    empty_browser = _Browser(contexts=[])
    _patch_playwright(monkeypatch, _Playwright(_Chromium(cdp_browser=empty_browser)))
    empty = AntigravityEngine(data_dir=str(tmp_path / "empty"), mode="bridge", cdp_url="http://127.0.0.1:9222")
    await empty.start()
    assert empty.context is empty_browser.contexts[0]

    fallback_browser = _Browser()
    _patch_playwright(monkeypatch, _Playwright(_Chromium(browser=fallback_browser, fail_cdp=True)))
    fallback = AntigravityEngine(data_dir=str(tmp_path / "fallback"), mode="bridge", cdp_url="http://127.0.0.1:9222")
    await fallback.start()
    assert fallback.mode is EngineMode.ISOLATED
    assert fallback.context is fallback_browser.contexts[0]


@pytest.mark.asyncio
async def test_stop_saves_and_closes_isolated_resources(monkeypatch, tmp_path):
    context = _Context()
    browser = _Browser(contexts=[context])
    playwright = _Playwright()
    session = types.SimpleNamespace(close=AsyncMock())
    engine = AntigravityEngine(data_dir=str(tmp_path), mode="isolated")
    engine.playwright = playwright
    engine.browser = browser
    engine.context = context
    engine._browser_use_session = session

    await engine.stop()

    assert context.storage_paths == [str(tmp_path / "chrome_session" / "storage_state.json")]
    assert context.closed is True
    assert browser.closed is True
    assert playwright.stopped is True
    assert session.close.await_count == 1
    assert engine.playwright is None


@pytest.mark.asyncio
async def test_stop_disconnects_bridge_without_closing_user_browser(tmp_path):
    context = _Context()
    browser = _Browser(contexts=[context])
    playwright = _Playwright()
    engine = AntigravityEngine(data_dir=str(tmp_path), mode="bridge", cdp_url="ws://chrome")
    engine.playwright = playwright
    engine.browser = browser
    engine.context = context

    await engine.stop()

    assert context.closed is False
    assert browser.closed is False
    assert playwright.stopped is True
    assert engine.context is None


@pytest.mark.asyncio
async def test_navigate_success_and_error_capture_evidence(tmp_path):
    engine = AntigravityEngine(data_dir=str(tmp_path))
    success_page = _Page(title="Dashboard")
    engine.context = _Context()
    engine.context.new_page = AsyncMock(return_value=success_page)
    engine._capture_evidence = AsyncMock(return_value="proof.png")
    engine._save_session = AsyncMock()

    success = await engine.navigate("https://example.com/dashboard")

    assert success == {
        "status": "success",
        "title": "Dashboard",
        "url": "https://example.com/dashboard",
        "receipt": "proof.png",
    }
    assert success_page.closed is True
    assert engine._save_session.await_count == 1

    error_page = _Page(fail_goto=True)
    engine.context.new_page = AsyncMock(return_value=error_page)

    error = await engine.navigate("https://example.com/fail")

    assert error["status"] == "error"
    assert "navigation failed" in error["error"]
    assert error_page.closed is True


@pytest.mark.asyncio
async def test_tab_helpers_and_visual_audit(tmp_path):
    engine = AntigravityEngine(data_dir=str(tmp_path))
    good = _Page(url="https://example.com/app", title="App")
    bad_title = _Page(url="https://example.com/other", fail_title=True)
    engine.context = _Context(pages=[good, bad_title])

    assert engine.get_open_tabs() == [
        {"url": "https://example.com/app", "title": ""},
        {"url": "https://example.com/other", "title": ""},
    ]
    assert await engine.get_open_tabs_async() == [
        {"url": "https://example.com/app", "title": "App"},
        {"url": "https://example.com/other", "title": "(unknown)"},
    ]
    assert await engine.interact_with_tab("APP") is good
    assert await engine.interact_with_tab("missing") is None

    receipt = await engine._capture_evidence(good, "Open app: step 1")
    assert receipt.endswith("_Openappstep1.png")
    assert good.screenshots[0]["path"] == receipt

    failing = _Page()
    failing.screenshot = AsyncMock(side_effect=RuntimeError("capture failed"))
    assert await engine._capture_evidence(failing, "bad") is None


def test_open_tab_helpers_return_empty_without_context(tmp_path):
    engine = AntigravityEngine(data_dir=str(tmp_path))

    assert engine.get_open_tabs() == []
    assert asyncio.run(engine.get_open_tabs_async()) == []
    assert asyncio.run(engine.interact_with_tab("anything")) is None


@pytest.mark.asyncio
async def test_run_agent_task_handles_missing_package_and_runtime_errors(monkeypatch, tmp_path):
    engine = AntigravityEngine(data_dir=str(tmp_path))
    engine.context = _Context(pages=[_Page()])

    missing = await engine.run_agent_task("do work")
    assert missing["status"] == "error"
    assert "browser-use package not installed" in missing["error"]

    browser_use = types.ModuleType("browser_use")
    browser_use.Agent = lambda **_kwargs: types.SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("agent failed")))
    browser_session_mod = types.ModuleType("browser_use.browser.session")
    browser_session_mod.BrowserSession = lambda **kwargs: kwargs
    browser_mod = types.ModuleType("browser_use.browser")
    browser_mod.BrowserProfile = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "browser_use", browser_use)
    monkeypatch.setitem(sys.modules, "browser_use.browser.session", browser_session_mod)
    monkeypatch.setitem(sys.modules, "browser_use.browser", browser_mod)
    monkeypatch.setattr(engine, "_get_agent_llm", lambda model_name=None: object())

    failed = await engine.run_agent_task("do work", model_name="model")

    assert failed["status"] == "error"
    assert failed["task"] == "do work"
    assert "agent failed" in failed["error"]


@pytest.mark.asyncio
async def test_run_agent_task_success_uses_bridge_session_and_captures_evidence(monkeypatch, tmp_path):
    class _Agent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self):
            return "done"

    captured_sessions = []
    browser_use = types.ModuleType("browser_use")
    browser_use.Agent = _Agent
    browser_session_mod = types.ModuleType("browser_use.browser.session")
    browser_session_mod.BrowserSession = lambda **kwargs: captured_sessions.append(kwargs) or kwargs
    browser_mod = types.ModuleType("browser_use.browser")
    browser_mod.BrowserProfile = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "browser_use", browser_use)
    monkeypatch.setitem(sys.modules, "browser_use.browser.session", browser_session_mod)
    monkeypatch.setitem(sys.modules, "browser_use.browser", browser_mod)

    engine = AntigravityEngine(data_dir=str(tmp_path), mode="bridge", cdp_url="ws://chrome")
    engine.context = _Context(pages=[_Page()])
    engine._get_agent_llm = lambda model_name=None: "llm"
    engine._capture_evidence = AsyncMock(return_value="proof.png")

    result = await engine.run_agent_task("open app")

    assert result == {"status": "success", "task": "open app", "result": "done"}
    assert captured_sessions[0]["cdp_url"] == "ws://chrome"
    assert engine._capture_evidence.await_count == 1


def test_llm_builder_selection_fallbacks_and_missing_keys(monkeypatch, tmp_path):
    engine = AntigravityEngine(data_dir=str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai")

    with pytest.raises(RuntimeError, match="No LLM available"):
        engine._get_agent_llm()

    class _LLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    openai_mod = types.ModuleType("langchain_openai")
    openai_mod.ChatOpenAI = _LLM
    gemini_mod = types.ModuleType("langchain_google_genai")
    gemini_mod.ChatGoogleGenerativeAI = _LLM
    anthropic_mod = types.ModuleType("langchain_anthropic")
    anthropic_mod.ChatAnthropic = _LLM
    monkeypatch.setitem(sys.modules, "langchain_openai", openai_mod)
    monkeypatch.setitem(sys.modules, "langchain_google_genai", gemini_mod)
    monkeypatch.setitem(sys.modules, "langchain_anthropic", anthropic_mod)

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    selected = engine._get_agent_llm("custom-model")
    assert selected.kwargs == {"model": "custom-model", "api_key": "openai-key"}

    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    fallback = engine._get_agent_llm()
    assert fallback.kwargs["model"] == "gpt-4o"

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert engine._build_gemini_llm().kwargs["google_api_key"] == "gemini-key"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    assert engine._build_anthropic_llm().kwargs["api_key"] == "anthropic-key"

    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    xai = engine._build_xai_llm()
    assert xai.kwargs["base_url"] == "https://api.x.ai/v1"
