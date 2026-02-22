# Production Hardening Guide

Operational security guidance for deploying Project Lancelot in production environments.

**Applies to:** Lancelot v7.4 (v0.2.25+)
**Author:** Myles Russell Hamilton

---

## Table of Contents

1. [Encryption at Rest](#1-encryption-at-rest)
2. [Vault Key Management](#2-vault-key-management)
3. [Host Execution Flag](#3-host-execution-flag)
4. [Pre-Deployment Checklist](#4-pre-deployment-checklist)

---

## 1. Encryption at Rest

**Security Finding:** F-006 — Unencrypted Data at Rest

### What's at risk

While Lancelot's credential vault uses Fernet encryption (AES-128-CBC + HMAC-SHA256), the following data stores are **not** encrypted at the application layer:

| Data Store | Contents | Location |
|-----------|----------|----------|
| `scheduler.sqlite` | Job definitions, execution history | `lancelot_data` volume |
| `memory.sqlite` | Tiered memory items | `lancelot_data` volume |
| `chat_log.json` | Full conversation history | `lancelot_data` volume |
| `audit.log` | Security event log (hash-chained) | `lancelot_data` volume |
| `receipts/` | Action receipts with inputs/outputs | `lancelot_data` volume |

### Recommendation: Volume-level encryption

Encrypt the host filesystem where Docker volumes are stored. This provides transparent encryption for **all** data with zero application code changes.

#### Windows (BitLocker)

1. **Check if BitLocker is already enabled** (most Windows 11 Pro machines have it on by default):
   ```powershell
   manage-bde -status C:
   ```
   Look for `Protection Status: Protection On` and `Encryption Method: XTS-AES 128/256`.

2. **If not enabled**, turn it on:
   - Open **Settings > Privacy & Security > Device Encryption**
   - Or via PowerShell (administrator):
     ```powershell
     Enable-BitLocker -MountPoint "C:" -EncryptionMethod XtsAes256 -UsedSpaceOnly
     ```

3. **Verify Docker volumes are on the encrypted drive:**
   ```powershell
   docker volume inspect lancelot_data --format '{{ .Mountpoint }}'
   ```
   The mountpoint should be on the BitLocker-encrypted drive (typically `C:`).

#### Linux (LUKS/dm-crypt)

1. **Option A — Full disk encryption (recommended):** Most Linux distributions offer LUKS encryption during OS installation. If your server was installed with full disk encryption, you're already covered.

2. **Option B — Encrypt the Docker data directory:**
   ```bash
   # Create an encrypted partition for Docker data
   sudo cryptsetup luksFormat /dev/sdX
   sudo cryptsetup open /dev/sdX docker-crypt
   sudo mkfs.ext4 /dev/mapper/docker-crypt
   sudo mount /dev/mapper/docker-crypt /var/lib/docker

   # Restart Docker
   sudo systemctl restart docker
   ```

3. **Verify:**
   ```bash
   lsblk -f | grep crypt
   ```

### What this covers

Volume-level encryption protects against:
- Physical theft of the disk/server
- Unauthorized access to the filesystem when the OS is shut down
- Forensic recovery of deleted data from the volume

It does **not** protect against:
- Attacks while the system is running and the volume is mounted (use application-layer access controls for this — Lancelot's auth, write gates, and vault handle runtime protection)

---

## 2. Vault Key Management

**Security Finding:** F-013 — Vault Key Auto-Generation

### How the vault key works

The credential vault uses Fernet symmetric encryption. The encryption key is resolved in this order:

1. **`LANCELOT_VAULT_KEY` environment variable** (preferred)
2. **Auto-generated ephemeral key** (fallback — development only)

When no key is set, the vault generates a random key at startup and logs a warning:
```
LANCELOT_VAULT_KEY not set — generated ephemeral key.
Credentials will NOT survive restarts without setting this env var.
```

### Production requirements

**Always set `LANCELOT_VAULT_KEY` explicitly in production.**

#### Generating a vault key

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This produces a base64-encoded 32-byte key, e.g.:
```
dGhpcyBpcyBhIHNhbXBsZSBrZXkgZm9yIGRvY3M=
```

#### Setting the key

Add it to your `.env` file:
```env
LANCELOT_VAULT_KEY=your-generated-key-here
```

The `.env` file is loaded by Docker Compose via the `env_file` directive and is `.gitignored` — it has never been committed to the repository.

#### Key backup and recovery

- **Back up the key separately from the encrypted data.** If the key is lost, all vault-encrypted credentials are **permanently unrecoverable**.
- Store the key in a password manager, hardware security module (HSM), or cloud secrets manager (e.g., AWS Secrets Manager, Azure Key Vault, GCP Secret Manager).
- Consider rotating the key periodically. To rotate:
  1. Export all credentials from the vault
  2. Set the new `LANCELOT_VAULT_KEY`
  3. Restart the service — existing encrypted data will fail to decrypt
  4. Re-import credentials (they'll be encrypted with the new key)

#### What auto-generation means

If `LANCELOT_VAULT_KEY` is **not** set:
- A random key is generated each time the service starts
- Credentials stored during one session will be **unreadable** after a restart
- This is acceptable for development/testing but **never for production**

### Verification

Check that the vault key is set:
```bash
docker exec lancelot_core printenv LANCELOT_VAULT_KEY | wc -c
```
Should return a value > 1 (the key length). If it returns 0 or 1, the key is not set.

---

## 3. Host Execution Flag

**Security Finding:** F-014 — `FEATURE_TOOLS_HOST_EXECUTION` Flag

### What it does

The `FEATURE_TOOLS_HOST_EXECUTION` feature flag (default: `false`) controls whether Lancelot can execute commands **directly on the host OS** instead of inside Docker sandbox containers.

When **disabled** (default):
- All commands run in isolated Docker containers with memory limits, CPU limits, no network, and output bounding
- The PolicyEngine's command denylist and path traversal detection still apply
- Container escape requires exploiting Docker itself

When **enabled**:
- Commands run directly on the host machine via `subprocess`
- No container isolation, no memory/CPU limits, no network isolation
- The PolicyEngine's command denylist and workspace boundary enforcement still apply, but these are the **only** protections
- A successful prompt injection or skill exploit could execute arbitrary commands on the host

### Production requirement

**Never enable `FEATURE_TOOLS_HOST_EXECUTION` in production.**

Verify it is not set in your `.env` file:
```bash
grep FEATURE_TOOLS_HOST_EXECUTION .env
```

If found, ensure it is either:
- Removed entirely (defaults to `false`)
- Explicitly set to `false`

### When it's acceptable

This flag exists for two specific scenarios:
1. **Development without Docker:** When developing on a machine where Docker is not available, this allows basic tool execution for testing
2. **Debugging:** When diagnosing sandbox issues, temporarily enabling host execution can help isolate whether a problem is Docker-related

In both cases, disable it immediately after use.

### Verification

```bash
docker exec lancelot_core python3 -c "from src.core.feature_flags import FEATURE_TOOLS_HOST_EXECUTION; print(FEATURE_TOOLS_HOST_EXECUTION)"
```

Should print `False`.

---

## 4. Pre-Deployment Checklist

Run through this checklist before deploying Lancelot to any environment beyond local development:

### Authentication

- [ ] `LANCELOT_API_TOKEN` is set to a strong random value (32+ bytes)
- [ ] `LANCELOT_OWNER_TOKEN` is set to a different strong random value
- [ ] `LANCELOT_DEV_MODE` is **not** set (or set to `false`)

### Encryption

- [ ] `LANCELOT_VAULT_KEY` is explicitly set in `.env`
- [ ] Vault key is backed up in a secure location separate from the server
- [ ] Host filesystem uses full disk encryption (BitLocker/LUKS)

### Feature Flags

- [ ] `FEATURE_TOOLS_HOST_EXECUTION` is **not** set (defaults to `false`)
- [ ] Review all feature flags via the Kill Switches UI or API

### Network

- [ ] Review `config/network_allowlist.yaml` — remove any domains not needed
- [ ] Ensure port 8000 is not exposed to the public internet without a reverse proxy
- [ ] Consider adding a reverse proxy (nginx/Caddy) with TLS termination

### Docker

- [ ] Docker socket proxy is running (`docker ps | grep lancelot_docker_proxy`)
- [ ] `lancelot-core` does **not** have the Docker socket mounted directly
- [ ] Docker images are up to date

### Monitoring

- [ ] Check health endpoint: `curl http://localhost:8000/health`
- [ ] Verify audit log is being written: `docker exec lancelot_core tail /home/lancelot/data/audit.log`
- [ ] Review CognitionGovernor limits are appropriate for your usage

---

*This guide is part of the Project Lancelot security documentation. For the full security assessment, see the [Security Whitepaper](security-whitepaper.md).*
