#!/usr/bin/env python3
"""
migrate_secrets_to_vault.py — One-time migration of .env secrets into the vault.

Reads plaintext secrets from .env, stores each into the CredentialVault
(hashing the War Room password), and optionally strips migrated lines
from .env with --remove-from-env.

Usage:
    python scripts/migrate_secrets_to_vault.py                    # migrate only
    python scripts/migrate_secrets_to_vault.py --remove-from-env  # migrate + strip .env
    python scripts/migrate_secrets_to_vault.py --dry-run          # preview only
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

# Add src paths so vault import works
_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "src" / "connectors"))
sys.path.insert(0, str(_repo / "src" / "core"))
sys.path.insert(0, str(_repo / "src"))

# Map of env var → (vault_key, should_hash)
SECRETS = {
    "LANCELOT_API_TOKEN":        ("system.api_token", False),
    "LANCELOT_OWNER_TOKEN":      ("system.owner_token", False),
    "WARROOM_USERNAME":           ("system.warroom_username", False),
    "WARROOM_PASSWORD":           ("system.warroom_password_hash", True),
    "LANCELOT_TELEGRAM_TOKEN":    ("system.telegram_token", False),
    "LANCELOT_TELEGRAM_CHAT_ID":  ("system.telegram_chat_id", False),
}


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env file into key=value pairs."""
    values = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
        if match:
            key, val = match.group(1), match.group(2)
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            values[key] = val
    return values


def _strip_keys_from_env(env_path: Path, keys: set[str]) -> int:
    """Remove lines for given keys from .env. Returns count removed."""
    if not env_path.exists():
        return 0
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        match = re.match(r'^([A-Z_][A-Z0-9_]*)=', stripped)
        if match and match.group(1) in keys:
            removed += 1
        else:
            kept.append(line)
    env_path.write_text("".join(kept), encoding="utf-8")
    return removed


def main():
    parser = argparse.ArgumentParser(description="Migrate .env secrets to vault")
    parser.add_argument("--env-file", default=str(_repo / ".env"),
                        help="Path to .env file (default: repo root .env)")
    parser.add_argument("--remove-from-env", action="store_true",
                        help="Strip migrated secrets from .env after migration")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would happen without making changes")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    env_values = _read_env_file(env_path)

    # LANCELOT_VAULT_KEY must be available to initialize the vault
    vault_key = env_values.get("LANCELOT_VAULT_KEY") or os.environ.get("LANCELOT_VAULT_KEY")
    if not vault_key:
        print("ERROR: LANCELOT_VAULT_KEY not found in .env or environment.")
        print("       Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        sys.exit(1)

    # Set the vault key so CredentialVault can use it
    os.environ["LANCELOT_VAULT_KEY"] = vault_key

    if args.dry_run:
        print("=== DRY RUN — no changes will be made ===\n")

    # Import vault after setting env
    from connectors.vault import CredentialVault
    vault = CredentialVault(config_path=str(_repo / "config" / "vault.yaml"))

    migrated_keys = set()
    for env_key, (vault_key_name, should_hash) in SECRETS.items():
        raw_value = env_values.get(env_key)
        if not raw_value:
            print(f"  SKIP  {env_key} — not in .env")
            continue

        if vault.exists(vault_key_name):
            print(f"  EXISTS {env_key} → {vault_key_name} (already in vault)")
            migrated_keys.add(env_key)
            continue

        if should_hash:
            store_value = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
            label = "(SHA-256 hashed)"
        else:
            store_value = raw_value
            label = ""

        if args.dry_run:
            print(f"  WOULD MIGRATE {env_key} → {vault_key_name} {label}")
        else:
            vault.store(vault_key_name, store_value, type="system_secret")
            print(f"  MIGRATED {env_key} → {vault_key_name} {label}")
        migrated_keys.add(env_key)

    if args.remove_from_env and migrated_keys and not args.dry_run:
        removed = _strip_keys_from_env(env_path, migrated_keys)
        print(f"\nStripped {removed} secret lines from {env_path}")
    elif args.remove_from_env and args.dry_run:
        print(f"\nWOULD strip {len(migrated_keys)} secret lines from {env_path}")

    print(f"\nDone. {len(migrated_keys)} secrets processed.")


if __name__ == "__main__":
    main()
