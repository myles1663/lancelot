import importlib
from unittest.mock import patch

import pytest

from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.outbound_http import OutboundNetworkError
from src.core.provider_profile import LaneConfig, ProviderProfile
from src.core.update_checker import UpdateChecker


def _blocked(*args, **kwargs):
    raise OutboundNetworkError("blocked by network allowlist")


def _gemini_profile() -> ProviderProfile:
    return ProviderProfile(
        name="gemini",
        display_name="Gemini",
        fast=LaneConfig(model="gemini-fast", max_tokens=256, temperature=0.2),
        deep=LaneConfig(model="gemini-deep", max_tokens=1024, temperature=0.1),
    )


def test_flagship_client_fails_closed_when_allowlist_blocks(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("src.core.flagship_client.assert_url_allowed", _blocked)
    client = FlagshipClient("gemini", _gemini_profile())

    with patch("src.core.flagship_client.urllib.request.urlopen") as mock_open:
        with pytest.raises(FlagshipError, match="network allowlist"):
            client.complete("hello", lane="fast")

    mock_open.assert_not_called()


def test_update_checker_fails_closed_when_allowlist_blocks(monkeypatch):
    monkeypatch.setattr("src.core.update_checker.assert_url_allowed", _blocked)
    checker = UpdateChecker()

    with patch("urllib.request.urlopen") as mock_open:
        status = checker.force_check()

    mock_open.assert_not_called()
    assert "network allowlist" in (status["check_error"] or "")
    assert status["check_state"] == "failed"
    assert status["check_error_kind"] == "blocked_by_policy"


def test_core_allowlist_domain_bypasses_private_ip_guard(monkeypatch):
    security_module = importlib.import_module("src.core.security")
    monkeypatch.setattr(security_module.NetworkInterceptor, "_RELOAD_INTERVAL_S", 0)
    interceptor = security_module.NetworkInterceptor()
    monkeypatch.setattr(interceptor, "_is_private_ip", lambda hostname: True)

    assert interceptor.check_url("https://api.projectlancelot.dev/v1/version") is True
