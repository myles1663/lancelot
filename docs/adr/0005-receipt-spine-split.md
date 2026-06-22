# ADR 0005: Receipt Spine Split

## Status

Accepted.

## Context

Receipts are Lancelot's canonical proof system. The current hardening package
requires receipt responsibilities to become easier to inspect without breaking
existing imports or changing behavior during the split.

## Decision

Split the receipt spine behind a stable compatibility facade. Receipt models,
store, integrity, migrations, and service behavior may move into focused modules,
but existing public imports must remain compatible unless a ticket explicitly
migrates them.

CORE-B2 implements the first split with these ownership boundaries:

- `src/shared/receipts.py` remains the compatibility facade.
- `src/shared/receipts_action_types.py` owns `ActionType`.
- `src/shared/receipts_models.py` owns public receipt models and receipt errors.
- `src/shared/receipts_migrations.py` owns schema creation and migration helpers.
- `src/shared/receipts_integrity.py` owns hash-chain and signing helpers.
- `src/shared/receipts_store.py` owns SQLite connection and row mapping helpers.
- `src/shared/receipts_service.py` owns `ReceiptService` and receipt factory helpers.

UAB receipt metadata is defined during UAB hardening. CORE-B3 wires final
canonical UAB success, denial, and failure receipt emission through the split
receipt service boundary. Embedded Core and Tool Fabric UAB provider creation
must go through `src/core/uab_runtime_adapter.py` or explicitly inject a
`ReceiptService`; this keeps UAB standalone-maintainable while preserving
canonical proof in the Lancelot runtime. Provider helper paths that authorize
governed non-`act()` actions must use the same canonical receipt finalization
path as `act()`, including deterministic fallback failure reasons when a daemon
returns `success=false` without an error string.

## Consequences

- Refactor tickets must preserve behavior through contract tests before moving
  implementation details.
- UAB-A7 defines metadata and compatibility shape; CORE-B3 owns and implements
  final canonical receipt integration through `ReceiptService`.
- Local UAB audit cannot be used as a substitute for canonical receipt proof.
- Canonical UAB receipts omit the compatibility deferral marker; only the UAB-A7
  compatibility adapter carries `canonical_receipt_deferred_to=CORE-B3`.
- Import-boundary tests should prevent direct coupling from returning.
- Tool Fabric UAB registration must not construct a receiptless provider for
  governed APP_CONTROL actions.
- Core's UAB runtime adapter may inject a canonical receipt service only when
  the embedded provider constructor exposes that boundary. This preserves UAB
  standalone/test-double compatibility while the Lancelot-included provider
  continues to receive canonical receipt persistence.
