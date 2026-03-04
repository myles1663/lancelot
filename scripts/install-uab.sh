#!/usr/bin/env bash
# ── install-uab.sh ──────────────────────────────────────────────────
# Installs and builds the Universal App Bridge (UAB) daemon.
#
# UAB is licensed under BSL 1.1 (see packages/uab/LICENSE).
# It ships with Lancelot and is free for personal use and the
# Lancelot ecosystem. Commercial use requires a separate license.
#
# Usage:
#   ./scripts/install-uab.sh          # Install + build
#   ./scripts/install-uab.sh --start  # Install + build + start daemon
#
# Requirements:
#   - Node.js >= 18.0.0
#   - npm
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UAB_DIR="$ROOT_DIR/packages/uab"

echo "================================================"
echo "  Universal App Bridge (UAB) — Installer"
echo "  Licensed under BSL 1.1"
echo "================================================"
echo ""

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js is required but not installed."
    echo "Please install Node.js >= 18.0.0 from https://nodejs.org/"
    exit 1
fi

NODE_VERSION=$(node -v | sed 's/v//')
NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "ERROR: Node.js >= 18.0.0 required (found v$NODE_VERSION)"
    exit 1
fi
echo "Node.js: v$NODE_VERSION"

# Check npm
if ! command -v npm &> /dev/null; then
    echo "ERROR: npm is required but not installed."
    exit 1
fi
echo "npm: $(npm -v)"
echo ""

# Install dependencies
echo "Installing UAB dependencies..."
cd "$UAB_DIR"
npm install --production=false
echo ""

# Build
echo "Building UAB..."
npm run build
echo ""

echo "UAB installed successfully!"
echo ""
echo "To start the daemon:"
echo "  cd $UAB_DIR && node dist/index.js daemon"
echo ""
echo "To detect apps:"
echo "  cd $UAB_DIR && node dist/index.js detect"
echo ""

# Optional: start daemon
if [ "${1:-}" = "--start" ]; then
    echo "Starting UAB daemon on port 7900..."
    node dist/index.js daemon
fi
