# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
UAB Receipt System — App Control Receipts + Session Ledger
==========================================================

Specialized receipt types for tracking UAB (Universal App Bridge) actions
on desktop applications. Every UAB action — even read-only enumerate —
produces an auditable receipt.

Receipt Types:
- AppControlReceipt: Individual action receipt (what was done, to which
  element, in which app, with before/after state)
- AppSessionEntry: Per-app session summary that rolls up all actions

Design Principles:
- Every action gets a receipt (Receipts are Truth)
- Users must be able to reconstruct exactly what Lancelot did on their machine
- Read-only vs mutating actions are clearly distinguished
- Multi-step workflows (chains) are linked via chain_id
- Sensitive app detection auto-escalates risk level
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.contracts import RiskLevel

logger = logging.getLogger(__name__)


# =============================================================================
# AppControlReceipt — Individual Action Receipt
# =============================================================================


@dataclass
class AppControlReceipt:
    """
    Receipt for a single UAB action on a desktop application.

    Captures full audit trail: which app, which element, what action,
    what the state was before and after, and governance context.
    """

    # ── Identity ──────────────────────────────────────────────────
    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: Optional[str] = None
    parent_receipt_id: Optional[str] = None

    # ── App Context ───────────────────────────────────────────────
    app_name: str = ""
    app_pid: int = 0
    app_framework: Optional[str] = None
    window_title: Optional[str] = None
    connection_method: Optional[str] = None  # cdp, moc, gir, clr, dart_vm, jvm

    # ── Action ────────────────────────────────────────────────────
    action_type: str = ""  # detect, connect, enumerate, query, act, state
    mutating: bool = False
    risk_level: str = RiskLevel.LOW.value

    # Element targeted (for act/query operations)
    element_id: Optional[str] = None
    element_type: Optional[str] = None  # button, textfield, menu, etc.
    element_label: Optional[str] = None
    element_path: Optional[str] = None  # UI tree path

    # Action details
    action_performed: Optional[str] = None  # click, type, select, etc.
    action_params: Dict[str, Any] = field(default_factory=dict)

    # ── State Snapshots ───────────────────────────────────────────
    pre_state: Dict[str, Any] = field(default_factory=dict)
    post_state: Dict[str, Any] = field(default_factory=dict)

    # ── Query/Enumerate Results Summary ───────────────────────────
    elements_returned: int = 0
    query_selector: Optional[Dict[str, Any]] = None

    # ── Chain Context (multi-step workflows) ──────────────────────
    chain_id: Optional[str] = None
    chain_name: Optional[str] = None
    step_index: Optional[int] = None
    total_steps: Optional[int] = None

    # ── Verification ──────────────────────────────────────────────
    state_changed: Optional[bool] = None
    expected_outcome: Optional[str] = None
    actual_outcome: Optional[str] = None

    # ── Governance ────────────────────────────────────────────────
    governance_gate: str = "autonomous"  # autonomous | required_approval
    approval_id: Optional[str] = None

    # ── Policy ────────────────────────────────────────────────────
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)

    # ── Timing ────────────────────────────────────────────────────
    duration_ms: Optional[int] = None

    # ── Result ────────────────────────────────────────────────────
    success: bool = True
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppControlReceipt":
        """Deserialize from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def fail(self, message: str) -> None:
        """Mark receipt as failed."""
        self.success = False
        self.error_message = message


# =============================================================================
# AppSessionEntry — Per-App Session Summary
# =============================================================================


@dataclass
class AppSessionEntry:
    """
    Summary of all UAB interactions with a specific application during a session.

    Rolls up individual AppControlReceipts into a quick overview:
    "Lancelot accessed Slack for 12 minutes, performed 6 mutating actions."
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    app_name: str = ""
    app_pid: int = 0
    app_framework: Optional[str] = None
    connection_method: Optional[str] = None

    connected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    disconnected_at: Optional[str] = None

    # Counters
    total_actions: int = 0
    mutating_actions: int = 0
    read_only_actions: int = 0

    # Action breakdown
    action_summary: Dict[str, int] = field(default_factory=dict)

    # Elements touched (unique element IDs that were acted upon)
    elements_touched: List[str] = field(default_factory=list)

    # Highest risk action performed
    max_risk_level: str = RiskLevel.LOW.value

    # Links to individual receipts
    receipt_ids: List[str] = field(default_factory=list)

    def record_action(
        self,
        receipt: AppControlReceipt,
    ) -> None:
        """Record an action from an AppControlReceipt into this session."""
        self.total_actions += 1
        self.receipt_ids.append(receipt.receipt_id)

        if receipt.mutating:
            self.mutating_actions += 1
        else:
            self.read_only_actions += 1

        # Update action summary
        action_key = receipt.action_performed or receipt.action_type
        self.action_summary[action_key] = self.action_summary.get(action_key, 0) + 1

        # Track elements touched
        if receipt.element_id and receipt.element_id not in self.elements_touched:
            self.elements_touched.append(receipt.element_id)

        # Escalate max risk
        risk_order = {"low": 0, "medium": 1, "high": 2}
        if risk_order.get(receipt.risk_level, 0) > risk_order.get(self.max_risk_level, 0):
            self.max_risk_level = receipt.risk_level

    def close(self) -> None:
        """Mark session as disconnected."""
        self.disconnected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppSessionEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# AppControlReceiptStore — Persistence
# =============================================================================


class AppControlReceiptStore:
    """
    Stores AppControlReceipts and AppSessionEntries to disk.

    Receipts go into: {data_dir}/receipts/uab/
    Sessions go into: {data_dir}/receipts/uab/sessions/
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self._receipts_dir = Path(data_dir) / "receipts" / "uab"
        self._sessions_dir = self._receipts_dir / "sessions"
        self._receipts_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # In-memory session tracking
        self._active_sessions: Dict[int, AppSessionEntry] = {}  # pid -> session

        # Recent receipts cache (for War Room queries)
        self._recent_receipts: List[AppControlReceipt] = []
        self._max_recent = 500

    def store_receipt(self, receipt: AppControlReceipt) -> None:
        """Store a receipt to disk and update session tracking."""
        # Write receipt to disk
        receipt_path = self._receipts_dir / f"{receipt.receipt_id}.json"
        try:
            with open(receipt_path, "w") as f:
                json.dump(receipt.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning("Failed to store UAB receipt %s: %s", receipt.receipt_id, e)

        # Update in-memory cache
        self._recent_receipts.append(receipt)
        if len(self._recent_receipts) > self._max_recent:
            self._recent_receipts = self._recent_receipts[-self._max_recent:]

        # Update active session
        if receipt.app_pid in self._active_sessions:
            self._active_sessions[receipt.app_pid].record_action(receipt)

    def start_session(
        self, pid: int, app_name: str, framework: Optional[str] = None,
        connection_method: Optional[str] = None,
    ) -> AppSessionEntry:
        """Start tracking a new app session."""
        session = AppSessionEntry(
            app_name=app_name,
            app_pid=pid,
            app_framework=framework,
            connection_method=connection_method,
        )
        self._active_sessions[pid] = session
        return session

    def end_session(self, pid: int) -> Optional[AppSessionEntry]:
        """End an app session and persist the summary."""
        session = self._active_sessions.pop(pid, None)
        if session is None:
            return None

        session.close()

        # Write session summary to disk
        session_path = self._sessions_dir / f"{session.session_id}.json"
        try:
            with open(session_path, "w") as f:
                json.dump(session.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning("Failed to store UAB session %s: %s", session.session_id, e)

        return session

    def get_active_sessions(self) -> Dict[int, AppSessionEntry]:
        """Return all active (connected) sessions."""
        return dict(self._active_sessions)

    def get_recent_receipts(
        self,
        limit: int = 50,
        app_name: Optional[str] = None,
        mutating_only: bool = False,
        action_type: Optional[str] = None,
    ) -> List[AppControlReceipt]:
        """Query recent receipts with optional filters."""
        results = self._recent_receipts

        if app_name:
            results = [r for r in results if r.app_name.lower() == app_name.lower()]

        if mutating_only:
            results = [r for r in results if r.mutating]

        if action_type:
            results = [r for r in results if r.action_type == action_type or r.action_performed == action_type]

        return list(reversed(results[-limit:]))

    def get_receipts_for_chain(self, chain_id: str) -> List[AppControlReceipt]:
        """Get all receipts belonging to an action chain."""
        return [r for r in self._recent_receipts if r.chain_id == chain_id]

    def get_session_summaries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent session summaries (active + persisted)."""
        summaries = []

        # Active sessions first
        for session in self._active_sessions.values():
            d = session.to_dict()
            d["active"] = True
            summaries.append(d)

        # Load recent persisted sessions
        try:
            session_files = sorted(
                self._sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for session_file in session_files[:limit]:
                with open(session_file) as f:
                    d = json.load(f)
                    d["active"] = False
                    summaries.append(d)
        except Exception as e:
            logger.warning("Failed to load UAB session summaries: %s", e)

        return summaries[:limit]


# =============================================================================
# Module-level singleton
# =============================================================================

_store: Optional[AppControlReceiptStore] = None


def get_uab_receipt_store(data_dir: str = "/home/lancelot/data") -> AppControlReceiptStore:
    """Get or create the UAB receipt store singleton."""
    global _store
    if _store is None:
        _store = AppControlReceiptStore(data_dir=data_dir)
    return _store


def reset_uab_receipt_store() -> None:
    """Reset the singleton (for tests)."""
    global _store
    _store = None


# =============================================================================
# Receipt Builder Helpers
# =============================================================================


def create_app_control_receipt(
    action_type: str,
    app_name: str = "",
    app_pid: int = 0,
    app_framework: Optional[str] = None,
    session_id: Optional[str] = None,
    **kwargs,
) -> AppControlReceipt:
    """Create a new AppControlReceipt with standard fields populated.

    Auto-computes ``mutating`` and ``risk_level`` from the action details.
    Callers may override them via kwargs, but the auto-computed values are
    the default.
    """
    from src.tools.providers.uab_bridge import classify_action_risk

    # Pop fields we compute so they don't conflict with **kwargs
    caller_mutating = kwargs.pop("mutating", None)
    caller_risk = kwargs.pop("risk_level", None)

    auto_mutating = action_type in ("act",) or kwargs.get("action_performed", "") in (
        # UI interaction
        "click", "doubleclick", "rightclick", "type", "clear",
        "select", "scroll", "focus", "hover", "expand", "collapse",
        "check", "uncheck", "toggle", "keypress", "hotkey",
        # Window management
        "close", "invoke", "minimize", "maximize", "restore",
        "move", "resize",
        # Context menu
        "contextmenu",
        # Office write operations
        "writeCell", "writeRange",
        "composeEmail", "sendEmail",
    )

    action_name = kwargs.get("action_performed", action_type)
    auto_risk = classify_action_risk(action_name, app_name)

    return AppControlReceipt(
        action_type=action_type,
        app_name=app_name,
        app_pid=app_pid,
        app_framework=app_framework,
        mutating=caller_mutating if caller_mutating is not None else auto_mutating,
        risk_level=caller_risk if caller_risk is not None else auto_risk.value,
        session_id=session_id,
        **kwargs,
    )
