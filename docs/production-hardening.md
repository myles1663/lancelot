# Production Hardening Guide

Operational readiness guidance for deploying Lancelot in a controlled enterprise production environment.

This document is not a marketing checklist. It is the operator-facing source of truth for what must be configured, verified, and exercised before you treat an instance as production-ready.

**Applies to:** current private-dev runtime after the enterprise hardening pass

---

## What "Production Ready" Means Here

For Lancelot, "production ready" does not mean "all features enabled."

It means:

- the runtime identity and authentication model are configured intentionally
- secrets survive restart and are recoverable
- operator controls are real and tested
- disabled or degraded subsystems are visible as such
- external peers are treated as hostile unless explicitly governed
- compliance exports are reproducible, attributable, and integrity-verifiable
- rollout is staged and reversible

If any of those are missing, the instance is not production ready even if `/health/ready` is green.

---

## Recommended Deployment Posture

Use this guide for:

- on-prem enterprise deployments
- internal team production environments
- regulated environments where auditability matters
- federated or multi-agent deployments with bounded specialties

Do not treat Lancelot as an internet-exposed SaaS app by default.

Recommended posture:

- private network or VPN access only
- reverse proxy and TLS in front of the War Room/API
- OIDC for human operators when available
- local auth only for tightly controlled internal deployments
- explicit enablement for Federation, A2A, Time-Travel, and other advanced subsystems

---

## 1. Identity and Access

### War Room authentication mode

Choose exactly one:

- `LANCELOT_AUTH_PROVIDER=local`
- `LANCELOT_AUTH_PROVIDER=oidc`

#### Local auth

Required:

```env
LANCELOT_AUTH_PROVIDER=local
WARROOM_USERNAME=choose-an-operator-username
WARROOM_PASSWORD=choose-a-strong-password
WARROOM_PASSWORD_RESET_CODE=store-this-reset-code-securely
```

Use local auth only when:

- the instance is private
- the operator set is small
- you do not have an enterprise IdP wired yet

#### OIDC auth

Required:

```env
LANCELOT_AUTH_PROVIDER=oidc
OIDC_ISSUER_URL=https://your-idp.example.com/realms/lancelot
OIDC_CLIENT_ID=lancelot-war-room
OIDC_CLIENT_SECRET=your-client-secret
OIDC_REDIRECT_URI=https://your-lancelot.example.com/auth/oidc/callback
```

Recommended additional controls:

- restrict by IdP groups
- use TLS everywhere
- use short session lifetimes and IdP-controlled reauthentication policy

### Session model

The War Room uses server-backed cookie sessions. Production expectations:

- verify sessions survive restart
- verify logout invalidates the live session
- verify browser access to admin surfaces is blocked without the required capability

Do not rely on bearer tokens in browser URLs or local storage.

---

## 2. Secrets and Persistence

### Vault key

The credential vault must have a stable key in production:

```env
LANCELOT_VAULT_KEY=your-generated-key-here
```

Generate one with:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Requirements:

- set it explicitly
- back it up separately from the server
- document who can recover it
- prefer a Docker secret or external secret manager over a long-lived production `.env` copy

If the vault key is missing, credentials will not survive restart correctly. That is acceptable for development, not for production.

### Vault mismatch response

Treat vault key continuity loss as a fail-closed security event, not as data corruption to be bypassed.

- The runtime now writes non-secret vault metadata at `data/vault/credentials.meta.json` with a stable key id so operators can distinguish a missing key from a key mismatch without decrypting the vault.
- Use War Room Setup or `GET /api/setup/vault/status` to inspect connector-vault health before deleting anything. The status payload reports key source/origin, whether encrypted files exist, and whether the configured key id disagrees with the stored vault metadata.
- If the original key cannot be recovered, use the audited connector-vault reset flow instead of manual file deletion. `POST /api/setup/vault/reset` archives the existing encrypted artifacts under `data/vault/reset_backups/<timestamp>/` and restarts the container to clear stale in-memory state before a clean vault is re-created.
- A reset is not recovery. Archived ciphertext remains unreadable without the original key, so plan to re-enroll connector credentials and OAuth grants after the reset.

### Data-at-rest protection

Lancelot persists operational state, receipts, audit data, sessions, approvals, and control-plane state on disk. Protect the host volume with filesystem or disk encryption.

War Room session state and persisted OIDC pending/exchange data are stored as an encrypted Fernet envelope on disk, with the encryption key kept separately (`LANCELOT_AUTH_STATE_KEY_FILE` or `LANCELOT_AUTH_STATE_ENCRYPTION_KEY`). Treat both the state file and its key material as sensitive runtime assets when designing backups, secret mounts, and host-volume access controls.

Recommended:

- Windows: BitLocker
- Linux: LUKS/dm-crypt or full-disk encryption

This does not replace runtime access control. It protects offline disk compromise.

---

## 3. Runtime Controls You Must Test

These are not optional in production. Test them before go-live.

### Pause / Resume

War Room `Pause Runtime` is now a real persisted control-plane action.

Verify:

1. pause the runtime
2. confirm new `/chat` work is blocked
3. confirm new scheduler dispatch is blocked
4. confirm HIVE intake/spawn is blocked
5. confirm inbound A2A execution is blocked
6. resume the runtime and confirm normal operation returns

### Emergency Stop

War Room `Emergency Stop` is also a real control-plane action.

Verify:

1. trigger emergency stop
2. confirm runtime pause is set
3. confirm active HIVE agents are stopped when present
4. confirm operator resume is required to return to normal operation

### Health and truthful status surfaces

At minimum, verify:

- `/health`
- `/health/ready`
- `/api/federation/status` when federation is enabled
- `/api/a2a/status` when A2A is enabled
- `/api/hive/status` when HIVE is enabled
- `/api/timetravel/status` when Time-Travel is enabled
- `/api/mcp/servers` when MCP is enabled

Production expectation:

- disabled features report disabled
- uninitialized features report not initialized
- broken runtime paths report degraded with reasons
- `/health/ready` includes `startup_validation.ready=true`
- `startup_validation.blocked` and `startup_validation.degraded` are empty

Healthy-looking defaults are not acceptable evidence of readiness.

Startup validation is the first health surface to inspect after deployment. Treat any `blocked` finding as fail-closed misconfiguration. Treat any `degraded` finding as an explicit unavailable lane, not as a warning to ignore.

---

## 4. Tool Execution and Host Safety

### Host execution

Never enable host execution in production:

```env
FEATURE_TOOLS_HOST_EXECUTION=false
```

Verify:

```bash
docker exec lancelot_core python3 -c "from src.core.feature_flags import FEATURE_TOOLS_HOST_EXECUTION; print(FEATURE_TOOLS_HOST_EXECUTION)"
```

Expected result:

```text
False
```

Production tool execution should stay inside the governed sandbox path.

When Host Bridge is enabled for an intentional desktop/host workflow, keep `HOST_AGENT_URL` on `host.docker.internal` or loopback only. The runtime now rejects public host-agent URLs before it opens the control connection, but operators should still treat any deviation from local-only host-agent routing as a misconfiguration.

### Local utility model boundary

`LOCAL_LLM_URL` is also treated as a local control-plane path, not a generic model endpoint. Production expectation:

- keep it on loopback, `host.docker.internal`, or an internal single-label service name such as `local-llm`
- do not point it at a public provider or general-purpose remote hostname
- treat a fail-closed local-only URL error as a configuration issue, not as a transient model outage

### Network egress

Review any configured allowlists and external connectors. Production policy should be:

- minimum required outbound domains only
- no broad wildcard egress without explicit justification
- connectors enabled only when actually used

---

## 5. Federation Readiness

Only apply this section when federation is enabled:

```env
FEATURE_FEDERATION=true
```

### Local instance identity

Before peer bootstrap, configure the local externally reachable address in Federation Overview or `config/federation.yaml`:

- `self_address`

This is runtime identity, not graph-only metadata.

Production expectations:

- `self_address` is valid and externally reachable from intended peers
- instance ID and public fingerprint are recorded operationally
- peer bootstrap uses the real mutual confirm flow

### Peer trust model

Federation is for governed Lancelot-to-Lancelot peers.

Production expectations:

- only trusted peers are registered
- root authority is explicit
- root-only actions remain root-only
- replay protection survives restart
- heartbeat and divergence status are monitored

### Graph Builder and deployment

If you deploy a graph:

- local node reuses the configured `self_address`
- node budgets are configured intentionally
- compatibility warnings are resolved before deployment
- the graph is not the source of truth for the local instance identity

### Hard-stop and kill governance

Before production, verify:

- operator-issued federation kill creates a persisted command record
- non-root peers cannot issue root-scoped federation kills
- budget `hard_stop` pauses the real runtime
- T3 Soul rollout pauses, confirms, and resumes truthfully

---

## 6. A2A Readiness

Only apply this section when A2A is enabled:

```env
FEATURE_A2A=true
```

Treat non-Lancelot A2A peers as hostile external systems.

Production expectations:

- preregistration required
- remote peers use verifiable credentials
- Agent Cards are pinned and reverified on drift
- inbound identity is not header-trusted
- outbound delegation uses bounded credentials
- A2A outputs are treated as untrusted input

Do not enable A2A in production as an open peer-discovery surface.

---

## 7. HIVE and Multi-Agent Readiness

Only apply this section when HIVE is enabled.

Production expectations:

- sub-agents inherit a narrower scoped Soul than the parent
- governance checks are active in the runtime path, not just docs
- budget and divergence gates actually affect spawn behavior
- child-agent receipts preserve operator/session provenance
- local stop and pause controls actually affect active HIVE execution

Before go-live, run at least one end-to-end HIVE task and inspect the receipt chain, not just the UI summary.

---

## 8. Compliance Export Readiness

Lancelot does not generate the final auditor-issued SOC 2 report. It generates evidence bundles for audit and GRC workflows.

Production expectation:

- SOC 2 / ISO 27001 / GDPR exports are framework-specific evidence packages
- each export bundle includes JSON, summary PDF, CSV index, README, and signed manifest/hash material
- forensic timeline remains a direct PDF artifact
- operator attribution and instance metadata are populated

Before production, generate one live export for each framework you intend to support and verify:

- correct framework mapping
- expected time scope
- exporter identity present
- active Soul versions present
- integrity block present
- no obviously missing deployment metadata

If `system_context` fields are blank, fix deployment metadata before relying on the export operationally.

---

## 9. Staged Rollout Plan

Do not go from local development straight to broad production.

Recommended sequence:

1. Internal single-instance production
2. Limited operator group
3. Real auth mode and vault backup verified
4. Pause/emergency stop tested
5. Compliance bundle generated and reviewed
6. Optional federation or A2A enablement only after the standalone instance is stable

For federation:

1. configure `self_address`
2. bootstrap a single peer
3. verify heartbeat, replay protection, and peer registration flow
4. test T2/T3 Soul propagation in a controlled environment
5. add more peers only after the first link is operationally proven

---

## 10. Production Readiness Checklist

### Identity and auth

- [ ] `LANCELOT_AUTH_PROVIDER` is set intentionally to `local` or `oidc`
- [ ] local auth has a strong `WARROOM_PASSWORD` and stored `WARROOM_PASSWORD_RESET_CODE`, or OIDC is fully configured
- [ ] War Room is behind trusted network boundaries and TLS
- [ ] admin-only surfaces are not reachable anonymously

### Secrets and persistence

- [ ] `LANCELOT_VAULT_KEY` is explicitly set
- [ ] vault key is backed up outside the host
- [ ] host storage is encrypted at rest
- [ ] restart was tested and credentials remained readable

### Runtime controls

- [ ] pause/resume was tested live
- [ ] emergency stop was tested live
- [ ] `/health/ready` is green
- [ ] degraded status surfaces were reviewed for enabled subsystems

### Tool execution

- [ ] `FEATURE_TOOLS_HOST_EXECUTION` is false
- [ ] outbound network policy is reviewed
- [ ] only required connectors are enabled

### Federation, if enabled

- [ ] `FEATURE_FEDERATION=true` is intentional
- [ ] `self_address` is configured and validated
- [ ] peer bootstrap was tested end to end
- [ ] root authority is documented
- [ ] budget hard-stop was tested in a non-production staging environment

### A2A, if enabled

- [ ] `FEATURE_A2A=true` is intentional
- [ ] remote peers are preregistered
- [ ] Agent Card verification and pinning are in use
- [ ] inbound and outbound status surfaces are reviewed

### HIVE, if enabled

- [ ] HIVE task execution was tested
- [ ] scoped Souls are visible in receipts
- [ ] pause/stop affects active agent execution

### Compliance

- [ ] framework exports were generated and reviewed
- [ ] bundle manifests and hashes are present
- [ ] deployment metadata is populated
- [ ] operator attribution is acceptable for audit use

### Rollout

- [ ] staged rollout plan is documented
- [ ] operator runbook exists for pause, emergency stop, federation bootstrap, and credential recovery
- [ ] backups and recovery ownership are assigned

---

## 11. Minimal Live Verification Commands

Run these after deployment:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/health/ready
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/war-room/
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/.well-known/agent.json
```

If authenticated management routes are enabled, confirm they are mounted and protected:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/federation/status
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/a2a/status
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/hive/status
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/timetravel/status
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/mcp/servers
```

Expected unauthenticated behavior for mounted admin routes is `401`, not `404`.

---

## Related Docs

- [Installation Guide](installation.md)
- [Quickstart](quickstart.md)
- [War Room Guide](war-room.md)
- [Federation Data Plane](federation.md)
- [A2A Protocol](a2a.md)
- [HIVE](hive.md)
- [Time-Travel](time-travel.md)
- [Compliance Export Guide](compliance-export.md)
- [Security Posture](security.md)
