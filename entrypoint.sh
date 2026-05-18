#!/bin/bash
# Lancelot container entrypoint — ensure data directories are writable
# before starting the application. Runs as root, drops to lancelot user.

set -e

# Ensure data and workspace directories exist and are writable
for dir in /home/lancelot/data /home/lancelot/workspace /home/lancelot/.codex; do
    mkdir -p "$dir"
    chown -R lancelot:lancelot "$dir" 2>/dev/null || true
    chmod -R u+rwX "$dir" 2>/dev/null || true
done

# Connector provider selection is persisted to config/connectors.yaml from the
# War Room. Keep this operator-facing config writable after image build or
# hot-copy updates while leaving the rest of the application tree immutable.
mkdir -p /home/lancelot/app/config
touch /home/lancelot/app/config/connectors.yaml
chown lancelot:lancelot /home/lancelot/app/config /home/lancelot/app/config/connectors.yaml 2>/dev/null || true
chmod u+rwX /home/lancelot/app/config /home/lancelot/app/config/connectors.yaml 2>/dev/null || true

# Drop to lancelot user and execute the CMD
exec gosu lancelot "$@"
