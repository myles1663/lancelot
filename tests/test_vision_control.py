"""
Tests for Antigravity Vision Control Provider
==============================================

Tests for vision-based UI control:
- Provider health checks
- Screen capture
- Element location
- Action execution
- State verification
- Explicit failure when unavailable

Prompt 9 — VisionControl
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.tools.providers.vision_antigravity import (
    AntigravityVisionProvider,
    VisionConfig,
    create_vision_provider,
    AntigravityUnavailableError,
    VisionOperationError,
)
from src.tools.contracts import (
    Capability,
    ProviderState,
    VisionResult,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def provider():
    """Create an AntigravityVisionProvider instance."""
    return AntigravityVisionProvider()


@pytest.fixture
def provider_enabled():
    """Create an enabled provider with mocked availability."""
    config = VisionConfig(enabled=True)
    p = AntigravityVisionProvider(config=config)
    p._antigravity_available = True
    return p


@pytest.fixture
def provider_disabled():
    """Create a disabled provider."""
    config = VisionConfig(enabled=False)
    return AntigravityVisionProvider(config=config)


@pytest.fixture
def mock_engine():
    """Create a mock AntigravityEngine."""
    engine = MagicMock()
    engine.start = AsyncMock()
    engine.stop = AsyncMock()

    # Mock context
    context = MagicMock()
    engine.context = context

    # Mock page
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    page.close = AsyncMock()
    page.locator = MagicMock()
    page.get_by_text = MagicMock()
    page.url = "https://example.com"

    # Mock mouse and keyboard
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()

    context.new_page = AsyncMock(return_value=page)

    return engine


# =============================================================================
# Provider Identity Tests
# =============================================================================


class TestProviderIdentity:
    """Test provider identification."""

    def test_provider_id(self, provider):
        """Provider has correct ID."""
        assert provider.provider_id == "vision_antigravity"

    def test_capabilities(self, provider):
        """Provider declares VisionControl capability."""
        caps = provider.capabilities
        assert Capability.VISION_CONTROL in caps
        assert len(caps) == 1

    def test_supports_vision_control(self, provider):
        """supports() returns True for VisionControl."""
        assert provider.supports(Capability.VISION_CONTROL) is True
        assert provider.supports(Capability.SHELL_EXEC) is False


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Test health check functionality."""

    def test_health_with_feature_disabled(self, provider):
        """Health check with feature flag disabled."""
        with patch("src.tools.providers.vision_antigravity.FEATURE_TOOLS_ANTIGRAVITY", False):
            health = provider.health_check()

            assert health.provider_id == "vision_antigravity"
            assert health.state == ProviderState.OFFLINE
            assert "disabled" in health.error_message.lower()
            assert provider._antigravity_available is False

    def test_health_with_config_disabled(self, provider_disabled):
        """Health check with config disabled."""
        with patch("src.tools.providers.vision_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            health = provider_disabled.health_check()

            assert health.state == ProviderState.OFFLINE
            assert "disabled" in health.error_message.lower()

    def test_health_engine_available(self, provider):
        """Health check with engine available."""
        with patch("src.tools.providers.vision_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_engine", return_value=True):
                health = provider.health_check()

                assert health.state == ProviderState.HEALTHY
                assert provider._antigravity_available is True

    def test_health_engine_unavailable(self, provider):
        """Health check with engine unavailable."""
        with patch("src.tools.providers.vision_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_engine", return_value=False):
                health = provider.health_check()

                assert health.state == ProviderState.OFFLINE
                assert "AntigravityEngine not available" in health.error_message

    def test_health_metadata_includes_headless(self, provider):
        """Health metadata includes headless setting."""
        with patch("src.tools.providers.vision_antigravity.FEATURE_TOOLS_ANTIGRAVITY", True):
            with patch.object(provider, "_check_antigravity_engine", return_value=True):
                health = provider.health_check()

                assert "headless" in health.metadata


# =============================================================================
# Explicit Failure Tests (No Silent Downgrade)
# =============================================================================


class TestExplicitFailure:
    """Test that provider explicitly fails when unavailable."""

    def test_capture_screen_fails_when_unavailable(self, provider):
        """capture_screen raises error when unavailable."""
        provider._antigravity_available = False

        with pytest.raises(AntigravityUnavailableError) as exc_info:
            provider.capture_screen()

        assert "unavailable" in str(exc_info.value).lower()

    def test_locate_element_fails_when_unavailable(self, provider):
        """locate_element raises error when unavailable."""
        provider._antigravity_available = False

        with pytest.raises(AntigravityUnavailableError):
            provider.locate_element("#button")

    def test_perform_action_fails_when_unavailable(self, provider):
        """perform_action raises error when unavailable."""
        provider._antigravity_available = False

        with pytest.raises(AntigravityUnavailableError):
            provider.perform_action("click", {"x": 100, "y": 100})

    def test_verify_state_fails_when_unavailable(self, provider):
        """verify_state raises error when unavailable."""
        provider._antigravity_available = False

        with pytest.raises(AntigravityUnavailableError):
            provider.verify_state({"text": "expected"})

    def test_no_fallback_behavior(self, provider):
        """Provider has no fallback - always fails when unavailable."""
        provider._antigravity_available = False

        # All operations should raise, never succeed silently
        operations = [
            lambda: provider.capture_screen(),
            lambda: provider.locate_element("button"),
            lambda: provider.perform_action("click", {}),
            lambda: provider.verify_state({}),
        ]

        for op in operations:
            with pytest.raises(AntigravityUnavailableError):
                op()


# =============================================================================
# Vision Receipts Tests
# =============================================================================


class TestVisionReceipts:
    """Test vision receipt generation."""

    def test_capture_creates_receipt(self, provider_enabled, mock_engine):
        """Screen capture creates receipt."""
        with patch("asyncio.run", return_value=(b"screenshot", "hash123")):
            provider_enabled.capture_screen()

            receipts = provider_enabled.get_receipts()
            assert len(receipts) == 1
            assert receipts[0]["action"] == "capture_screen"

    def test_receipts_include_screenshot_hash(self, provider_enabled):
        """Receipts include screenshot hash, not raw bytes."""
        with patch("asyncio.run", return_value=(b"screenshot_data", "abc123hash")):
            provider_enabled.capture_screen()

            receipts = provider_enabled.get_receipts()
            # Receipt has hash, not raw bytes
            assert receipts[0]["screenshot_after_hash"] == "abc123hash"

    def test_clear_receipts(self, provider_enabled):
        """Receipts can be cleared."""
        with patch("asyncio.run", return_value=(b"data", "hash")):
            provider_enabled.capture_screen()
            assert len(provider_enabled.get_receipts()) == 1

            provider_enabled.clear_receipts()
            assert len(provider_enabled.get_receipts()) == 0

    def test_receipts_disabled(self):
        """Receipts can be disabled in config."""
        config = VisionConfig(emit_vision_receipts=False)
        provider = AntigravityVisionProvider(config=config)
        provider._antigravity_available = True

        with patch("asyncio.run", return_value=(b"data", "hash")):
            provider.capture_screen()
            assert len(provider.get_receipts()) == 0


# =============================================================================
# Action Tests
# =============================================================================


class TestActions:
    """Test action execution."""

    def test_click_action_type(self, provider_enabled):
        """Click action is recognized."""
        mock_result = VisionResult(success=True, action_performed="click")
        with patch("asyncio.run", return_value=mock_result):
            result = provider_enabled.perform_action(
                "click",
                {"x": 100, "y": 200}
            )
            assert result.success is True

    def test_type_action_type(self, provider_enabled):
        """Type action is recognized."""
        mock_result = VisionResult(success=True, action_performed="type")
        with patch("asyncio.run", return_value=mock_result):
            result = provider_enabled.perform_action(
                "type",
                {"x": 100, "y": 200},
                value="hello"
            )
            assert result.success is True

    def test_scroll_action_type(self, provider_enabled):
        """Scroll action is recognized."""
        mock_result = VisionResult(success=True, action_performed="scroll")
        with patch("asyncio.run", return_value=mock_result):
            result = provider_enabled.perform_action(
                "scroll",
                {"delta": 100}
            )
            assert result.success is True


# =============================================================================
# Element Location Tests
# =============================================================================


class TestElementLocation:
    """Test element location."""

    def test_locate_by_css_selector(self, provider_enabled):
        """Element location by CSS selector."""
        mock_elements = [
            {"selector": "#button", "x": 10, "y": 20, "confidence": 1.0}
        ]
        with patch("asyncio.run", return_value=mock_elements):
            elements = provider_enabled.locate_element("#button")

            assert len(elements) == 1
            assert elements[0]["confidence"] == 1.0

    def test_locate_by_description(self, provider_enabled):
        """Element location by natural language."""
        mock_elements = [
            {"description": "Submit button", "x": 100, "y": 200, "confidence": 0.8}
        ]
        with patch("asyncio.run", return_value=mock_elements):
            elements = provider_enabled.locate_element("Submit button")

            assert len(elements) == 1
            assert elements[0]["confidence"] == 0.8


# =============================================================================
# State Verification Tests
# =============================================================================


class TestStateVerification:
    """Test state verification."""

    def test_verify_state_success(self, provider_enabled):
        """State verification succeeds."""
        mock_result = VisionResult(success=True, confidence=1.0)
        with patch("asyncio.run", return_value=mock_result):
            result = provider_enabled.verify_state({"text": "Welcome"})
            assert result.success is True

    def test_verify_state_failure(self, provider_enabled):
        """State verification fails gracefully."""
        mock_result = VisionResult(success=False, confidence=0.0)
        with patch("asyncio.run", return_value=mock_result):
            result = provider_enabled.verify_state({"text": "Not found"})
            assert result.success is False


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfiguration:
    """Test provider configuration."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = VisionConfig()

        assert config.enabled is True
        assert config.headless is True
        assert config.emit_vision_receipts is True

    def test_custom_config(self):
        """Custom config values are respected."""
        config = VisionConfig(
            enabled=False,
            headless=False,
            emit_vision_receipts=False,
        )

        assert config.enabled is False
        assert config.headless is False
        assert config.emit_vision_receipts is False


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Test create_vision_provider factory."""

    def test_create_with_defaults(self):
        """Create provider with default settings."""
        provider = create_vision_provider()

        assert provider.provider_id == "vision_antigravity"
        assert provider.config.enabled is True
        assert provider.config.headless is True

    def test_create_disabled(self):
        """Create disabled provider."""
        provider = create_vision_provider(enabled=False)

        assert provider.config.enabled is False

    def test_create_with_display(self):
        """Create provider with display (not headless)."""
        provider = create_vision_provider(headless=False)

        assert provider.config.headless is False

    def test_create_no_receipts(self):
        """Create provider without receipts."""
        provider = create_vision_provider(emit_receipts=False)

        assert provider.config.emit_vision_receipts is False


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling."""

    def test_antigravity_unavailable_error_message(self):
        """AntigravityUnavailableError has correct message."""
        error = AntigravityUnavailableError("test message")

        assert "test message" in str(error)

    def test_vision_operation_error_includes_operation(self):
        """VisionOperationError includes operation name."""
        error = VisionOperationError("capture_screen", "failed")

        assert "capture_screen" in str(error)
        assert "failed" in str(error)

    def test_ensure_available_checks_health(self, provider):
        """_ensure_available triggers health check if needed."""
        provider._antigravity_available = None

        with patch.object(provider, "health_check") as mock_health:
            mock_health.return_value = MagicMock(state=ProviderState.OFFLINE)

            with pytest.raises(AntigravityUnavailableError):
                provider._ensure_available()

            mock_health.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for vision control workflows."""

    def test_full_workflow_unavailable(self, provider):
        """Full workflow fails explicitly when unavailable."""
        provider._antigravity_available = False

        # Every step should fail with clear error
        with pytest.raises(AntigravityUnavailableError):
            provider.capture_screen()

        with pytest.raises(AntigravityUnavailableError):
            provider.locate_element("#submit")

        with pytest.raises(AntigravityUnavailableError):
            provider.perform_action("click", {"x": 100, "y": 100})

        with pytest.raises(AntigravityUnavailableError):
            provider.verify_state({"text": "Success"})

    def test_receipt_captures_action_details(self, provider_enabled):
        """Receipt captures full action details."""
        mock_result = VisionResult(
            success=True,
            action_performed="click",
            confidence=1.0,
        )
        with patch("asyncio.run", return_value=mock_result):
            target = {"x": 100, "y": 200, "center_x": 150, "center_y": 250}
            provider_enabled.perform_action("click", target)

            receipts = provider_enabled.get_receipts()
            assert len(receipts) == 1
            assert receipts[0]["action"] == "perform_action:click"
            assert receipts[0]["target_element"] == target
            assert receipts[0]["action_performed"] == "click"


class _FakeElement:
    def __init__(self, box):
        self._box = box

    async def bounding_box(self):
        return self._box


class _FakeLocator:
    def __init__(self, boxes):
        self.boxes = boxes

    async def count(self):
        return len(self.boxes)

    def nth(self, index):
        return _FakeElement(self.boxes[index])


class _FakeMouse:
    def __init__(self):
        self.calls = []

    async def click(self, x, y):
        self.calls.append(("click", x, y))

    async def move(self, x, y):
        self.calls.append(("move", x, y))

    async def down(self):
        self.calls.append(("down",))

    async def up(self):
        self.calls.append(("up",))

    async def wheel(self, x, y):
        self.calls.append(("wheel", x, y))


class _FakeKeyboard:
    def __init__(self):
        self.typed = []

    async def type(self, value):
        self.typed.append(value)


class _FakePage:
    def __init__(self, selector_boxes=None, text_boxes=None, url="https://example.com/app"):
        self.selector_boxes = selector_boxes or []
        self.text_boxes = text_boxes or []
        self.url = url
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.closed = False

    async def screenshot(self, **_kwargs):
        return b"screen"

    async def close(self):
        self.closed = True

    def locator(self, selector):
        return _FakeLocator(self.selector_boxes)

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self.text_boxes)


class _FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class _FakeEngine:
    def __init__(self, page):
        self.context = _FakeContext(page)
        self.stopped = False

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True


class TestAsyncVisionInternals:
    @pytest.mark.asyncio
    async def test_async_capture_screen_hashes_and_closes_page(self, provider_enabled):
        page = _FakePage()
        provider_enabled._get_engine = AsyncMock(return_value=_FakeEngine(page))

        screenshot, digest = await provider_enabled._async_capture_screen()

        assert screenshot == b"screen"
        assert len(digest) == 64
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_async_locate_element_css_and_text_paths(self, provider_enabled):
        page = _FakePage(
            selector_boxes=[{"x": 10, "y": 20, "width": 30, "height": 40}],
            text_boxes=[{"x": 1, "y": 2, "width": 3, "height": 4}],
        )
        provider_enabled._get_engine = AsyncMock(return_value=_FakeEngine(page))

        css = await provider_enabled._async_locate_element("#submit", None)
        text = await provider_enabled._async_locate_element("Submit", None)

        assert css[0]["type"] == "css_selector"
        assert css[0]["center_x"] == 25
        assert text[0]["type"] == "text_match"
        assert text[0]["confidence"] == 0.8
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_async_perform_action_supports_click_type_drag_scroll_and_unknown(self, provider_enabled):
        page = _FakePage()
        provider_enabled._get_engine = AsyncMock(return_value=_FakeEngine(page))
        target = {"x": 10, "y": 20, "center_x": 15, "center_y": 25, "end_x": 40, "end_y": 50, "delta": 300}

        click = await provider_enabled._async_perform_action("click", target, None)
        typed = await provider_enabled._async_perform_action("type", target, "hello")
        drag = await provider_enabled._async_perform_action("drag", target, None)
        scroll = await provider_enabled._async_perform_action("scroll", target, None)
        unknown = await provider_enabled._async_perform_action("hover", target, None)

        assert click.success is True
        assert typed.success is True
        assert drag.success is True
        assert scroll.success is True
        assert unknown.success is False
        assert page.keyboard.typed == ["hello"]
        assert ("wheel", 0, 300) in page.mouse.calls

    @pytest.mark.asyncio
    async def test_async_verify_state_checks_text_selector_and_url(self, provider_enabled):
        page = _FakePage(
            selector_boxes=[{"x": 1, "y": 2, "width": 3, "height": 4}],
            text_boxes=[{"x": 5, "y": 6, "width": 7, "height": 8}],
            url="https://example.com/success",
        )
        provider_enabled._get_engine = AsyncMock(return_value=_FakeEngine(page))

        ok = await provider_enabled._async_verify_state(
            {"text": "Welcome", "selector": "#ok", "url": "/success"},
            None,
        )
        bad = await provider_enabled._async_verify_state(
            {"text": "Welcome", "selector": "#ok", "url": "/missing"},
            None,
        )

        assert ok.success is True
        assert len(ok.elements_detected) == 2
        assert bad.success is False
        assert bad.confidence == 0.0

    def test_public_methods_record_failures_as_receipts(self, provider_enabled):
        def fail_run(coroutine):
            coroutine.close()
            raise RuntimeError("vision failed")

        provider_enabled._run_sync = MagicMock(side_effect=fail_run)

        with pytest.raises(VisionOperationError):
            provider_enabled.capture_screen()
        with pytest.raises(VisionOperationError):
            provider_enabled.locate_element("#submit", screenshot=b"before")

        action = provider_enabled.perform_action("click", {"x": 1, "y": 2})
        state = provider_enabled.verify_state({"text": "done"}, screenshot=b"before")

        receipts = provider_enabled.get_receipts()
        assert action.success is False
        assert state.success is False
        assert len(receipts) == 4
        assert all(receipt["success"] is False for receipt in receipts)

    @pytest.mark.asyncio
    async def test_cleanup_stops_engine_and_clears_reference(self, provider_enabled):
        engine = _FakeEngine(_FakePage())
        provider_enabled._engine = engine

        await provider_enabled.cleanup()

        assert engine.stopped is True
        assert provider_enabled._engine is None

