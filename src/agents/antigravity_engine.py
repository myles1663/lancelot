"""
Antigravity Engine - Production Browser Core
--------------------------------------------
Orchestrates a persistent, audit-logged headless browser session using Playwright.

Features:
- **Session Bridge**: Persists cookies/localStorage to `storage_state.json` to maintain logins.
- **Visual Audit**: Captures screenshots of every significant action.
- **Stealth**: Runs in headless mode with sandboxing configurations.
"""

import os
import asyncio
import logging
from datetime import datetime
from playwright.async_api import async_playwright, Page
from security import AuditLogger

# Configure Logging
logger = logging.getLogger("lancelot.antigravity")

class AntigravityEngine:
    def __init__(self, data_dir="/home/lancelot/data", headless=True):
        self.data_dir = data_dir
        self.session_dir = os.path.join(data_dir, "chrome_session")
        self.evidence_dir = os.path.join(data_dir, "artifacts", "browser_proof")
        self.headless = headless
        self.audit_logger = AuditLogger()
        self.playwright = None
        self.browser = None
        self.context = None
        
        # Ensure critical directories exist
        os.makedirs(self.session_dir, exist_ok=True)
        os.makedirs(self.evidence_dir, exist_ok=True)

    async def start(self):
        """Initializes the browser engine and loads the Session Bridge."""
        if self.playwright:
            return  # Already started

        logger.info("Initializing Antigravity Engine...")
        self.playwright = await async_playwright().start()
        
        # Launch Chromium with production flags
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox', 
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage'
            ]
        )
        
        # Session Bridge: Import 'Chrome Profile' cookies
        storage_state_path = os.path.join(self.session_dir, "storage_state.json")
        if os.path.exists(storage_state_path):
            try:
                self.context = await self.browser.new_context(
                    storage_state=storage_state_path,
                    viewport={"width": 1280, "height": 800}
                )
                logger.info(f"Session Bridge: Restored context from {storage_state_path}")
            except Exception as e:
                logger.warning(f"Session Bridge: Failed to load state ({e}). Starting fresh.")
                self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800})
        else:
            self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800})
            logger.info("Session Bridge: Initialized new context.")

    async def _capture_evidence(self, page: Page, action_tag: str):
        """Visual Audit: Captures a proof-of-work screenshot."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_tag = "".join(c for c in action_tag if c.isalnum() or c in "_")[:50]
        filename = f"{timestamp}_{safe_tag}.png"
        path = os.path.join(self.evidence_dir, filename)
        
        try:
            await page.screenshot(path=path)
            self.audit_logger.log_event(
                "VISUAL_AUDIT",
                f"Screenshot captured: {filename}",
                user="Antigravity"
            )
            return path
        except Exception as e:
            logger.error(f"Visual Audit Failed: {e}")
            return None

    async def _save_session(self):
        """Session Bridge: Exports 'Chrome Profile' cookies to disk."""
        if self.context:
            path = os.path.join(self.session_dir, "storage_state.json")
            await self.context.storage_state(path=path)
            logger.debug("Session Bridge: State saved.")

    async def navigate(self, url: str) -> dict:
        """Navigates to a URL and performs a Visual Audit."""
        if not self.context:
            await self.start()
            
        page = await self.context.new_page()
        result = {}
        
        try:
            logger.info(f"Navigating to: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            title = await page.title()
            
            # Capture receipt
            receipt_path = await self._capture_evidence(page, f"nav_{title}")
            
            # Persist session
            await self._save_session()
            
            result = {
                "status": "success",
                "title": title,
                "url": page.url,
                "receipt": receipt_path
            }
            
        except Exception as e:
            logger.error(f"Navigation Error: {e}")
            await self._capture_evidence(page, "error_nav")
            result = {
                "status": "error",
                "error": str(e)
            }
        finally:
            await page.close()
            
        return result

    async def stop(self):
        """Gracefully shuts down the engine."""
        await self._save_session()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Antigravity Engine stopped.")

# Quick Test
if __name__ == "__main__":
    async def test():
        engine = AntigravityEngine(headless=True)
        try:
            await engine.start()
            res = await engine.navigate("https://example.com")
            print(res)
        finally:
            await engine.stop()
    
    asyncio.run(test())
