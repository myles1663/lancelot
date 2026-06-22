"""Receipt hash-chain and signing helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .receipts_models import Receipt, ReceiptIntegrityKeyError
except ImportError:  # pragma: no cover - legacy top-level import path
    from receipts_models import Receipt, ReceiptIntegrityKeyError

INTEGRITY_KEY_VERSION = 1
INTEGRITY_KEY_FILENAME = "receipt_integrity_key.json"
INTEGRITY_CHAIN_HEAD = "0" * 64


def derive_env_key_id(integrity_secret: bytes) -> str:
    configured = os.environ.get("LANCELOT_RECEIPT_HMAC_KEY_ID", "").strip()
    if configured:
        return configured
    digest = hashlib.sha256(integrity_secret).hexdigest()[:12]
    return f"env:receipt-hmac:{digest}"


def build_local_key_record(integrity_secret: bytes, version: int = INTEGRITY_KEY_VERSION) -> Dict[str, Any]:
    digest = hashlib.sha256(integrity_secret).hexdigest()[:12]
    return {
        "version": version,
        "algorithm": "hmac-sha256",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "key_id": f"local:receipt-hmac:{digest}",
        "secret_b64": base64.b64encode(integrity_secret).decode("ascii"),
    }


def load_integrity_key_file(path: str, version: int = INTEGRITY_KEY_VERSION) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ReceiptIntegrityKeyError(
            f"Receipt integrity key file is unreadable: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ReceiptIntegrityKeyError(
            "Receipt integrity key file must contain a JSON object"
        )

    actual_version = payload.get("version")
    key_id = payload.get("key_id")
    secret_b64 = payload.get("secret_b64")
    algorithm = payload.get("algorithm")
    if actual_version != version:
        raise ReceiptIntegrityKeyError(
            f"Unsupported receipt integrity key version: {actual_version}"
        )
    if algorithm != "hmac-sha256":
        raise ReceiptIntegrityKeyError(
            f"Unsupported receipt integrity key algorithm: {algorithm}"
        )
    if not isinstance(key_id, str) or not key_id.strip():
        raise ReceiptIntegrityKeyError(
            "Receipt integrity key file is missing a valid key_id"
        )
    if not isinstance(secret_b64, str) or not secret_b64.strip():
        raise ReceiptIntegrityKeyError(
            "Receipt integrity key file is missing the signing secret"
        )

    return {"key_id": key_id, "secret_b64": secret_b64}


def create_integrity_key_file(path: str, version: int, build_record, load_existing) -> Dict[str, str]:
    integrity_secret = secrets.token_bytes(32)
    payload = build_record(integrity_secret)
    serialized = f"{json.dumps(payload, indent=2)}\n".encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return load_existing()
    except OSError as exc:
        raise ReceiptIntegrityKeyError(
            f"Unable to create receipt integrity key file: {exc}"
        ) from exc

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
    except Exception as exc:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise ReceiptIntegrityKeyError(
            f"Unable to persist receipt integrity key file: {exc}"
        ) from exc

    return {"key_id": payload["key_id"], "secret_b64": payload["secret_b64"]}


def canonical_receipt_payload(receipt: Receipt, prev_hash: str) -> Dict[str, Any]:
    return {
        "id": receipt.id,
        "timestamp": receipt.timestamp,
        "action_type": receipt.action_type,
        "action_name": receipt.action_name,
        "inputs": receipt.inputs,
        "outputs": receipt.outputs,
        "status": receipt.status,
        "duration_ms": receipt.duration_ms,
        "token_count": receipt.token_count,
        "tier": receipt.tier,
        "parent_id": receipt.parent_id,
        "quest_id": receipt.quest_id,
        "error_message": receipt.error_message,
        "metadata": receipt.metadata,
        "operator_id": receipt.operator_id,
        "session_id": receipt.session_id,
        "prev_hash": prev_hash,
    }


def compute_integrity_hash(receipt: Receipt, prev_hash: str) -> str:
    payload = canonical_receipt_payload(receipt, prev_hash)
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_integrity_signature(integrity_hash: str, integrity_secret: bytes) -> Optional[str]:
    return hmac.new(
        integrity_secret,
        integrity_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
