"""
Tests for ExecutionToken schema, store, and minter (Fix Pack V1 PR3).
"""

import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.execution_authority.schema import (
    AuthResult,
    ExecutionToken,
    NetworkPolicy,
    SecretPolicy,
    TaskType,
    TokenStatus,
)
from src.core.execution_authority.store import ExecutionTokenStore
from src.core.execution_authority.minter import PermissionMinter


# =========================================================================
# ExecutionToken Schema Tests
# =========================================================================


class TestExecutionTokenSchema:
    def test_default_creation(self):
        token = ExecutionToken()
        assert token.id
        assert token.status == TokenStatus.ACTIVE.value
        assert token.actions_used == 0
        assert token.expires_at is not None  # auto-set from max_duration_sec

    def test_expires_at_auto_calculated(self):
        token = ExecutionToken(max_duration_sec=60)
        assert token.expires_at is not None

    def test_is_expired_by_status(self):
        token = ExecutionToken(status=TokenStatus.REVOKED.value)
        assert token.is_expired() is True

    def test_is_expired_by_actions(self):
        token = ExecutionToken(max_actions=5, actions_used=5)
        assert token.is_expired() is True

    def test_not_expired_when_active(self):
        token = ExecutionToken(max_actions=50, actions_used=0, max_duration_sec=3600)
        assert token.is_expired() is False

    def test_allows_tool_empty_means_all(self):
        token = ExecutionToken(allowed_tools=[])
        assert token.allows_tool("any_tool") is True

    def test_allows_tool_specific(self):
        token = ExecutionToken(allowed_tools=["read_file", "write_file"])
        assert token.allows_tool("read_file") is True
        assert token.allows_tool("execute_command") is False

    def test_allows_skill_empty_means_all(self):
        token = ExecutionToken(allowed_skills=[])
        assert token.allows_skill("any_skill") is True

    def test_allows_skill_specific(self):
        token = ExecutionToken(allowed_skills=["repo_writer"])
        assert token.allows_skill("repo_writer") is True
        assert token.allows_skill("command_runner") is False

    def test_allows_path_empty_means_all(self):
        token = ExecutionToken(allowed_paths=[])
        assert token.allows_path("/any/path") is True

    def test_allows_path_glob(self):
        token = ExecutionToken(allowed_paths=["src/*.py", "tests/*"])
        assert token.allows_path("src/main.py") is True
        assert token.allows_path("tests/test_foo.py") is True
        assert token.allows_path("config/settings.yaml") is False

    def test_allows_network_off(self):
        token = ExecutionToken(network_policy=NetworkPolicy.OFF.value)
        assert token.allows_network("google.com") is False

    def test_allows_network_full(self):
        token = ExecutionToken(network_policy=NetworkPolicy.FULL.value)
        assert token.allows_network("anything.com") is True

    def test_allows_network_allowlist(self):
        token = ExecutionToken(
            network_policy=NetworkPolicy.ALLOWLIST.value,
            network_allowlist=["api.example.com"],
        )
        assert token.allows_network("api.example.com") is True
        assert token.allows_network("evil.com") is False

    def test_to_dict_roundtrip(self):
        token = ExecutionToken(
            scope="Test scope",
            task_type=TaskType.CODE_CHANGE.value,
            allowed_tools=["read_file"],
            risk_tier="MED",
        )
        d = token.to_dict()
        token2 = ExecutionToken.from_dict(d)
        assert token2.scope == token.scope
        assert token2.task_type == token.task_type
        assert token2.allowed_tools == token.allowed_tools
        assert token2.risk_tier == token.risk_tier


# =========================================================================
# ExecutionTokenStore Tests
# =========================================================================


class TestExecutionTokenStore:
    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "tokens.db"
        return ExecutionTokenStore(db_path)

    def test_create_and_get(self, store):
        token = ExecutionToken(scope="test scope")
        store.create(token)
        retrieved = store.get(token.id)
        assert retrieved is not None
        assert retrieved.scope == "test scope"

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent-id") is None

    def test_revoke(self, store):
        token = ExecutionToken(scope="to revoke")
        store.create(token)
        assert store.revoke(token.id, "test reason") is True
        retrieved = store.get(token.id)
        assert retrieved.status == TokenStatus.REVOKED.value

    def test_revoke_already_revoked(self, store):
        token = ExecutionToken(scope="already revoked", status=TokenStatus.REVOKED.value)
        store.create(token)
        assert store.revoke(token.id, "again") is False

    def test_increment_actions(self, store):
        token = ExecutionToken(scope="inc test", max_actions=3, actions_used=0)
        store.create(token)
        assert store.increment_actions(token.id) is True
        retrieved = store.get(token.id)
        assert retrieved.actions_used == 1

    def test_increment_actions_at_max(self, store):
        token = ExecutionToken(scope="max test", max_actions=1, actions_used=1)
        store.create(token)
        assert store.increment_actions(token.id) is False

    def test_expire_stale(self, store):
        from datetime import datetime, timezone, timedelta
        # Create a token that's already expired
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        token = ExecutionToken(
            scope="stale",
            expires_at=past,
            max_duration_sec=1,
        )
        store.create(token)
        count = store.expire_stale()
        assert count >= 1
        retrieved = store.get(token.id)
        assert retrieved.status == TokenStatus.EXPIRED.value

    def test_get_active_for_session(self, store):
        token1 = ExecutionToken(scope="active1", session_id="session-1")
        token2 = ExecutionToken(scope="active2", session_id="session-1")
        token3 = ExecutionToken(scope="other", session_id="session-2")
        store.create(token1)
        store.create(token2)
        store.create(token3)
        active = store.get_active_for_session("session-1")
        assert len(active) == 2

    def test_list_tokens(self, store):
        for i in range(5):
            store.create(ExecutionToken(scope=f"token-{i}"))
        tokens = store.list_tokens(limit=3)
        assert len(tokens) == 3

    def test_list_tokens_by_status(self, store):
        store.create(ExecutionToken(scope="active", status=TokenStatus.ACTIVE.value))
        store.create(ExecutionToken(scope="revoked", status=TokenStatus.REVOKED.value))
        active = store.list_tokens(status=TokenStatus.ACTIVE.value)
        assert len(active) == 1
        assert active[0].scope == "active"

    def test_whitespace_token_id_not_found(self, store):
        assert store.get("   ") is None

    def test_empty_token_id_not_found(self, store):
        assert store.get("") is None


# =========================================================================
# PermissionMinter Tests
# =========================================================================


class TestPermissionMinter:
    @pytest.fixture
    def minter(self, tmp_path):
        store = ExecutionTokenStore(tmp_path / "tokens.db")
        return PermissionMinter(store=store)

    def test_mint_from_approval(self, minter):
        token = minter.mint_from_approval(
            scope="Edit config files",
            task_type=TaskType.CODE_CHANGE.value,
            tools=["write_file"],
            paths=["src/*.py"],
            risk_tier="MED",
        )
        assert token.id
        assert token.scope == "Edit config files"
        assert token.status == TokenStatus.ACTIVE.value
        assert token.allowed_tools == ["write_file"]
        assert token.allowed_paths == ["src/*.py"]

    def test_mint_persists_to_store(self, minter):
        token = minter.mint_from_approval(scope="Persist test")
        retrieved = minter.store.get(token.id)
        assert retrieved is not None
        assert retrieved.scope == "Persist test"

    def test_check_authority_allowed(self, minter):
        token = minter.mint_from_approval(
            scope="test",
            tools=["read_file", "write_file"],
        )
        result = minter.check_authority(token, tool="read_file")
        assert result.allowed is True

    def test_check_authority_denied_tool(self, minter):
        token = minter.mint_from_approval(
            scope="test",
            tools=["read_file"],
        )
        result = minter.check_authority(token, tool="execute_command")
        assert result.allowed is False
        assert "not in allowed_tools" in result.reason

    def test_check_authority_denied_path(self, minter):
        token = minter.mint_from_approval(
            scope="test",
            paths=["src/*.py"],
        )
        result = minter.check_authority(token, path="/etc/passwd")
        assert result.allowed is False

    def test_check_authority_denied_network(self, minter):
        token = minter.mint_from_approval(
            scope="test",
            network=NetworkPolicy.OFF.value,
        )
        result = minter.check_authority(token, network_host="evil.com")
        assert result.allowed is False

    def test_check_authority_expired_token(self, minter):
        token = minter.mint_from_approval(scope="test", max_actions=0)
        token.actions_used = 1  # Force expired
        result = minter.check_authority(token, tool="any")
        assert result.allowed is False
        assert "expired" in result.reason.lower()

    def test_mint_with_receipt_service(self, tmp_path):
        from src.shared.receipts import ReceiptService
        receipt_svc = ReceiptService(str(tmp_path / "receipts"))
        store = ExecutionTokenStore(tmp_path / "tokens.db")
        minter = PermissionMinter(store=store, receipt_service=receipt_svc)
        token = minter.mint_from_approval(scope="with receipts")
        # Should have created a receipt
        receipts = receipt_svc.list(action_type="token_minted")
        assert len(receipts) >= 1
