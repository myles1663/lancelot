"""Legacy compatibility shim for the retired text-file receipt helper.

The production receipt system lives in ``src.shared.receipts`` and persists
immutable receipts in SQLite. This module remains only so stale imports fail
loudly with a clear migration path instead of silently generating fake receipt
files that look production-like.
"""

from __future__ import annotations


class LegacyReceiptServiceError(RuntimeError):
    """Raised when deprecated receipt_service.py is used."""


class ReceiptService:
    def __init__(self, *args, **kwargs):
        raise LegacyReceiptServiceError(
            "src.shared.receipt_service is retired. Use src.shared.receipts.ReceiptService "
            "or src.shared.receipts.get_receipt_service for the immutable SQLite receipt log."
        )
