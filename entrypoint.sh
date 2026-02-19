#!/bin/bash
# Lancelot container entrypoint — ensure data directories are writable
# before starting the application. Runs as root, drops to lancelot user.

set -e

# Ensure data and workspace directories exist and are writable
for dir in /home/lancelot/data /home/lancelot/workspace; do
    mkdir -p "$dir"
    chown -R lancelot:lancelot "$dir" 2>/dev/null || true
    chmod -R u+rwX "$dir" 2>/dev/null || true
done

# Drop to lancelot user and execute the CMD
exec gosu lancelot "$@"
