# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Webhook Delivery Engine — at-least-once delivery with HMAC-SHA256 signatures.

Delivery guarantees (from spec Section 3.3):
- At-least-once: retried on failure with webhook_id + delivery_attempt for dedup
- Retry schedule: immediate, 30s, 2m, 10m, 30m, 2h (6 total attempts)
- Timeout: 10s per attempt
- GOVERNANCE_CRITICAL always retried to exhaustion
- WEBHOOK_DELIVERY_FAILED receipt after all retries exhausted
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx

from src.core.outbound_http import OutboundNetworkError, assert_url_allowed
from src.observability.webhook_categories import (
    should_deliver,
    is_governance_critical,
    ALL_CATEGORIES,
)
from src.observability.config import WebhookEndpoint

logger = logging.getLogger("lancelot.observability.webhook_engine")

# Retry delays in seconds: immediate, 30s, 2m, 10m, 30m, 2h
RETRY_DELAYS = [0, 30, 120, 600, 1800, 7200]
MAX_ATTEMPTS = 6


@dataclass
class WebhookDelivery:
    """A pending webhook delivery."""
    webhook_id: str
    endpoint: WebhookEndpoint
    payload_envelope: Dict[str, Any]
    attempt: int = 1
    created_at: float = field(default_factory=time.time)
    last_status: Optional[int] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "endpoint_id": self.endpoint.id,
            "payload_envelope": self.payload_envelope,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }


class WebhookEngine:
    """Manages webhook delivery with retry and HMAC signing.

    Runs a background thread for retry scheduling. Delivery attempts
    use httpx with a 10-second timeout.
    """

    def __init__(
        self,
        endpoints: List[WebhookEndpoint],
        deployment_id: str = "",
        delivery_timeout_s: int = 10,
        max_retries: int = MAX_ATTEMPTS,
        data_dir: str = "/home/lancelot/data",
        network_interceptor=None,
    ):
        self._endpoints = {ep.id: ep for ep in endpoints if ep.enabled}
        self._deployment_id = deployment_id or os.getenv("LANCELOT_DEPLOYMENT_ID", "")
        self._timeout = delivery_timeout_s
        self._max_retries = max_retries
        self._pending: List[WebhookDelivery] = []
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client = httpx.Client(timeout=delivery_timeout_s, verify=True)
        self._network_interceptor = network_interceptor
        pending_file_override = os.getenv("LANCELOT_WEBHOOK_PENDING_FILE", "").strip()
        if pending_file_override:
            self._pending_file = Path(pending_file_override)
            self._pending_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._data_dir = Path(data_dir)
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._pending_file = self._data_dir / "webhook_pending_deliveries.json"

        # Stats
        self._stats: Dict[str, Dict[str, int]] = {}
        for ep_id in self._endpoints:
            self._stats[ep_id] = {
                "delivered": 0,
                "failed": 0,
                "pending_retries": 0,
                "last_delivery_ts": 0,
            }
        self._load_pending()

    def start(self) -> None:
        """Start the retry background thread."""
        with self._lock:
            if self._running and self._thread is not None and self._thread.is_alive():
                logger.debug("Webhook engine start skipped; retry thread is already running")
                return
            self._running = True
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._retry_loop, daemon=True, name="webhook-retry"
            )
            self._thread = thread

        thread.start()
        logger.info("Webhook engine started (%d endpoints)", len(self._endpoints))

    def stop(self) -> None:
        """Stop the retry thread and close the HTTP client."""
        with self._lock:
            thread = self._thread
            was_running = self._running or (thread is not None and thread.is_alive())
            self._running = False

        self._stop_event.set()
        if thread:
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning("Webhook engine retry thread did not stop within 5s")
            else:
                with self._lock:
                    if self._thread is thread:
                        self._thread = None

        self._client.close()
        if was_running:
            logger.info("Webhook engine stopped")
        else:
            logger.debug("Webhook engine stop skipped; retry thread was not running")

    def update_endpoints(self, endpoints: List[WebhookEndpoint]) -> None:
        """Hot-update endpoint list (from config changes)."""
        with self._lock:
            self._endpoints = {ep.id: ep for ep in endpoints if ep.enabled}
            for ep_id in self._endpoints:
                if ep_id not in self._stats:
                    self._stats[ep_id] = {
                        "delivered": 0, "failed": 0,
                        "pending_retries": 0, "last_delivery_ts": 0,
                    }
            self._pending = [
                delivery for delivery in self._pending
                if delivery.endpoint.id in self._endpoints
            ]
            self._update_pending_stats_locked()
            self._save_pending_locked()

    def on_receipt(self, receipt_dict: Dict[str, Any]) -> None:
        """Process a receipt and deliver to matching webhook endpoints.

        Called from the receipt bridge. MUST NOT raise.
        """
        action_type = receipt_dict.get("action_type", "")

        for ep_id, ep in self._endpoints.items():
            subscribed = set(ep.categories)
            if not should_deliver(action_type, subscribed):
                continue

            # Build envelope
            envelope = self._build_envelope(receipt_dict, ep)
            delivery = WebhookDelivery(
                webhook_id=str(uuid.uuid4()),
                endpoint=ep,
                payload_envelope=envelope,
            )

            # Attempt immediate delivery
            success = self._deliver(delivery)
            if not success:
                with self._lock:
                    self._pending.append(delivery)
                    self._update_pending_stats_locked()
                    self._save_pending_locked()

    def get_stats(self) -> Dict[str, Any]:
        """Return delivery stats per endpoint."""
        with self._lock:
            self._update_pending_stats_locked()
            return dict(self._stats)

    # ── Internal ──────────────────────────────────────────────────

    def _build_envelope(
        self, receipt_dict: Dict[str, Any], endpoint: WebhookEndpoint
    ) -> Dict[str, Any]:
        """Build the webhook payload envelope (spec Section 3.2)."""
        action_type = receipt_dict.get("action_type", "")
        from src.observability.webhook_categories import get_categories_for_type
        categories = get_categories_for_type(action_type)

        # Event-specific payload from receipt
        payload = {
            "action_type": action_type,
            "action_name": receipt_dict.get("action_name", ""),
            "status": receipt_dict.get("status", ""),
            "tier": receipt_dict.get("tier", 0),
            "inputs": receipt_dict.get("inputs", {}),
            "outputs": receipt_dict.get("outputs", {}),
            "duration_ms": receipt_dict.get("duration_ms"),
            "error_message": receipt_dict.get("error_message"),
        }

        envelope = {
            "webhook_id": str(uuid.uuid4()),
            "delivery_attempt": 1,
            "event_category": categories[0] if categories else "UNCATEGORIZED",
            "event_type": action_type.upper(),
            "timestamp": receipt_dict.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "deployment_id": self._deployment_id,
            "receipt_id": receipt_dict.get("id", ""),
            "quest_id": receipt_dict.get("quest_id"),
            "operator_id": receipt_dict.get("operator_id") or "SYSTEM",
            "operator_name": receipt_dict.get("operator_name", "Lancelot Automation"),
            "payload": payload,
            "signature": "",  # Computed below
        }

        # Compute HMAC-SHA256 signature over the payload field only
        envelope["signature"] = self._compute_signature(payload, endpoint)

        return envelope

    def _compute_signature(
        self, payload: Dict[str, Any], endpoint: WebhookEndpoint
    ) -> str:
        """Compute HMAC-SHA256 signature for the payload.

        Uses the shared secret from the Credential Vault.
        Canonical string is the JSON-serialized payload field.
        """
        secret = self._get_secret(endpoint)
        if not secret:
            return ""

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sig = hmac.new(
            secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def _get_secret(self, endpoint: WebhookEndpoint) -> str:
        """Retrieve the webhook shared secret from the Credential Vault."""
        if not endpoint.secret_vault_key:
            return ""
        try:
            import secret_cache
            if secret_cache.is_bootstrapped():
                return secret_cache.get(endpoint.secret_vault_key) or ""
        except Exception as exc:
            logger.debug(
                "Failed to read webhook secret '%s' from secret cache: %s",
                endpoint.secret_vault_key,
                exc,
            )
        return os.getenv(endpoint.secret_vault_key, "")

    def _deliver(self, delivery: WebhookDelivery) -> bool:
        """Attempt a single webhook delivery. Returns True on success."""
        ep = delivery.endpoint
        envelope = delivery.payload_envelope
        envelope["delivery_attempt"] = delivery.attempt

        try:
            assert_url_allowed(
                ep.url,
                component="Webhook delivery",
                network_interceptor=self._network_interceptor,
            )
            response = self._client.post(
                ep.url,
                json=envelope,
                headers={"Content-Type": "application/json"},
            )
            if 200 <= response.status_code < 300:
                self._record_success(ep.id)
                return True
            else:
                delivery.last_status = response.status_code
                delivery.last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "Webhook delivery failed: endpoint=%s, status=%d, attempt=%d",
                    ep.id, response.status_code, delivery.attempt,
                )
                return False
        except OutboundNetworkError as exc:
            delivery.last_error = str(exc)
            logger.warning("Webhook delivery blocked: endpoint=%s, error=%s", ep.id, exc)
            return False
        except Exception as exc:
            delivery.last_error = str(exc)
            logger.warning(
                "Webhook delivery error: endpoint=%s, error=%s, attempt=%d",
                ep.id, exc, delivery.attempt,
            )
            return False

    def _record_success(self, ep_id: str) -> None:
        """Record a successful delivery."""
        if ep_id in self._stats:
            self._stats[ep_id]["delivered"] += 1
            self._stats[ep_id]["last_delivery_ts"] = int(time.time())

    def _record_failure(self, delivery: WebhookDelivery) -> None:
        """Record a final delivery failure and emit WEBHOOK_DELIVERY_FAILED receipt."""
        ep_id = delivery.endpoint.id
        if ep_id in self._stats:
            self._stats[ep_id]["failed"] += 1

        # Emit WEBHOOK_DELIVERY_FAILED receipt
        try:
            from src.shared.receipts import (
                ActionType, Receipt, ReceiptStatus, get_receipt_service,
            )
            receipt = Receipt(
                action_type=ActionType.WEBHOOK_DELIVERY_FAILED.value,
                action_name="webhook_delivery_failed",
                inputs={
                    "endpoint_id": ep_id,
                    "endpoint_url_hash": hashlib.sha256(
                        delivery.endpoint.url.encode()
                    ).hexdigest()[:16],
                    "event_type": delivery.payload_envelope.get("event_type", ""),
                    "receipt_id_ref": delivery.payload_envelope.get("receipt_id", ""),
                },
                outputs={
                    "total_attempts": delivery.attempt,
                    "last_status": delivery.last_status,
                    "last_error": delivery.last_error,
                },
                status=ReceiptStatus.FAILURE.value,
                operator_id="SYSTEM",
                metadata={"webhook_failure": True},
            )
            svc = get_receipt_service("/home/lancelot/data")
            svc.create(receipt)
        except Exception as exc:
            logger.error("Failed to write WEBHOOK_DELIVERY_FAILED receipt: %s", exc)

    def _load_pending(self) -> None:
        if not self._pending_file.exists():
            return
        try:
            raw = self._pending_file.read_text(encoding="utf-8").strip()
            if not raw:
                return
            data = json.loads(raw)
            pending: List[WebhookDelivery] = []
            for item in data if isinstance(data, list) else []:
                endpoint_id = item.get("endpoint_id", "")
                endpoint = self._endpoints.get(endpoint_id)
                if endpoint is None:
                    continue
                pending.append(
                    WebhookDelivery(
                        webhook_id=item.get("webhook_id", str(uuid.uuid4())),
                        endpoint=endpoint,
                        payload_envelope=dict(item.get("payload_envelope", {})),
                        attempt=int(item.get("attempt", 1)),
                        created_at=float(item.get("created_at", time.time())),
                        last_status=item.get("last_status"),
                        last_error=item.get("last_error"),
                    )
                )
            with self._lock:
                self._pending = pending
                self._update_pending_stats_locked()
        except Exception as exc:
            logger.warning("Failed to load pending webhook deliveries: %s", exc)
            with self._lock:
                self._pending = []
                self._update_pending_stats_locked()

    def _save_pending_locked(self) -> None:
        try:
            payload = [delivery.to_dict() for delivery in self._pending]
            self._pending_file.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to persist pending webhook deliveries: %s", exc)

    def _update_pending_stats_locked(self) -> None:
        pending_counts = {ep_id: 0 for ep_id in self._stats}
        for delivery in self._pending:
            endpoint_id = delivery.endpoint.id
            pending_counts[endpoint_id] = pending_counts.get(endpoint_id, 0) + 1
        for ep_id, stats in self._stats.items():
            stats["pending_retries"] = pending_counts.get(ep_id, 0)

    def _retry_loop(self) -> None:
        """Background thread that processes pending retries."""
        try:
            while not self._stop_event.is_set():
                if self._stop_event.wait(timeout=5):
                    return
                self._process_pending_retries()
        finally:
            with self._lock:
                self._running = False

    def _process_pending_retries(self) -> None:
        """Process pending deliveries whose retry delay has elapsed."""
        now = time.time()
        to_retry: List[WebhookDelivery] = []
        remaining: List[WebhookDelivery] = []

        with self._lock:
            for d in self._pending:
                if d.attempt >= self._max_retries:
                    # Exhausted - record final failure.
                    self._record_failure(d)
                    continue

                retry_after = d.created_at + sum(RETRY_DELAYS[:d.attempt])
                if now >= retry_after:
                    d.attempt += 1
                    to_retry.append(d)
                else:
                    remaining.append(d)

            self._pending = remaining
            self._update_pending_stats_locked()
            self._save_pending_locked()

        # Execute retries outside the lock.
        for d in to_retry:
            success = self._deliver(d)
            if not success:
                with self._lock:
                    self._pending.append(d)
                    self._update_pending_stats_locked()
                    self._save_pending_locked()


# Module-level singleton
_engine: Optional[WebhookEngine] = None


def get_webhook_engine() -> Optional[WebhookEngine]:
    """Return the webhook engine singleton, or None if not initialized."""
    return _engine


def init_webhook_engine(
    endpoints: List[WebhookEndpoint],
    deployment_id: str = "",
    delivery_timeout_s: int = 10,
    max_retries: int = MAX_ATTEMPTS,
    data_dir: str = "/home/lancelot/data",
) -> WebhookEngine:
    """Initialize and start the webhook engine singleton."""
    global _engine
    if _engine:
        _engine.stop()
    _engine = WebhookEngine(
        endpoints=endpoints,
        deployment_id=deployment_id,
        delivery_timeout_s=delivery_timeout_s,
        max_retries=max_retries,
        data_dir=data_dir,
    )
    _engine.start()
    return _engine
