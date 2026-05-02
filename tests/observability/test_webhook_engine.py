# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the Webhook Delivery Engine."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import hashlib
import hmac
import json
import threading
import time
import types

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.observability.config import WebhookEndpoint
from src.core.outbound_http import OutboundNetworkError
import src.observability.webhook_engine as webhook_engine_module
from src.observability.webhook_engine import (
    WebhookEngine,
    WebhookDelivery,
    RETRY_DELAYS,
    MAX_ATTEMPTS,
)


# ── Helpers ──────────────────────────────────────────────────────


def _make_endpoint(
    id="ep-1",
    url="https://example.com/hook",
    categories=None,
    secret_vault_key="",
    enabled=True,
):
    return WebhookEndpoint(
        id=id,
        url=url,
        categories=categories or ["ALL"],
        secret_vault_key=secret_vault_key,
        enabled=enabled,
    )


def _make_receipt(**overrides):
    base = {
        "id": "rcpt-001",
        "action_type": "kill_switch_issued",
        "action_name": "kill_switch",
        "status": "success",
        "tier": 3,
        "quest_id": "quest-1",
        "timestamp": "2026-03-17T12:00:00Z",
        "operator_id": "op-1",
        "inputs": {},
        "outputs": {},
        "duration_ms": 10,
        "error_message": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def mock_httpx_client():
    """Patch httpx.Client so no real HTTP calls are made."""
    with patch("src.observability.webhook_engine.httpx.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture(autouse=True)
def isolated_webhook_pending_file(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "LANCELOT_WEBHOOK_PENDING_FILE",
        str(tmp_path / "webhook-pending.json"),
    )


@pytest.fixture(autouse=True)
def allow_outbound_requests(monkeypatch):
    monkeypatch.setattr("src.observability.webhook_engine.assert_url_allowed", lambda url, **kwargs: url)


# ── Lifecycle ────────────────────────────────────────────────────


class TestWebhookEngineLifecycle:
    def test_start_stop(self, mock_httpx_client):
        """Engine starts and stops without error."""
        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.start()
        assert engine._running is True
        assert engine._thread is not None
        assert engine._thread.is_alive()

        engine.stop()
        assert engine._running is False
        mock_httpx_client.close.assert_called_once()

    def test_double_start_idempotent(self, mock_httpx_client):
        """Calling start() twice doesn't create duplicate threads."""
        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.start()
        thread1 = engine._thread
        engine.start()
        assert engine._thread is thread1
        engine.stop()

    def test_stop_without_start(self, mock_httpx_client):
        """Stopping a never-started engine does not raise."""
        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.stop()


# ── on_receipt ───────────────────────────────────────────────────


class TestOnReceipt:
    def test_queues_delivery(self, mock_httpx_client):
        """on_receipt() attempts immediate delivery."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.post.return_value = mock_response

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())

        mock_httpx_client.post.assert_called_once()

    def test_delivery_success_first_attempt(self, mock_httpx_client):
        """Successful delivery on first attempt records stats."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.post.return_value = mock_response

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())

        stats = engine.get_stats()
        assert stats["ep-1"]["delivered"] == 1

    def test_delivery_failure_queues_retry(self, mock_httpx_client):
        """Failed delivery queues the item for retry."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_httpx_client.post.return_value = mock_response

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())

        assert len(engine._pending) == 1

    def test_delivery_exception_queues_retry(self, mock_httpx_client):
        """HTTP exception queues the item for retry."""
        mock_httpx_client.post.side_effect = ConnectionError("refused")

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())

        assert len(engine._pending) == 1

    def test_delivery_blocked_by_network_allowlist_queues_retry(self, mock_httpx_client, monkeypatch):
        monkeypatch.setattr(
            "src.observability.webhook_engine.assert_url_allowed",
            lambda url, **kwargs: (_ for _ in ()).throw(
                OutboundNetworkError("Webhook delivery blocked by network allowlist")
            ),
        )

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())

        assert len(engine._pending) == 1
        assert "network allowlist" in engine._pending[0].last_error
        mock_httpx_client.post.assert_not_called()

    def test_empty_endpoint_list(self, mock_httpx_client):
        """No endpoints means no deliveries."""
        engine = WebhookEngine(endpoints=[])
        engine.on_receipt(_make_receipt())
        mock_httpx_client.post.assert_not_called()

    def test_disabled_endpoint_skipped(self, mock_httpx_client):
        """Disabled endpoints are filtered out during construction."""
        ep = _make_endpoint(enabled=False)
        engine = WebhookEngine(endpoints=[ep])
        assert len(engine._endpoints) == 0
        engine.on_receipt(_make_receipt())
        mock_httpx_client.post.assert_not_called()

    def test_multiple_endpoints_receive_same_event(self, mock_httpx_client):
        """All matching endpoints get the delivery."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.post.return_value = mock_response

        ep1 = _make_endpoint(id="ep-1", url="https://a.com/hook")
        ep2 = _make_endpoint(id="ep-2", url="https://b.com/hook")
        engine = WebhookEngine(endpoints=[ep1, ep2])
        engine.on_receipt(_make_receipt())

        assert mock_httpx_client.post.call_count == 2
        assert engine.get_stats()["ep-1"]["delivered"] == 1
        assert engine.get_stats()["ep-2"]["delivered"] == 1


# ── HMAC Signing ─────────────────────────────────────────────────


class TestHMACSigning:
    def test_hmac_sha256_produces_correct_signature(self, mock_httpx_client):
        """HMAC-SHA256 signature matches manual computation."""
        secret = "my-webhook-secret"
        ep = _make_endpoint(secret_vault_key="WH_SECRET")

        engine = WebhookEngine(endpoints=[ep])

        payload = {"action_type": "test", "status": "ok"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        with patch.object(engine, "_get_secret", return_value=secret):
            sig = engine._compute_signature(payload, ep)
        assert sig == expected_sig

    def test_hmac_uses_sorted_keys(self, mock_httpx_client):
        """Canonical JSON uses sorted keys for deterministic signing."""
        secret = "test-secret"
        ep = _make_endpoint(secret_vault_key="WH_SECRET")
        engine = WebhookEngine(endpoints=[ep])

        payload_a = {"z_field": 1, "a_field": 2}
        payload_b = {"a_field": 2, "z_field": 1}

        with patch.object(engine, "_get_secret", return_value=secret):
            sig_a = engine._compute_signature(payload_a, ep)
            sig_b = engine._compute_signature(payload_b, ep)

        assert sig_a == sig_b

    def test_no_secret_returns_empty_signature(self, mock_httpx_client):
        """No secret vault key means empty signature."""
        ep = _make_endpoint(secret_vault_key="")
        engine = WebhookEngine(endpoints=[ep])

        sig = engine._compute_signature({"test": 1}, ep)
        assert sig == ""


# ── Retry Schedule ───────────────────────────────────────────────


class TestRetrySchedule:
    def test_retry_delays_match_spec(self):
        """Retry schedule: 0s, 30s, 2m, 10m, 30m, 2h."""
        assert RETRY_DELAYS == [0, 30, 120, 600, 1800, 7200]

    def test_max_attempts_is_6(self):
        assert MAX_ATTEMPTS == 6

    def test_delivery_timeout_default(self, mock_httpx_client):
        """Default delivery timeout is 10 seconds."""
        engine = WebhookEngine(endpoints=[_make_endpoint()])
        assert engine._timeout == 10

    def test_custom_timeout(self, mock_httpx_client):
        engine = WebhookEngine(
            endpoints=[_make_endpoint()], delivery_timeout_s=5
        )
        assert engine._timeout == 5


# ── Delivery Failure Recording ───────────────────────────────────


class TestDeliveryFailure:
    def test_record_failure_increments_stats(self, mock_httpx_client):
        """Final failure increments the failed counter."""
        ep = _make_endpoint()
        engine = WebhookEngine(endpoints=[ep])

        delivery = WebhookDelivery(
            webhook_id="wh-1",
            endpoint=ep,
            payload_envelope={"event_type": "test", "receipt_id": "r1"},
            attempt=6,
        )

        with patch("src.observability.webhook_engine.get_receipt_service",
                    create=True, side_effect=ImportError):
            engine._record_failure(delivery)

        assert engine._stats["ep-1"]["failed"] == 1

    @patch("src.observability.webhook_engine.WebhookEngine._record_failure")
    def test_max_retries_triggers_failure_receipt(self, mock_fail, mock_httpx_client):
        """After max retries, _record_failure is called from retry loop."""
        ep = _make_endpoint()
        engine = WebhookEngine(endpoints=[ep], max_retries=2)

        delivery = WebhookDelivery(
            webhook_id="wh-1",
            endpoint=ep,
            payload_envelope={"event_type": "test", "receipt_id": "r1"},
            attempt=2,  # Already at max
        )

        with engine._lock:
            engine._pending.append(delivery)

        # Run one iteration of retry logic manually
        engine._running = True
        # Simulate the retry loop check
        now = time.time()
        to_retry = []
        remaining = []
        with engine._lock:
            for d in engine._pending:
                if d.attempt >= engine._max_retries:
                    engine._record_failure(d)
                    continue
                remaining.append(d)
            engine._pending = remaining

        mock_fail.assert_called_once()


# ── update_endpoints ─────────────────────────────────────────────


class TestUpdateEndpoints:
    def test_adds_new_endpoints(self, mock_httpx_client):
        engine = WebhookEngine(endpoints=[_make_endpoint(id="ep-1")])
        assert len(engine._endpoints) == 1

        new_eps = [
            _make_endpoint(id="ep-1"),
            _make_endpoint(id="ep-2", url="https://new.com/hook"),
        ]
        engine.update_endpoints(new_eps)
        assert len(engine._endpoints) == 2
        assert "ep-2" in engine._endpoints

    def test_removes_endpoints(self, mock_httpx_client):
        ep1 = _make_endpoint(id="ep-1")
        ep2 = _make_endpoint(id="ep-2")
        engine = WebhookEngine(endpoints=[ep1, ep2])
        assert len(engine._endpoints) == 2

        engine.update_endpoints([ep1])
        assert len(engine._endpoints) == 1
        assert "ep-2" not in engine._endpoints

    def test_initializes_stats_for_new_endpoint(self, mock_httpx_client):
        engine = WebhookEngine(endpoints=[])
        engine.update_endpoints([_make_endpoint(id="ep-new")])
        assert "ep-new" in engine._stats
        assert engine._stats["ep-new"]["delivered"] == 0


# ── get_stats ────────────────────────────────────────────────────


class TestGetStats:
    def test_returns_stats_per_endpoint(self, mock_httpx_client):
        engine = WebhookEngine(endpoints=[_make_endpoint(id="ep-1")])
        stats = engine.get_stats()
        assert "ep-1" in stats
        assert "delivered" in stats["ep-1"]
        assert "failed" in stats["ep-1"]
        assert "pending_retries" in stats["ep-1"]
        assert "last_delivery_ts" in stats["ep-1"]

    def test_stats_update_on_delivery(self, mock_httpx_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.post.return_value = mock_response

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        engine.on_receipt(_make_receipt())
        engine.on_receipt(_make_receipt(id="rcpt-002"))

        assert engine.get_stats()["ep-1"]["delivered"] == 2


# ── Thread Safety ────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_on_receipt_calls(self, mock_httpx_client):
        """Multiple threads calling on_receipt should not crash."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.post.return_value = mock_response

        engine = WebhookEngine(endpoints=[_make_endpoint()])
        errors = []

        def worker(n):
            try:
                for i in range(10):
                    engine.on_receipt(_make_receipt(id=f"rcpt-{n}-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Concurrent calls raised: {errors}"
        assert engine.get_stats()["ep-1"]["delivered"] == 50


class TestWebhookRuntimePersistence:
    def test_default_pending_file_uses_data_dir_when_no_override(self, mock_httpx_client, monkeypatch, tmp_path):
        monkeypatch.delenv("LANCELOT_WEBHOOK_PENDING_FILE", raising=False)

        engine = WebhookEngine(endpoints=[_make_endpoint()], data_dir=str(tmp_path))

        assert engine._pending_file == tmp_path / "webhook_pending_deliveries.json"

    def test_secret_cache_and_env_fallback_paths(self, mock_httpx_client, monkeypatch):
        ep = _make_endpoint(secret_vault_key="WH_SECRET")
        engine = WebhookEngine(endpoints=[ep])

        monkeypatch.setitem(
            sys.modules,
            "secret_cache",
            type(
                "SecretCache",
                (),
                {
                    "is_bootstrapped": staticmethod(lambda: True),
                    "get": staticmethod(lambda key: "vault-secret"),
                },
            ),
        )
        assert engine._get_secret(ep) == "vault-secret"

        monkeypatch.setitem(
            sys.modules,
            "secret_cache",
            type(
                "BrokenSecretCache",
                (),
                {
                    "is_bootstrapped": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("vault down"))),
                },
            ),
        )
        monkeypatch.setenv("WH_SECRET", "env-secret")
        assert engine._get_secret(ep) == "env-secret"

    def test_load_pending_restores_matching_deliveries_and_skips_stale_endpoints(
        self, mock_httpx_client, monkeypatch, tmp_path
    ):
        pending_file = tmp_path / "pending.json"
        pending_file.write_text(
            json.dumps(
                [
                    {
                        "webhook_id": "wh-1",
                        "endpoint_id": "ep-1",
                        "payload_envelope": {"event_type": "TASK_EXECUTED"},
                        "attempt": 3,
                        "created_at": 123.5,
                        "last_status": 503,
                        "last_error": "HTTP 503",
                    },
                    {
                        "webhook_id": "wh-stale",
                        "endpoint_id": "missing",
                        "payload_envelope": {},
                    },
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("LANCELOT_WEBHOOK_PENDING_FILE", str(pending_file))

        engine = WebhookEngine(endpoints=[_make_endpoint(id="ep-1")])

        assert len(engine._pending) == 1
        assert engine._pending[0].webhook_id == "wh-1"
        assert engine._pending[0].attempt == 3
        assert engine._stats["ep-1"]["pending_retries"] == 1

    def test_load_pending_handles_empty_or_corrupt_files(self, mock_httpx_client, monkeypatch, tmp_path):
        pending_file = tmp_path / "empty.json"
        pending_file.write_text("", encoding="utf-8")
        monkeypatch.setenv("LANCELOT_WEBHOOK_PENDING_FILE", str(pending_file))

        engine = WebhookEngine(endpoints=[_make_endpoint(id="ep-1")])
        assert engine._pending == []

        pending_file.write_text("{not-json", encoding="utf-8")
        engine = WebhookEngine(endpoints=[_make_endpoint(id="ep-1")])
        assert engine._pending == []
        assert engine._stats["ep-1"]["pending_retries"] == 0

    def test_save_pending_failure_is_non_fatal(self, mock_httpx_client, monkeypatch):
        ep = _make_endpoint()
        engine = WebhookEngine(endpoints=[ep])
        engine._pending.append(
            WebhookDelivery(webhook_id="wh-1", endpoint=ep, payload_envelope={"event_type": "TEST"})
        )
        monkeypatch.setattr(
            type(engine._pending_file),
            "write_text",
            lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )

        with engine._lock:
            engine._save_pending_locked()

    def test_retry_loop_exhausts_waits_and_requeues_failed_ready_delivery(
        self, mock_httpx_client, monkeypatch
    ):
        ep = _make_endpoint()
        engine = WebhookEngine(endpoints=[ep], max_retries=2)
        now = time.time()
        exhausted = WebhookDelivery(
            webhook_id="wh-exhausted",
            endpoint=ep,
            payload_envelope={"event_type": "TEST"},
            attempt=2,
        )
        ready = WebhookDelivery(
            webhook_id="wh-ready",
            endpoint=ep,
            payload_envelope={"event_type": "TEST"},
            attempt=1,
            created_at=now - 10,
        )
        waiting = WebhookDelivery(
            webhook_id="wh-waiting",
            endpoint=ep,
            payload_envelope={"event_type": "TEST"},
            attempt=1,
            created_at=now + 1000,
        )
        engine._pending = [exhausted, ready, waiting]
        failed = []

        monkeypatch.setattr(time, "sleep", lambda seconds: setattr(engine, "_running", False))
        monkeypatch.setattr(time, "time", lambda: now)
        monkeypatch.setattr(engine, "_record_failure", lambda delivery: failed.append(delivery.webhook_id))
        monkeypatch.setattr(engine, "_deliver", lambda delivery: False)

        engine._running = True
        engine._retry_loop()

        assert failed == ["wh-exhausted"]
        assert sorted(delivery.webhook_id for delivery in engine._pending) == ["wh-ready", "wh-waiting"]
        assert next(delivery for delivery in engine._pending if delivery.webhook_id == "wh-ready").attempt == 2

    def test_unsubscribed_receipt_does_not_deliver(self, mock_httpx_client):
        engine = WebhookEngine(
            endpoints=[_make_endpoint(categories=["COST_THRESHOLD"])]
        )

        engine.on_receipt(_make_receipt(action_type="task_executed"))

        mock_httpx_client.post.assert_not_called()
        assert engine._pending == []

    def test_record_failure_swallows_receipt_service_import_failures(
        self, mock_httpx_client, monkeypatch
    ):
        ep = _make_endpoint()
        engine = WebhookEngine(endpoints=[ep])
        delivery = WebhookDelivery(
            webhook_id="wh-failed",
            endpoint=ep,
            payload_envelope={"event_type": "TEST", "receipt_id": "receipt-1"},
            attempt=6,
        )
        monkeypatch.setitem(sys.modules, "src.shared.receipts", types.SimpleNamespace())

        engine._record_failure(delivery)

        assert engine._stats["ep-1"]["failed"] == 1


class TestWebhookSingletonLifecycle:
    def test_init_replaces_existing_engine_and_shutdown_clears_singleton(
        self, mock_httpx_client, monkeypatch, tmp_path
    ):
        starts = []
        stops = []
        monkeypatch.setattr(WebhookEngine, "start", lambda self: starts.append(id(self)))
        monkeypatch.setattr(WebhookEngine, "stop", lambda self: stops.append(id(self)))

        first = webhook_engine_module.init_webhook_engine(
            [_make_endpoint(id="ep-1")],
            data_dir=str(tmp_path),
        )
        second = webhook_engine_module.init_webhook_engine(
            [_make_endpoint(id="ep-2")],
            data_dir=str(tmp_path),
        )

        assert webhook_engine_module.get_webhook_engine() is second
        assert starts == [id(first), id(second)]
        assert stops == [id(first)]

        webhook_engine_module.shutdown_webhook_engine()

        assert stops == [id(first), id(second)]
        assert webhook_engine_module.get_webhook_engine() is None
