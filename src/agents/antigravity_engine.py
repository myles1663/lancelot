"""
Antigravity Engine - Dual-Mode Browser Core
--------------------------------------------
Orchestrates browser automation with two distinct modes:

**Isolated Mode** (Playwright):
  Launches a private headless Chromium instance inside the container.
  Used for sandboxed tasks: scraping, screenshots, testing.

**Bridge Mode** (Browser Use + CDP):
  Connects to the user's real Chrome browser via Chrome DevTools Protocol.
  Used for authenticated enterprise apps (Salesforce, Square9, etc.)
  that require the user's existing login session.

Features:
- Session Bridge: Persists cookies/localStorage across restarts (isolated mode)
- Visual Audit: Captures proof-of-work screenshots for every action
- AI Agent: Browser Use agent can execute natural-language browser tasks
- Stealth: Headless with sandboxing flags (isolated mode)
"""

import os
import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from security import AuditLogger

logger = logging.getLogger("lancelot.antigravity")


class EngineMode(str, Enum):
    """Browser engine operation mode."""
    ISOLATED = "isolated"   # Private headless Chromium (Playwright)
    BRIDGE = "bridge"       # Connect to user's Chrome via CDP


class AntigravityEngine:
    """Dual-mode browser automation engine."""

    def __init__(
        self,
        data_dir: str = "/home/lancelot/data",
        headless: bool = True,
        mode: Optional[str] = None,
        cdp_url: Optional[str] = None,
    ):
        self.data_dir = data_dir
        self.session_dir = os.path.join(data_dir, "chrome_session")
        self.evidence_dir = os.path.join(data_dir, "artifacts", "browser_proof")
        self.headless = headless
        self.audit_logger = AuditLogger()

        # Determine mode
        self.cdp_url = cdp_url or os.getenv("LANCELOT_CDP_URL", "")
        mode_str = (mode or os.getenv("LANCELOT_BROWSER_MODE", "isolated")).lower()
        self.mode = EngineMode(mode_str) if mode_str in ("isolated", "bridge") else EngineMode.ISOLATED

        # Playwright objects
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

        # Browser Use agent (lazy-loaded)
        self._browser_use_session = None

        # Ensure directories exist
        os.makedirs(self.session_dir, exist_ok=True)
        os.makedirs(self.evidence_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Initialize the browser engine in the configured mode."""
        if self.playwright:
            return  # Already running

        logger.info(f"Antigravity Engine starting in {self.mode.value} mode...")
        self.playwright = await async_playwright().start()

        if self.mode == EngineMode.BRIDGE and self.cdp_url:
            await self._start_bridge()
        else:
            await self._start_isolated()

    async def _start_isolated(self):
        """Launch a private headless Chromium instance."""
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        # Session Bridge: restore cookies if available
        storage_state_path = os.path.join(self.session_dir, "storage_state.json")
        if os.path.exists(storage_state_path):
            try:
                self.context = await self.browser.new_context(
                    storage_state=storage_state_path,
                    viewport={"width": 1280, "height": 800},
                )
                logger.info("Session Bridge: Restored saved context.")
            except Exception as e:
                logger.warning(f"Session Bridge: Failed to load state ({e}). Starting fresh.")
                self.context = await self.browser.new_context(
                    viewport={"width": 1280, "height": 800}
                )
        else:
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800}
            )

        logger.info("Antigravity Engine: Isolated mode ready.")

    async def _start_bridge(self):
        """Connect to an existing Chrome browser via CDP."""
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
            # Use the first existing context (the user's authenticated session)
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                logger.info(f"Bridge mode: Connected to Chrome at {self.cdp_url} "
                            f"({len(contexts)} context(s), "
                            f"{len(self.context.pages)} page(s))")
            else:
                self.context = await self.browser.new_context(
                    viewport={"width": 1280, "height": 800}
                )
                logger.info(f"Bridge mode: Connected (new context created).")
        except Exception as e:
            logger.error(f"Bridge mode connection failed: {e}. Falling back to isolated.")
            self.mode = EngineMode.ISOLATED
            await self._start_isolated()

    async def stop(self):
        """Gracefully shut down the engine."""
        await self._save_session()

        if self._browser_use_session:
            try:
                await self._browser_use_session.close()
            except Exception:
                pass
            self._browser_use_session = None

        if self.mode == EngineMode.ISOLATED:
            # Only close browser we launched (not the user's Chrome)
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        else:
            # Bridge mode: disconnect without closing user's browser
            if self.browser:
                self.browser = None
            self.context = None

        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

        logger.info("Antigravity Engine stopped.")

    # ------------------------------------------------------------------
    # Core Operations
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> dict:
        """Navigate to a URL and capture a visual audit screenshot."""
        if not self.context:
            await self.start()

        page = await self.context.new_page()
        result = {}

        try:
            logger.info(f"Navigating to: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)

            title = await page.title()
            receipt_path = await self._capture_evidence(page, f"nav_{title}")
            await self._save_session()

            result = {
                "status": "success",
                "title": title,
                "url": page.url,
                "receipt": receipt_path,
            }

        except Exception as e:
            logger.error(f"Navigation Error: {e}")
            await self._capture_evidence(page, "error_nav")
            result = {"status": "error", "error": str(e)}
        finally:
            await page.close()

        return result

    async def run_agent_task(self, task: str, model_name: str = None) -> dict:
        """
        Execute a natural-language browser task using Browser Use agent.

        This is the AI-driven mode where you describe what you want
        ("go to Salesforce and create a new lead") and the agent executes it.

        Args:
            task: Natural language description of the browser task
            model_name: LLM model override (defaults to active LANCELOT_PROVIDER)

        Returns:
            dict with status, result text, and screenshots
        """
        if not self.context:
            await self.start()

        try:
            from browser_use import Agent
            from browser_use.browser.session import BrowserSession
            from browser_use.browser import BrowserProfile

            # Build the LLM client
            llm = self._get_agent_llm(model_name)

            # Create a BrowserSession wrapping our existing Playwright context
            if self.mode == EngineMode.BRIDGE and self.cdp_url:
                browser_session = BrowserSession(
                    cdp_url=self.cdp_url,
                    browser_profile=BrowserProfile(
                        viewport_size={"width": 1280, "height": 800},
                    ),
                )
            else:
                browser_session = BrowserSession(
                    browser_profile=BrowserProfile(
                        headless=self.headless,
                        viewport_size={"width": 1280, "height": 800},
                    ),
                )

            agent = Agent(
                task=task,
                llm=llm,
                browser_session=browser_session,
            )

            logger.info(f"Agent task: {task[:80]}...")
            result = await agent.run()

            # Capture evidence
            if self.context and self.context.pages:
                page = self.context.pages[-1]
                await self._capture_evidence(page, f"agent_{task[:30]}")

            return {
                "status": "success",
                "task": task,
                "result": str(result),
            }

        except ImportError:
            logger.error("browser-use not installed. Cannot run agent tasks.")
            return {
                "status": "error",
                "error": "browser-use package not installed",
            }
        except Exception as e:
            logger.error(f"Agent task error: {e}")
            return {
                "status": "error",
                "task": task,
                "error": str(e),
            }

    def _get_agent_llm(self, model_name: str = None):
        """Build a LangChain LLM instance for Browser Use agent.

        Respects the LANCELOT_PROVIDER env var to select the active provider.
        Falls back to whichever provider has an API key available.
        """
        provider = os.getenv("LANCELOT_PROVIDER", "").lower()

        # Provider configs: (env_var, default_model, builder)
        provider_builders = {
            "gemini": self._build_gemini_llm,
            "openai": self._build_openai_llm,
            "anthropic": self._build_anthropic_llm,
            "xai": self._build_xai_llm,
        }

        # Try the configured provider first
        if provider in provider_builders:
            llm = provider_builders[provider](model_name)
            if llm:
                return llm

        # Fallback: try each provider that has an API key
        for name, builder in provider_builders.items():
            if name == provider:
                continue  # Already tried
            llm = builder(model_name)
            if llm:
                logger.info(f"Antigravity: Using fallback provider '{name}' for agent LLM")
                return llm

        raise RuntimeError(
            "No LLM available for Browser Use agent. "
            "Set LANCELOT_PROVIDER and provide an API key "
            "(GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or XAI_API_KEY)."
        )

    def _build_gemini_llm(self, model_name: str = None):
        """Build a Gemini LangChain LLM if API key is available."""
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            return None
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=model_name or "gemini-2.0-flash",
                google_api_key=api_key,
            )
        except ImportError:
            logger.warning("langchain-google-genai not installed")
            return None

    def _build_openai_llm(self, model_name: str = None):
        """Build an OpenAI LangChain LLM if API key is available."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name or "gpt-4o",
                api_key=api_key,
            )
        except ImportError:
            logger.warning("langchain-openai not installed")
            return None

    def _build_anthropic_llm(self, model_name: str = None):
        """Build an Anthropic LangChain LLM if API key is available."""
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        try:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=model_name or "claude-sonnet-4-5-20250929",
                api_key=api_key,
            )
        except ImportError:
            logger.warning("langchain-anthropic not installed")
            return None

    def _build_xai_llm(self, model_name: str = None):
        """Build an xAI (Grok) LangChain LLM via OpenAI-compatible API."""
        api_key = os.getenv("XAI_API_KEY", "")
        if not api_key:
            return None
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name or "grok-3",
                api_key=api_key,
                base_url="https://api.x.ai/v1",
            )
        except ImportError:
            logger.warning("langchain-openai not installed (needed for xAI)")
            return None

    # ------------------------------------------------------------------
    # Bridge Mode Helpers
    # ------------------------------------------------------------------

    def get_open_tabs(self) -> list:
        """List all open tabs/pages in the connected browser (bridge mode)."""
        if not self.context:
            return []
        return [
            {"url": p.url, "title": ""}  # title requires async
            for p in self.context.pages
        ]

    async def get_open_tabs_async(self) -> list:
        """List all open tabs with titles (async version)."""
        if not self.context:
            return []
        tabs = []
        for p in self.context.pages:
            try:
                title = await p.title()
            except Exception:
                title = "(unknown)"
            tabs.append({"url": p.url, "title": title})
        return tabs

    async def interact_with_tab(self, url_fragment: str) -> Optional[Page]:
        """Find and return a page matching the URL fragment."""
        if not self.context:
            return None
        for page in self.context.pages:
            if url_fragment.lower() in page.url.lower():
                return page
        return None

    # ------------------------------------------------------------------
    # Visual Audit & Session
    # ------------------------------------------------------------------

    async def _capture_evidence(self, page: Page, action_tag: str) -> Optional[str]:
        """Capture a proof-of-work screenshot."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_tag = "".join(c for c in action_tag if c.isalnum() or c in "_")[:50]
        filename = f"{timestamp}_{safe_tag}.png"
        path = os.path.join(self.evidence_dir, filename)

        try:
            await page.screenshot(path=path)
            self.audit_logger.log_event(
                "VISUAL_AUDIT",
                f"Screenshot captured: {filename}",
                user="Antigravity",
            )
            return path
        except Exception as e:
            logger.error(f"Visual Audit Failed: {e}")
            return None

    async def _save_session(self):
        """Export session cookies to disk (isolated mode only)."""
        if self.mode == EngineMode.ISOLATED and self.context:
            try:
                path = os.path.join(self.session_dir, "storage_state.json")
                await self.context.storage_state(path=path)
                logger.debug("Session Bridge: State saved.")
            except Exception as e:
                logger.debug(f"Session save skipped: {e}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return engine status summary."""
        return {
            "mode": self.mode.value,
            "running": self.playwright is not None,
            "cdp_url": self.cdp_url if self.mode == EngineMode.BRIDGE else None,
            "pages": len(self.context.pages) if self.context else 0,
            "headless": self.headless,
        }


# Quick Test
if __name__ == "__main__":
    async def test():
        engine = AntigravityEngine(headless=True, mode="isolated")
        try:
            await engine.start()
            res = await engine.navigate("https://example.com")
            print(res)
        finally:
            await engine.stop()

    asyncio.run(test())
