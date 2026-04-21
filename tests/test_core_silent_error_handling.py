import importlib
import logging
from pathlib import Path
from unittest.mock import MagicMock

from src.core.google_oauth_manager import GoogleOAuthManager
from src.core.oauth_token_manager import OAuthTokenManager
from src.core.openai_codex_oauth_manager import OpenAICodexOAuthManager
from src.core.security import AuditLogger, CognitionGovernor, Sentry


class _VaultRaises:
    def __init__(self, exists_exception=None, retrieve_exception=None, delete_exception=None):
        self._exists_exception = exists_exception
        self._retrieve_exception = retrieve_exception
        self._delete_exception = delete_exception

    def exists(self, key):
        if self._exists_exception:
            raise self._exists_exception
        return True

    def retrieve(self, key, accessor_id=""):
        if self._retrieve_exception:
            raise self._retrieve_exception
        return "token"

    def delete(self, key):
        if self._delete_exception:
            raise self._delete_exception

    def store(self, key, value, type="config"):
        return None


def test_google_oauth_revoke_logs_token_retrieval_failure(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    manager = GoogleOAuthManager(
        vault=_VaultRaises(retrieve_exception=RuntimeError("vault read failed")),
        port=8000,
    )

    with caplog.at_level(logging.WARNING):
        manager.revoke()

    assert "Failed to retrieve Google OAuth access token for revocation" in caplog.text


def test_anthropic_oauth_revoke_logs_delete_failure(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(tmp_path / "anthropic-oauth.json"))
    manager = OAuthTokenManager(
        vault=_VaultRaises(delete_exception=RuntimeError("delete failed")),
        port=8000,
    )

    with caplog.at_level(logging.WARNING):
        manager.revoke()

    assert "Failed to delete Anthropic OAuth vault key" in caplog.text


def test_codex_oauth_status_logs_account_lookup_failure(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    vault = _VaultRaises(retrieve_exception=RuntimeError("account lookup failed"))
    manager = OpenAICodexOAuthManager(vault=vault, port=1455)
    manager._get_expiry = MagicMock(return_value=9999999999.0)

    with caplog.at_level(logging.WARNING):
        status = manager.get_token_status()

    assert status["configured"] is True
    assert "Failed to read Codex OAuth account ID from vault" in caplog.text


def test_codex_account_extract_logs_debug_for_malformed_token(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    manager = OpenAICodexOAuthManager(vault=_VaultRaises(), port=1455)

    with caplog.at_level(logging.DEBUG):
        account_id = manager._extract_account_id("header.bad-payload.signature")

    assert account_id == ""
    assert "Failed to extract Codex OAuth account ID from token" in caplog.text


def test_audit_logger_recovery_logs_warning_on_corrupt_log(caplog, tmp_path):
    log_path = tmp_path / "audit.log"
    log_path.write_text("x", encoding="utf-8")

    original_open = open

    def _broken_open(path, mode="r", *args, **kwargs):
        if Path(path) == log_path and "rb" in mode:
            raise RuntimeError("open failed")
        return original_open(path, mode, *args, **kwargs)

    with caplog.at_level(logging.WARNING):
        import builtins

        old_open = builtins.open
        builtins.open = _broken_open
        try:
            logger = AuditLogger(str(log_path))
        finally:
            builtins.open = old_open

    assert logger._prev_hash == "0" * 64
    assert "Failed to recover audit log hash" in caplog.text


def test_cognition_governor_logs_warning_on_bad_usage_file(caplog, tmp_path):
    usage_file = tmp_path / "usage_stats.json"
    usage_file.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        governor = CognitionGovernor(str(tmp_path))

    assert governor.usage["date"]
    assert "Failed to load usage stats" in caplog.text


def test_sentry_logs_warning_on_bad_approval_file(caplog, tmp_path):
    approvals_file = tmp_path / "sentry_whitelist.json"
    approvals_file.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        sentry = Sentry(str(tmp_path))

    assert sentry.approvals == {}
    assert "Failed to load sentry approvals" in caplog.text


def test_sentry_logs_warning_when_save_fails(caplog, tmp_path):
    sentry = Sentry(str(tmp_path))

    original_open = open

    def _broken_open(path, mode="r", *args, **kwargs):
        if Path(path) == Path(sentry.approvals_file) and "w" in mode:
            raise RuntimeError("write failed")
        return original_open(path, mode, *args, **kwargs)

    import builtins

    with caplog.at_level(logging.WARNING):
        old_open = builtins.open
        builtins.open = _broken_open
        try:
            sentry.add_approval("cli_shell", {"command": "echo hi"})
        finally:
            builtins.open = old_open

    assert "Failed to save sentry approvals" in caplog.text


def test_network_interceptor_logs_private_address_block(caplog, monkeypatch):
    security_module = importlib.import_module("src.core.security")
    monkeypatch.setattr(security_module.NetworkInterceptor, "_RELOAD_INTERVAL_S", 0)
    interceptor = security_module.NetworkInterceptor()
    monkeypatch.setattr(interceptor, "_is_private_ip", lambda hostname: True)

    with caplog.at_level(logging.WARNING):
        allowed = interceptor.check_url("https://example.com/api")

    assert allowed is False
    assert "Blocked connection to private/internal address example.com" in caplog.text


def test_network_interceptor_logs_blocked_outbound_hostname(caplog, monkeypatch):
    security_module = importlib.import_module("src.core.security")
    monkeypatch.setattr(security_module.NetworkInterceptor, "_RELOAD_INTERVAL_S", 0)
    interceptor = security_module.NetworkInterceptor()
    monkeypatch.setattr(interceptor, "_is_private_ip", lambda hostname: False)
    monkeypatch.setattr(
        interceptor._allowlist,
        "is_hostname_allowed",
        lambda hostname, domains=None: False,
    )

    with caplog.at_level(logging.WARNING):
        allowed = interceptor.check_url("https://blocked.example/api")

    assert allowed is False
    assert "Blocked outbound connection to blocked.example" in caplog.text


def test_mcp_sentry_logs_bad_config_load(caplog, tmp_path):
    mcp_sentry_module = importlib.import_module("src.integrations.mcp_sentry")
    (tmp_path / "mcp_configs").mkdir()
    (tmp_path / "MEMORY_SUMMARY.md").write_text("", encoding="utf-8")
    (tmp_path / "mcp_configs" / "broken.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        sentry = mcp_sentry_module.MCPSentry(data_dir=str(tmp_path))

    assert sentry.tools == {}
    assert "Error loading MCP config broken.json" in caplog.text


def test_vault_logs_store_and_retrieve_failures(caplog, monkeypatch, tmp_path):
    vault_module = importlib.import_module("src.memory.vault")
    vault = vault_module.SecretVault(data_dir=str(tmp_path))

    class _BrokenFernet:
        def encrypt(self, *_args, **_kwargs):
            raise RuntimeError("encrypt exploded")

        def decrypt(self, *_args, **_kwargs):
            raise RuntimeError("decrypt exploded")

    vault.fernet = _BrokenFernet()
    monkeypatch.setattr(vault, "_load_secrets", lambda: {"token": "ciphertext"})

    with caplog.at_level(logging.WARNING):
        assert vault.store("token", "secret") is False
        assert vault.retrieve("token") is None

    assert "Vault store error: encrypt exploded" in caplog.text
    assert "Vault retrieve error: decrypt exploded" in caplog.text
