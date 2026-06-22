"""Compatibility contracts for the split receipt facade."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_package_receipts_facade_preserves_public_symbols(tmp_path):
    from src.shared import receipts
    from src.shared.receipts_action_types import ActionType
    from src.shared.receipts_models import Receipt, ReceiptStatus
    from src.shared.receipts_service import ReceiptService

    assert receipts.ActionType is ActionType
    assert receipts.Receipt is Receipt
    assert receipts.ReceiptService is ReceiptService

    service = receipts.ReceiptService(str(tmp_path / "package"))
    try:
        pending = receipts.create_receipt(
            receipts.ActionType.SYSTEM,
            "package_facade",
            {"source": "package"},
            quest_id="core-b2",
        )
        finalized = service.update(
            service.create(pending).complete({"ok": True}, duration_ms=1)
        )
        loaded = service.get(finalized.id)

        assert loaded is not None
        assert loaded.status == ReceiptStatus.SUCCESS.value
        assert loaded.outputs == {"ok": True}
        assert service.validate_integrity_chain(quest_id="core-b2") == []
    finally:
        service.close()


def test_legacy_top_level_receipts_import_preserves_public_symbols(tmp_path, monkeypatch):
    shared_path = str(Path.cwd() / "src" / "shared")
    monkeypatch.syspath_prepend(shared_path)

    legacy_receipts = importlib.import_module("receipts")

    for symbol in legacy_receipts.__all__:
        assert hasattr(legacy_receipts, symbol)

    service = legacy_receipts.ReceiptService(str(tmp_path / "legacy"))
    try:
        pending = legacy_receipts.create_receipt(
            legacy_receipts.ActionType.SYSTEM,
            "legacy_facade",
            {"source": "legacy"},
            quest_id="core-b2",
        )
        staged = service.create(pending)
        finalized = service.update(staged.complete({"ok": True}, duration_ms=1))
        loaded = service.get(finalized.id)

        assert loaded is not None
        assert loaded.id == finalized.id
        assert loaded.integrity_hash
        assert service.validate_integrity_chain(quest_id="core-b2") == []
    finally:
        service.close()

    for module_name in [
        "receipts",
        "receipts_action_types",
        "receipts_models",
        "receipts_service",
    ]:
        sys.modules.pop(module_name, None)


def test_receipts_facade_preserves_legacy_singleton_reset_hook(tmp_path):
    import src.shared.receipts as facade
    from src.shared import receipts_service

    service = facade.get_receipt_service(str(tmp_path / "first"))

    assert facade._service_instance is service
    assert receipts_service._service_instance is service

    with facade._service_lock:
        facade._service_instance.close()
        facade._service_instance = None

    assert receipts_service._service_instance is None

    replacement = facade.get_receipt_service(str(tmp_path / "second"))
    try:
        assert replacement is not service
        assert receipts_service._service_instance is replacement
    finally:
        replacement.close()
        facade._service_instance = None
