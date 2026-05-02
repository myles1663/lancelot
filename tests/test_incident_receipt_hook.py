from __future__ import annotations

from types import SimpleNamespace

from src.incidents import receipt_hook


def _reset_hook() -> None:
    receipt_hook._enabled = False
    receipt_hook._data_dir = None
    receipt_hook._trigger_engine = None
    receipt_hook._store = None


def test_configure_disabled_clears_runtime_state() -> None:
    receipt_hook._trigger_engine = object()
    receipt_hook._store = object()

    receipt_hook.configure(False, "data")

    assert receipt_hook._enabled is False
    assert receipt_hook._trigger_engine is None
    assert receipt_hook._store is None


def test_on_receipt_returns_when_hook_not_ready() -> None:
    _reset_hook()

    receipt_hook.on_receipt_for_incidents({"action_type": "tool_run"})


def test_evaluate_ignores_incident_lifecycle_receipts() -> None:
    _reset_hook()
    receipt_hook._trigger_engine = SimpleNamespace(evaluate=lambda receipt: (_ for _ in ()).throw(AssertionError()))
    receipt_hook._store = object()

    receipt_hook._evaluate_receipt({"action_type": "incident_opened"})
    receipt_hook._evaluate_receipt({"action_type": "playbook_updated"})


def test_evaluate_returns_when_no_trigger_matches() -> None:
    _reset_hook()
    receipt_hook._trigger_engine = SimpleNamespace(evaluate=lambda receipt: None)
    receipt_hook._store = object()

    receipt_hook._evaluate_receipt({"action_type": "tool_run"})


def test_evaluate_skips_duplicate_dedup_key() -> None:
    _reset_hook()
    incident = SimpleNamespace(
        dedup_key="tool_run:r-1",
        playbook_name="availability",
        trigger_receipt_id="r-1",
    )
    receipt_hook._trigger_engine = SimpleNamespace(
        triggers=[SimpleNamespace(playbook="availability", dedup_window_seconds=60)],
        evaluate=lambda receipt: incident,
    )
    receipt_hook._store = SimpleNamespace(
        find_by_dedup_key=lambda key, window_seconds: "inc-1",
        find_by_trigger_receipt=lambda receipt_id: None,
        create=lambda item: (_ for _ in ()).throw(AssertionError()),
    )

    receipt_hook._evaluate_receipt({"action_type": "tool_run"})


def test_evaluate_creates_incident_emits_receipts_and_pages(monkeypatch) -> None:
    _reset_hook()
    emitted = []
    created = []
    updated = []
    incident = SimpleNamespace(
        incident_id="inc-1",
        dedup_key="tool_run:r-1",
        playbook_name="availability",
        trigger_receipt_id="r-1",
        category="availability",
        severity="high",
        paged_at=None,
    )
    receipt_hook._trigger_engine = SimpleNamespace(triggers=[], evaluate=lambda receipt: incident)
    receipt_hook._store = SimpleNamespace(
        find_by_dedup_key=lambda key, window_seconds: None,
        find_by_trigger_receipt=lambda receipt_id: None,
        create=lambda item: created.append(item),
        update=lambda item: updated.append(item),
    )
    monkeypatch.setattr(
        receipt_hook,
        "_emit_incident_receipt",
        lambda action_type, metadata: emitted.append((action_type, metadata)),
    )

    receipt_hook._evaluate_receipt({"action_type": "tool_run"})

    assert created == [incident]
    assert updated == [incident]
    assert emitted[0][0] == "incident_opened"
    assert emitted[1] == (
        "incident_paged",
        {
            "incident_id": "inc-1",
            "paging_channel": "webhook",
            "escalation_number": 1,
            "severity": "high",
        },
    )
    assert incident.paged_at is not None
