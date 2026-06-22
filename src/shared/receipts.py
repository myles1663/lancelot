"""Compatibility facade for canonical receipt APIs.

The implementation is split across focused receipt modules while this public
module preserves existing import paths.
"""

try:
    from . import receipts_service as _receipts_service
    from .receipts_action_types import ActionType
    from .receipts_models import (
        CognitionTier,
        ImmutableReceiptError,
        Receipt,
        ReceiptIntegrityKeyError,
        ReceiptStatus,
    )
    from .receipts_service import (
        ReceiptService,
        create_finalized_receipt,
        create_receipt,
        get_receipt_service,
    )
except ImportError:  # pragma: no cover - legacy top-level import path
    import receipts_service as _receipts_service
    from receipts_action_types import ActionType
    from receipts_models import (
        CognitionTier,
        ImmutableReceiptError,
        Receipt,
        ReceiptIntegrityKeyError,
        ReceiptStatus,
    )
    from receipts_service import (
        ReceiptService,
        create_finalized_receipt,
        create_receipt,
        get_receipt_service,
    )

import sys
import types

__all__ = [
    "ActionType",
    "CognitionTier",
    "ImmutableReceiptError",
    "Receipt",
    "ReceiptIntegrityKeyError",
    "ReceiptService",
    "ReceiptStatus",
    "create_finalized_receipt",
    "create_receipt",
    "get_receipt_service",
]


class _ReceiptFacadeModule(types.ModuleType):
    """Proxy legacy singleton test hooks to the split service module."""

    def __getattr__(self, name):
        if name in {"_service_instance", "_service_lock"}:
            return getattr(_receipts_service, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in {"_service_instance", "_service_lock"}:
            setattr(_receipts_service, name, value)
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ReceiptFacadeModule
