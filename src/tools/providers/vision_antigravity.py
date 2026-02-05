"""
Antigravity Vision Control Provider
====================================

Vision-based UI control provider using Antigravity's browser automation.

This provider implements the VisionControl capability for:
- Screen capture and hashing
- Element location (by selector or natural language)
- UI action execution (click, type, drag, scroll)
- State verification

IMPORTANT: VisionControl requires Antigravity. This provider explicitly
fails when Antigravity is unavailable - there is NO silent downgrade
or fallback to alternative providers.

Prompt 9 — VisionControl
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.tools.contracts import (
    BaseProvider,
    Capability,
    ProviderHealth,
    ProviderState,
    VisionResult,
    VisionControlCapability,
)
from src.tools.receipts import (
    VisionReceipt,
    create_vision_receipt,
)
from src.core.feature_flags import FEATURE_TOOLS_ANTIGRAVITY

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class AntigravityUnavailableError(Exception):
    """Raised when Antigravity is required but unavailable."""

    def __init__(self, message: str = "Antigravity is unavailable"):
        self.message = message
        super().__init__(self.message)


class VisionOperationError(Exception):
    """Raised when a vision operation fails."""

    def __init__(self, operation: str, message: str):
        self.operation = operation
        self.message = message
        super().__init__(f"{operation} failed: {message}")


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class VisionConfig:
    """Configuration for AntigravityVisionProvider."""

    # Feature control
    enabled: bool = True

    # Antigravity settings
    antigravity_data_dir: str = "/home/lancelot/data"
    headless: bool = True

    # Timeouts
    navigation_timeout_s: int = 60
    action_timeout_s: int = 30
    screenshot_timeout_s: int = 10

    # Receipt settings
    emit_vision_receipts: bool = True
    store_screenshots: bool = False  # Only hash, don't persist

    # Confidence thresholds
    element_confidence_threshold: float = 0.7


# =============================================================================
# AntigravityVisionProvider
# =============================================================================


class AntigravityVisionProvider(BaseProvider):
    """
    Antigravity-powered vision control provider.

    Provides vision-based UI control using Playwright via AntigravityEngine.

    IMPORTANT: This provider REQUIRES Antigravity. It will explicitly fail
    with AntigravityUnavailableError when:
    - FEATURE_TOOLS_ANTIGRAVITY is disabled
    - AntigravityEngine is not available
    - Config has enabled=False

    There is NO fallback or silent downgrade.
    """

    def __init__(self, config: Optional[VisionConfig] = None):
        """
        Initialize the AntigravityVisionProvider.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or VisionConfig()
        self._last_health_check: Optional[str] = None
        self._antigravity_available: Optional[bool] = None
        self._engine = None

        # Vision receipts
        self._receipts: List[VisionReceipt] = []

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return "vision_antigravity"

    @property
    def capabilities(self) -> List[Capability]:
        """List of capabilities this provider implements."""
        return [Capability.VISION_CONTROL]

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """
        Check provider health.

        Verifies Antigravity feature is enabled and engine is available.
        """
        self._last_health_check = datetime.now(timezone.utc).isoformat()

        # Check feature flag
        if not FEATURE_TOOLS_ANTIGRAVITY:
            self._antigravity_available = False
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                error_message="FEATURE_TOOLS_ANTIGRAVITY is disabled",
                metadata={
                    "feature_enabled": False,
                    "requires_antigravity": True,
                },
            )

        # Check if config disables the provider
        if not self.config.enabled:
            self._antigravity_available = False
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                error_message="Provider is disabled in configuration",
                metadata={
                    "feature_enabled": True,
                    "config_enabled": False,
                },
            )

        # Check AntigravityEngine availability
        engine_available = self._check_antigravity_engine()
        self._antigravity_available = engine_available

        if engine_available:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                metadata={
                    "feature_enabled": True,
                    "engine_available": True,
                    "headless": self.config.headless,
                },
            )
        else:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                error_message="AntigravityEngine not available",
                metadata={
                    "feature_enabled": True,
                    "engine_available": False,
                },
            )

    def _check_antigravity_engine(self) -> bool:
        """Check if AntigravityEngine is available."""
        try:
            from src.agents.antigravity_engine import AntigravityEngine
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("AntigravityEngine check failed: %s", e)
            return False

    def _ensure_available(self) -> None:
        """Ensure Antigravity is available or raise error."""
        if self._antigravity_available is None:
            self.health_check()

        if not self._antigravity_available:
            raise AntigravityUnavailableError(
                "VisionControl requires Antigravity but it is unavailable. "
                "Enable FEATURE_TOOLS_ANTIGRAVITY and ensure AntigravityEngine is installed."
            )

    async def _get_engine(self):
        """Get or create AntigravityEngine instance."""
        if self._engine is None:
            from src.agents.antigravity_engine import AntigravityEngine
            self._engine = AntigravityEngine(
                data_dir=self.config.antigravity_data_dir,
                headless=self.config.headless,
            )
            await self._engine.start()
        return self._engine

    # =========================================================================
    # VisionControl Capability
    # =========================================================================

    def capture_screen(self) -> Tuple[bytes, str]:
        """
        Capture current screen state.

        Returns:
            Tuple of (screenshot_bytes, hash)

        Raises:
            AntigravityUnavailableError: If Antigravity is not available
        """
        self._ensure_available()

        start_time = time.time()
        receipt = None

        if self.config.emit_vision_receipts:
            receipt = create_vision_receipt(action="capture_screen")

        try:
            # Run async capture
            screenshot_bytes, screenshot_hash = asyncio.get_event_loop().run_until_complete(
                self._async_capture_screen()
            )

            if receipt:
                receipt.screenshot_after_hash = screenshot_hash
                receipt.success = True
                receipt.duration_ms = int((time.time() - start_time) * 1000)
                self._receipts.append(receipt)

            return screenshot_bytes, screenshot_hash

        except Exception as e:
            logger.exception("Screen capture failed")
            if receipt:
                receipt.fail(str(e))
                self._receipts.append(receipt)
            raise VisionOperationError("capture_screen", str(e))

    async def _async_capture_screen(self) -> Tuple[bytes, str]:
        """Async screen capture implementation."""
        engine = await self._get_engine()

        # Create a page for screenshot
        page = await engine.context.new_page()

        try:
            screenshot_bytes = await page.screenshot()
            screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()
            return screenshot_bytes, screenshot_hash
        finally:
            await page.close()

    def locate_element(
        self,
        selector_or_description: str,
        screenshot: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """
        Locate UI elements by selector or natural language description.

        Args:
            selector_or_description: CSS selector or natural language description
            screenshot: Optional screenshot to analyze (captures new if None)

        Returns:
            List of detected elements with coordinates and confidence

        Raises:
            AntigravityUnavailableError: If Antigravity is not available
        """
        self._ensure_available()

        start_time = time.time()
        receipt = None

        if self.config.emit_vision_receipts:
            receipt = create_vision_receipt(action="locate_element")
            if screenshot:
                receipt.screenshot_before_hash = hashlib.sha256(screenshot).hexdigest()

        try:
            elements = asyncio.get_event_loop().run_until_complete(
                self._async_locate_element(selector_or_description, screenshot)
            )

            if receipt:
                receipt.elements_detected = elements
                receipt.success = True
                receipt.duration_ms = int((time.time() - start_time) * 1000)
                receipt.confidence_score = max(
                    (e.get("confidence", 0) for e in elements),
                    default=0.0
                )
                self._receipts.append(receipt)

            return elements

        except Exception as e:
            logger.exception("Element location failed")
            if receipt:
                receipt.fail(str(e))
                self._receipts.append(receipt)
            raise VisionOperationError("locate_element", str(e))

    async def _async_locate_element(
        self,
        selector_or_description: str,
        screenshot: Optional[bytes],
    ) -> List[Dict[str, Any]]:
        """Async element location implementation."""
        engine = await self._get_engine()
        page = await engine.context.new_page()

        try:
            elements = []

            # Try as CSS selector first
            if selector_or_description.startswith(("#", ".", "[", "//", "xpath")):
                locator = page.locator(selector_or_description)
                count = await locator.count()

                for i in range(count):
                    element = locator.nth(i)
                    box = await element.bounding_box()
                    if box:
                        elements.append({
                            "selector": selector_or_description,
                            "index": i,
                            "x": box["x"],
                            "y": box["y"],
                            "width": box["width"],
                            "height": box["height"],
                            "center_x": box["x"] + box["width"] / 2,
                            "center_y": box["y"] + box["height"] / 2,
                            "confidence": 1.0,  # Exact CSS match
                            "type": "css_selector",
                        })
            else:
                # Natural language description - use text matching
                # In production, this would use AI vision
                locator = page.get_by_text(selector_or_description, exact=False)
                count = await locator.count()

                for i in range(min(count, 10)):  # Limit to 10 matches
                    element = locator.nth(i)
                    box = await element.bounding_box()
                    if box:
                        elements.append({
                            "description": selector_or_description,
                            "index": i,
                            "x": box["x"],
                            "y": box["y"],
                            "width": box["width"],
                            "height": box["height"],
                            "center_x": box["x"] + box["width"] / 2,
                            "center_y": box["y"] + box["height"] / 2,
                            "confidence": 0.8,  # Text match
                            "type": "text_match",
                        })

            return elements

        finally:
            await page.close()

    def perform_action(
        self,
        action: str,
        target: Dict[str, Any],
        value: Optional[str] = None,
    ) -> VisionResult:
        """
        Perform UI action.

        Args:
            action: Action type ("click", "type", "drag", "scroll")
            target: Target element with coordinates
            value: Optional value for type action

        Returns:
            VisionResult with action status

        Raises:
            AntigravityUnavailableError: If Antigravity is not available
        """
        self._ensure_available()

        start_time = time.time()
        receipt = None

        if self.config.emit_vision_receipts:
            receipt = create_vision_receipt(action=f"perform_action:{action}")
            receipt.target_element = target
            receipt.action_performed = action
            receipt.action_value = value

        try:
            result = asyncio.get_event_loop().run_until_complete(
                self._async_perform_action(action, target, value)
            )

            if receipt:
                receipt.with_vision_result(result)
                receipt.duration_ms = int((time.time() - start_time) * 1000)
                self._receipts.append(receipt)

            return result

        except Exception as e:
            logger.exception("Action execution failed")
            error_result = VisionResult(
                success=False,
                error_message=str(e),
            )
            if receipt:
                receipt.fail(str(e))
                self._receipts.append(receipt)
            return error_result

    async def _async_perform_action(
        self,
        action: str,
        target: Dict[str, Any],
        value: Optional[str],
    ) -> VisionResult:
        """Async action execution implementation."""
        engine = await self._get_engine()
        page = await engine.context.new_page()

        try:
            x = target.get("center_x", target.get("x", 0))
            y = target.get("center_y", target.get("y", 0))

            # Capture before screenshot
            before_screenshot = await page.screenshot()
            before_hash = hashlib.sha256(before_screenshot).hexdigest()

            if action == "click":
                await page.mouse.click(x, y)

            elif action == "type":
                if value:
                    # Click first to focus
                    await page.mouse.click(x, y)
                    await page.keyboard.type(value)

            elif action == "drag":
                end_x = target.get("end_x", x + 100)
                end_y = target.get("end_y", y)
                await page.mouse.move(x, y)
                await page.mouse.down()
                await page.mouse.move(end_x, end_y)
                await page.mouse.up()

            elif action == "scroll":
                delta = target.get("delta", 100)
                await page.mouse.wheel(0, delta)

            else:
                return VisionResult(
                    success=False,
                    error_message=f"Unknown action: {action}",
                )

            # Capture after screenshot
            after_screenshot = await page.screenshot()
            after_hash = hashlib.sha256(after_screenshot).hexdigest()

            return VisionResult(
                success=True,
                screenshot_hash=after_hash,
                action_performed=action,
                confidence=1.0,
            )

        finally:
            await page.close()

    def verify_state(
        self,
        expected: Dict[str, Any],
        screenshot: Optional[bytes] = None,
    ) -> VisionResult:
        """
        Verify UI matches expected state.

        Args:
            expected: Expected state definition
            screenshot: Optional screenshot to analyze

        Returns:
            VisionResult with verification status

        Raises:
            AntigravityUnavailableError: If Antigravity is not available
        """
        self._ensure_available()

        start_time = time.time()
        receipt = None

        if self.config.emit_vision_receipts:
            receipt = create_vision_receipt(action="verify_state")
            receipt.expected_state = expected
            if screenshot:
                receipt.screenshot_before_hash = hashlib.sha256(screenshot).hexdigest()

        try:
            result = asyncio.get_event_loop().run_until_complete(
                self._async_verify_state(expected, screenshot)
            )

            if receipt:
                receipt.with_vision_result(result)
                receipt.actual_state = result.__dict__.get("actual_state", {})
                receipt.state_matched = result.success
                receipt.duration_ms = int((time.time() - start_time) * 1000)
                self._receipts.append(receipt)

            return result

        except Exception as e:
            logger.exception("State verification failed")
            error_result = VisionResult(
                success=False,
                error_message=str(e),
            )
            if receipt:
                receipt.fail(str(e))
                self._receipts.append(receipt)
            return error_result

    async def _async_verify_state(
        self,
        expected: Dict[str, Any],
        screenshot: Optional[bytes],
    ) -> VisionResult:
        """Async state verification implementation."""
        engine = await self._get_engine()
        page = await engine.context.new_page()

        try:
            # Capture current screenshot
            current_screenshot = await page.screenshot()
            current_hash = hashlib.sha256(current_screenshot).hexdigest()

            # Check expected conditions
            verification_passed = True
            detected_elements = []

            # Check for expected text
            if "text" in expected:
                locator = page.get_by_text(expected["text"], exact=False)
                count = await locator.count()
                if count == 0:
                    verification_passed = False

            # Check for expected selector
            if "selector" in expected:
                locator = page.locator(expected["selector"])
                count = await locator.count()
                if count == 0:
                    verification_passed = False

            # Check for expected URL
            if "url" in expected:
                current_url = page.url
                if expected["url"] not in current_url:
                    verification_passed = False

            return VisionResult(
                success=verification_passed,
                screenshot_hash=current_hash,
                elements_detected=detected_elements,
                confidence=1.0 if verification_passed else 0.0,
            )

        finally:
            await page.close()

    # =========================================================================
    # Receipt Management
    # =========================================================================

    def get_receipts(self) -> List[Dict[str, Any]]:
        """Get all vision receipts."""
        return [r.to_dict() for r in self._receipts]

    def clear_receipts(self) -> None:
        """Clear stored receipts."""
        self._receipts = []

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self._engine:
            await self._engine.stop()
            self._engine = None

    def __del__(self):
        """Destructor to cleanup engine."""
        if self._engine:
            try:
                asyncio.get_event_loop().run_until_complete(self.cleanup())
            except Exception:
                pass


# =============================================================================
# Factory Function
# =============================================================================


def create_vision_provider(
    enabled: bool = True,
    headless: bool = True,
    emit_receipts: bool = True,
) -> AntigravityVisionProvider:
    """
    Factory function for creating AntigravityVisionProvider.

    Args:
        enabled: Whether vision control is enabled
        headless: Whether to run browser in headless mode
        emit_receipts: Whether to emit vision receipts

    Returns:
        Configured AntigravityVisionProvider
    """
    config = VisionConfig(
        enabled=enabled,
        headless=headless,
        emit_vision_receipts=emit_receipts,
    )
    return AntigravityVisionProvider(config=config)
