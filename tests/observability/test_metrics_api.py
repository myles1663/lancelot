# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the War Room Metrics API."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import base64
import json
import sqlite3
import time

import pytest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.observability.metrics_api as metrics_module
from src.observability.metrics_api import (
    router,
    init_metrics_api,
    _encode_cursor,
    _decode_cursor,
    _check_rate_limit,
    _envelope,
)
from src.core.api_auth import init_api_auth, require_authenticated_request
import src.core.auth_api as auth_api
from types import SimpleNamespace


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create a FastAPI app with the metrics router mounted."""
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture(autouse=True)
def patch_auth():
    init_api_auth(lambda request: True)
    original_request_has_capability = auth_api.request_has_capability
    original_load_config = metrics_module.load_config
    auth_api.request_has_capability = lambda request, capability: True
    metrics_module.load_config = lambda: SimpleNamespace(
        metrics_api=SimpleNamespace(
            enabled=True,
            rate_limit_per_minute=1000,
            receipt_queries=False,
        )
    )
    yield
    auth_api.request_has_capability = original_request_has_capability
    metrics_module.load_config = original_load_config
    init_api_auth(None)


@pytest.fixture
def mock_receipt_service(tmp_path):
    """Create a mock receipt service backed by a real SQLite DB."""
    db_path = str(tmp_path / "receipts.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE receipts (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            action_type TEXT,
            action_name TEXT,
            status TEXT,
            tier INTEGER DEFAULT 0,
            duration_ms INTEGER,
            quest_id TEXT,
            operator_id TEXT,
            inputs TEXT DEFAULT '{}',
            outputs TEXT DEFAULT '{}'
        )
    """)

    # Seed some test data
    for i in range(5):
        conn.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"rcpt-{i:03d}",
                f"2026-03-17T12:{i:02d}:00Z",
                "task_executed",
                f"action_{i}",
                "success",
                i % 4,
                100 + i * 10,
                f"quest-{i % 2}",
                f"op-{i % 3}",
                json.dumps({}),
                json.dumps({"cost_usd": 0.01 * (i + 1), "provider": "gemini"}),
            ),
        )
    conn.commit()

    class MockReceipt:
        def __init__(self, row):
            self._payload = dict(row)

        def to_dict(self):
            return dict(self._payload)

    def _query_receipts(
        *,
        limit=100,
        offset=0,
        action_type=None,
        status=None,
        quest_id=None,
        operator_id=None,
        risk_tier=None,
        since=None,
        until=None,
    ):
        sql = "SELECT * FROM receipts WHERE 1=1"
        params = []
        for clause, value in (
            ("action_type = ?", action_type),
            ("status = ?", status),
            ("quest_id = ?", quest_id),
            ("operator_id = ?", operator_id),
            ("timestamp >= ?", since),
            ("timestamp <= ?", until),
        ):
            if value:
                sql += f" AND {clause}"
                params.append(value)
        if risk_tier is not None:
            sql += " AND tier = ?"
            params.append(risk_tier)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [MockReceipt(row) for row in conn.execute(sql, params).fetchall()]

    def _aggregate_counts(*, group_by, since=None, until=None):
        sql = f"SELECT {group_by} as group_key, COUNT(*) as count FROM receipts WHERE 1=1"
        params = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        sql += f" GROUP BY {group_by} ORDER BY count DESC"
        return [
            {"key": row["group_key"], "count": row["count"]}
            for row in conn.execute(sql, params).fetchall()
        ]

    def _list_action_outputs(*, action_type, since=None, until=None):
        sql = "SELECT outputs FROM receipts WHERE action_type = ?"
        params = [action_type]
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        rows = conn.execute(sql, params).fetchall()
        return [json.loads(row["outputs"]) for row in rows]

    svc = MagicMock()
    svc.list.side_effect = _query_receipts
    svc.aggregate_counts.side_effect = _aggregate_counts
    svc.list_action_outputs.side_effect = _list_action_outputs
    svc.get.return_value = None  # Default: not found
    return svc


@pytest.fixture
def client(app, mock_receipt_service):
    """Provide a TestClient with an initialized metrics API."""
    init_metrics_api(mock_receipt_service)
    # Reset rate limit buckets
    metrics_module._rate_buckets.clear()
    return TestClient(app)


@pytest.fixture(autouse=True)
def patch_externals():
    """Patch external imports that won't be available in test env."""
    with patch.object(metrics_module, "_get_soul_version", return_value="test-soul-v1"), \
         patch.object(metrics_module, "_get_deployment_id", return_value="test-deploy"):
        yield


# ── GET /metrics/summary ─────────────────────────────────────────


class TestMetricsSummary:
    @patch("src.observability.metrics_api.metrics_summary.__wrapped__",
           new=None, create=True)
    def test_returns_valid_structure(self, client):
        """Summary endpoint returns expected envelope and data keys."""
        resp = client.get("/api/metrics/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "api_version" in body
        assert "data" in body
        data = body["data"]
        assert "active_kill_switches" in data
        assert "pending_t3_approvals" in data
        assert "current_spend_rate_usd_hr" in data
        assert "active_hive_agents" in data
        assert "soul_version" in data

    def test_envelope_has_soul_version(self, client):
        resp = client.get("/api/metrics/summary")
        body = resp.json()
        assert body["soul_version"] == "test-soul-v1"
        assert body["deployment_id"] == "test-deploy"

    def test_summary_surfaces_runtime_degradation(self, client):
        with patch("src.core.feature_flags.get_all_flags", side_effect=RuntimeError("flags exploded")), \
             patch("src.observability.metrics_api._get_pending_t3_approvals_count", side_effect=RuntimeError("approvals exploded")), \
             patch("src.observability.metrics_api._get_active_hive_agents_count", side_effect=RuntimeError("hive exploded")), \
             patch("src.core.control_plane.get_usage_tracker", side_effect=RuntimeError("tracker exploded")):
            resp = client.get("/api/metrics/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_degraded"] is True
        assert "Kill switch status unavailable" in body["degraded_reasons"]
        assert "Pending approval status unavailable" in body["degraded_reasons"]
        assert "HIVE runtime status unavailable" in body["degraded_reasons"]
        assert "Cost tracker status unavailable" in body["degraded_reasons"]
        assert any("flags exploded" in err for err in body["runtime_errors"])
        assert any("approvals exploded" in err for err in body["runtime_errors"])
        assert any("hive exploded" in err for err in body["runtime_errors"])
        assert any("tracker exploded" in err for err in body["runtime_errors"])


# ── GET /metrics/receipts ────────────────────────────────────────


class TestMetricsReceipts:
    def test_returns_receipts(self, client):
        resp = client.get("/api/metrics/receipts")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "receipts" in body["data"]
        assert len(body["data"]["receipts"]) == 5

    def test_pagination_limit(self, client):
        resp = client.get("/api/metrics/receipts?limit=2")
        body = resp.json()
        assert len(body["data"]["receipts"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["cursor"] is not None

    def test_pagination_cursor_follows(self, client):
        """Cursor from first page fetches next page."""
        resp1 = client.get("/api/metrics/receipts?limit=2")
        cursor = resp1.json()["pagination"]["cursor"]

        resp2 = client.get(f"/api/metrics/receipts?limit=2&cursor={cursor}")
        body2 = resp2.json()
        assert len(body2["data"]["receipts"]) == 2

        # Receipts should be different between pages
        ids1 = {r["id"] for r in resp1.json()["data"]["receipts"]}
        ids2 = {r["id"] for r in body2["data"]["receipts"]}
        assert ids1.isdisjoint(ids2)

    def test_filter_by_action_type(self, client):
        resp = client.get("/api/metrics/receipts?receipt_type=task_executed")
        body = resp.json()
        for r in body["data"]["receipts"]:
            assert r["action_type"] == "task_executed"

    def test_filter_by_quest_id(self, client):
        resp = client.get("/api/metrics/receipts?quest_id=quest-0")
        body = resp.json()
        for r in body["data"]["receipts"]:
            assert r["quest_id"] == "quest-0"


# ── GET /metrics/receipts/{receipt_id} ───────────────────────────


class TestMetricsReceiptDetail:
    def test_not_found_returns_404(self, client):
        resp = client.get("/api/metrics/receipts/nonexistent-id")
        assert resp.status_code == 404

    def test_found_returns_receipt(self, client, mock_receipt_service):
        mock_receipt = MagicMock()
        mock_receipt.to_dict.return_value = {
            "id": "rcpt-found",
            "action_type": "task_executed",
            "status": "success",
        }
        mock_receipt_service.get.return_value = mock_receipt

        resp = client.get("/api/metrics/receipts/rcpt-found")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["receipt"]["id"] == "rcpt-found"


# ── GET /metrics/actions ─────────────────────────────────────────


class TestMetricsActions:
    def test_returns_action_counts(self, client):
        resp = client.get("/api/metrics/actions")
        assert resp.status_code == 200
        body = resp.json()
        assert "groups" in body["data"]
        assert body["data"]["group_by"] == "risk_tier"

    def test_group_by_receipt_type(self, client):
        resp = client.get("/api/metrics/actions?group_by=receipt_type")
        body = resp.json()
        assert body["data"]["group_by"] == "receipt_type"
        # All 5 are task_executed
        groups = body["data"]["groups"]
        assert any(g["key"] == "task_executed" for g in groups)


# ── GET /metrics/cost ────────────────────────────────────────────


class TestMetricsCost:
    def test_returns_cost_data(self, client):
        resp = client.get("/api/metrics/cost")
        assert resp.status_code == 200
        body = resp.json()
        assert "cost_groups" in body["data"]

    def test_cost_aggregation(self, client):
        resp = client.get("/api/metrics/cost?group_by=provider")
        body = resp.json()
        groups = body["data"]["cost_groups"]
        # All 5 receipts have provider=gemini
        assert len(groups) >= 1
        gemini = next((g for g in groups if g["key"] == "gemini"), None)
        assert gemini is not None
        assert gemini["total_usd"] > 0


# ── GET /metrics/trust-ledger ────────────────────────────────────


class TestMetricsTrustLedger:
    def test_returns_trust_data(self, client):
        resp = client.get("/api/metrics/trust-ledger")
        assert resp.status_code == 200
        body = resp.json()
        assert "entries" in body["data"]


# ── GET /metrics/soul ────────────────────────────────────────────


class TestMetricsSoul:
    def test_returns_soul_metadata(self, client):
        resp = client.get("/api/metrics/soul")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body["data"]

    def test_surfaces_soul_runtime_failure(self, client):
        with patch("src.core.soul.store.load_active_soul", side_effect=RuntimeError("soul exploded")):
            resp = client.get("/api/metrics/soul")

        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_degraded"] is True
        assert "Soul status unavailable" in body["degraded_reasons"]
        assert any("soul exploded" in err for err in body["runtime_errors"])


# ── GET /metrics/kill-switches ───────────────────────────────────


class TestMetricsKillSwitches:
    def test_returns_flag_states(self, client):
        resp = client.get("/api/metrics/kill-switches")
        assert resp.status_code == 200
        body = resp.json()
        assert "switches" in body["data"]
        assert "total" in body["data"]

    def test_surfaces_kill_switch_failure(self, client):
        with patch("src.core.feature_flags.get_all_flags", side_effect=RuntimeError("flags exploded")):
            resp = client.get("/api/metrics/kill-switches")

        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_degraded"] is True
        assert "Kill switch status unavailable" in body["degraded_reasons"]
        assert any("flags exploded" in err for err in body["runtime_errors"])


class TestMetricsWebhookStatus:
    def test_surfaces_webhook_engine_failure(self, client):
        with patch("src.observability.webhook_engine.get_webhook_engine", side_effect=RuntimeError("engine exploded")):
            resp = client.get("/api/metrics/webhooks/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_degraded"] is True
        assert "Webhook engine status unavailable" in body["degraded_reasons"]
        assert any("engine exploded" in err for err in body["runtime_errors"])


# ── Cursor Encoding / Decoding ───────────────────────────────────


class TestCursorPagination:
    def test_encode_decode_roundtrip(self):
        for offset in [0, 1, 50, 100, 999]:
            cursor = _encode_cursor(offset)
            assert _decode_cursor(cursor) == offset

    def test_cursor_is_base64_urlsafe(self):
        cursor = _encode_cursor(42)
        # Should be valid base64 urlsafe
        decoded = base64.urlsafe_b64decode(cursor).decode()
        assert decoded == "42"

    def test_invalid_cursor_defaults_to_zero(self):
        assert _decode_cursor("not-valid-base64!!!") == 0

    def test_none_cursor_returns_zero(self):
        assert _decode_cursor(None) == 0

    def test_empty_cursor_returns_zero(self):
        assert _decode_cursor("") == 0


# ── Rate Limiting ────────────────────────────────────────────────


class TestRateLimiting:
    def setup_method(self):
        metrics_module._rate_buckets.clear()

    def test_allows_within_limit(self):
        for _ in range(60):
            assert _check_rate_limit("op-test", max_per_minute=60) is True

    def test_blocks_over_limit(self):
        for _ in range(60):
            _check_rate_limit("op-test", max_per_minute=60)
        assert _check_rate_limit("op-test", max_per_minute=60) is False

    def test_separate_operators_have_separate_buckets(self):
        for _ in range(60):
            _check_rate_limit("op-a", max_per_minute=60)
        # op-a is at limit, but op-b should still be allowed
        assert _check_rate_limit("op-b", max_per_minute=60) is True

    def test_old_entries_expire(self):
        """Entries older than 60s are pruned."""
        metrics_module._rate_buckets["op-expire"] = [time.time() - 120] * 60
        assert _check_rate_limit("op-expire", max_per_minute=60) is True


# ── Service Not Initialized ──────────────────────────────────────


class TestServiceNotInitialized:
    def test_summary_returns_503_when_not_initialized(self, app):
        """Endpoints return 503 when receipt service is None."""
        metrics_module._receipt_service = None
        tc = TestClient(app)
        resp = tc.get("/api/metrics/summary")
        assert resp.status_code == 503
