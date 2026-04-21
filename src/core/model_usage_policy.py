"""
Persisted admin policy for local model usage.

The local model is always installed as part of the supported product shape.
This module controls how that installed model is used at runtime:

- local execution for low-risk token-saving work
- local scrubbing before frontier egress
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Optional


LOCAL_EXECUTION_DISABLED = "disabled"
LOCAL_EXECUTION_LOW_RISK_ONLY = "low_risk_only"
VALID_LOCAL_EXECUTION_MODES = {
    LOCAL_EXECUTION_DISABLED,
    LOCAL_EXECUTION_LOW_RISK_ONLY,
}

FRONTIER_SCRUB_REQUIRED = "required"
FRONTIER_SCRUB_PREFERRED = "preferred"
FRONTIER_SCRUB_DISABLED = "disabled"
VALID_FRONTIER_SCRUB_MODES = {
    FRONTIER_SCRUB_REQUIRED,
    FRONTIER_SCRUB_PREFERRED,
    FRONTIER_SCRUB_DISABLED,
}

_POLICY_FILENAME = "model_usage_policy.json"


@dataclass
class ModelUsagePolicy:
    local_execution_mode: str = LOCAL_EXECUTION_LOW_RISK_ONLY
    frontier_scrub_mode: str = FRONTIER_SCRUB_REQUIRED
    updated_at: Optional[float] = None


_data_dir: Optional[str] = None
_policy = ModelUsagePolicy()


def _default_runtime_status() -> dict:
    return {
        "local_execution_available": False,
        "local_scrub_available": False,
        "availability_reason": "Local model not initialized",
        "local_model_loaded": False,
        "local_model_ready": False,
        "local_model_status": "unavailable",
        "local_model_last_verified_at": None,
        "local_model_last_checked_at": None,
        "local_model_last_error": None,
        "local_model_consecutive_failures": 0,
        "local_model_last_smoke_elapsed_ms": None,
        "frontier_scrub_fallback_active": False,
        "frontier_scrub_fallback_count": 0,
        "last_frontier_scrub_fallback_at": None,
        "last_frontier_scrub_fallback_reason": None,
    }


_runtime_status = _default_runtime_status()


def _policy_path() -> str:
    if not _data_dir:
        raise RuntimeError("Model usage policy not initialized")
    return os.path.join(_data_dir, _POLICY_FILENAME)


def _default_policy() -> ModelUsagePolicy:
    return ModelUsagePolicy(
        local_execution_mode=os.environ.get(
            "LANCELOT_LOCAL_EXECUTION_MODE",
            LOCAL_EXECUTION_LOW_RISK_ONLY,
        ).strip().lower() or LOCAL_EXECUTION_LOW_RISK_ONLY,
        frontier_scrub_mode=os.environ.get(
            "LANCELOT_FRONTIER_SCRUB_MODE",
            FRONTIER_SCRUB_REQUIRED,
        ).strip().lower() or FRONTIER_SCRUB_REQUIRED,
    )


def _validate_policy(policy: ModelUsagePolicy) -> ModelUsagePolicy:
    if policy.local_execution_mode not in VALID_LOCAL_EXECUTION_MODES:
        raise ValueError(
            f"Invalid local_execution_mode: {policy.local_execution_mode}"
        )
    if policy.frontier_scrub_mode not in VALID_FRONTIER_SCRUB_MODES:
        raise ValueError(
            f"Invalid frontier_scrub_mode: {policy.frontier_scrub_mode}"
        )
    return policy


def _load_policy() -> ModelUsagePolicy:
    path = _policy_path()
    if not os.path.exists(path):
        return _validate_policy(_default_policy())

    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    policy = ModelUsagePolicy(
        local_execution_mode=str(
            raw.get("local_execution_mode", LOCAL_EXECUTION_LOW_RISK_ONLY)
        ).strip().lower(),
        frontier_scrub_mode=str(
            raw.get("frontier_scrub_mode", FRONTIER_SCRUB_REQUIRED)
        ).strip().lower(),
        updated_at=raw.get("updated_at"),
    )
    return _validate_policy(policy)


def _save_policy() -> None:
    path = _policy_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    payload = asdict(_policy)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp_path, path)


def init_model_usage_policy(data_dir: str) -> None:
    global _data_dir, _policy, _runtime_status
    _data_dir = data_dir
    _policy = _load_policy()
    _runtime_status = _default_runtime_status()


def get_model_usage_policy() -> dict:
    return asdict(_policy)


def get_model_usage_status() -> dict:
    data = get_model_usage_policy()
    data.update(_runtime_status)
    return data


def update_model_usage_policy(
    *,
    local_execution_mode: Optional[str] = None,
    frontier_scrub_mode: Optional[str] = None,
) -> dict:
    global _policy
    next_policy = ModelUsagePolicy(
        local_execution_mode=(
            local_execution_mode.strip().lower()
            if local_execution_mode is not None
            else _policy.local_execution_mode
        ),
        frontier_scrub_mode=(
            frontier_scrub_mode.strip().lower()
            if frontier_scrub_mode is not None
            else _policy.frontier_scrub_mode
        ),
        updated_at=time.time(),
    )
    _policy = _validate_policy(next_policy)
    _save_policy()
    return get_model_usage_status()


def set_local_model_availability(
    available: bool,
    reason: str = "",
    *,
    loaded: Optional[bool] = None,
    ready: Optional[bool] = None,
    last_verified_at: Optional[str] = None,
    last_checked_at: Optional[str] = None,
    last_error: Optional[str] = None,
    consecutive_failures: Optional[int] = None,
    last_smoke_elapsed_ms: Optional[float] = None,
) -> None:
    loaded_value = _runtime_status["local_model_loaded"] if loaded is None else bool(loaded)
    ready_value = bool(available) if ready is None else bool(ready)
    _runtime_status["local_execution_available"] = ready_value
    _runtime_status["local_scrub_available"] = ready_value
    _runtime_status["availability_reason"] = reason or (
        "Local model ready" if ready_value else "Local model unavailable"
    )
    _runtime_status["local_model_loaded"] = loaded_value
    _runtime_status["local_model_ready"] = ready_value
    if ready_value:
        _runtime_status["local_model_status"] = "ready"
    elif loaded_value:
        _runtime_status["local_model_status"] = "loaded_not_ready"
    else:
        _runtime_status["local_model_status"] = "unavailable"
    if last_verified_at is not None:
        _runtime_status["local_model_last_verified_at"] = last_verified_at
    if last_checked_at is not None:
        _runtime_status["local_model_last_checked_at"] = last_checked_at
    if last_error is not None:
        _runtime_status["local_model_last_error"] = last_error
    elif ready_value:
        _runtime_status["local_model_last_error"] = None
    if consecutive_failures is not None:
        _runtime_status["local_model_consecutive_failures"] = consecutive_failures
    if last_smoke_elapsed_ms is not None:
        _runtime_status["local_model_last_smoke_elapsed_ms"] = last_smoke_elapsed_ms


def clear_frontier_scrub_fallback() -> None:
    _runtime_status["frontier_scrub_fallback_active"] = False
    _runtime_status["last_frontier_scrub_fallback_reason"] = None


def record_frontier_scrub_fallback(reason: str) -> None:
    _runtime_status["frontier_scrub_fallback_active"] = True
    _runtime_status["frontier_scrub_fallback_count"] += 1
    _runtime_status["last_frontier_scrub_fallback_at"] = time.time()
    _runtime_status["last_frontier_scrub_fallback_reason"] = reason
