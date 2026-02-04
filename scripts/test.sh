#!/usr/bin/env bash
# ===========================================================================
# Lancelot — Test Runner
# ===========================================================================
# Usage:
#   ./scripts/test.sh          Run unit tests only (no integration)
#   ./scripts/test.sh --all    Run unit + integration tests
#   ./scripts/test.sh --int    Run integration tests only
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

case "${1:-}" in
    --all)
        echo "=== Running ALL tests (unit + integration) ==="
        python -m pytest -q
        ;;
    --int)
        echo "=== Running integration tests only ==="
        python -m pytest -q -m integration
        ;;
    *)
        echo "=== Running unit tests (skipping integration) ==="
        python -m pytest -q -m "not integration"
        ;;
esac
