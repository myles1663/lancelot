# Installation Guide

Comprehensive installation guide for Lancelot covering all supported deployment methods, hardware configuration, and provider setup.

If you just want to get running quickly, see the [Quickstart](quickstart.md) instead. This guide is for custom deployments, non-Docker setups, GPU configuration, and detailed tuning. If you are preparing an instance for enterprise use, finish with the [Production Hardening Guide](production-hardening.md) before go-live.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Docker Compose (Recommended)](#docker-compose-recommended)
3. [One-Command Installer](#one-command-installer)
4. [Manual Docker Setup](#manual-docker-setup)
5. [Bare-Metal Python Installation](#bare-metal-python-installation)
6. [Local Model Setup](#local-model-setup)
7. [Multi-Provider LLM Configuration](#multi-provider-llm-configuration)
8. [Network Configuration](#network-configuration)
9. [Persistent Storage](#persistent-storage)
10. [Verifying the Installation](#verifying-the-installation)
11. [Configuration Reference](#configuration-reference)
12. [Stopping, Restarting, and Updating](#stopping-restarting-and-updating)
13. [Troubleshooting](#troubleshooting)

---

## System Requirements

### Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **RAM** | 8 GB | 16 GB |
| **Disk** | 10 GB free | 20 GB free |
| **CPU** | 4 cores | 8+ cores |
| **GPU** | Not required | NVIDIA with 4+ GB VRAM |

The local GGUF model weights are approximately 5 GB. With Docker images and runtime data, plan for at least 10 GB of free disk space.

**GPU notes:** An NVIDIA GPU significantly speeds up the local model. Lancelot auto-detects NVIDIA GPUs and offloads model layers to VRAM. A GTX 1070 (8 GB VRAM) works well with 15 GPU layers. Without a GPU, the local model runs on CPU — it's slower but fully functional.

### Software

| Software | Minimum Version | How to Check |
|----------|----------------|--------------|
| **Docker Desktop** | 4.0+ | `docker --version` |
| **Docker Compose** | v2+ (included with Docker Desktop) | `docker compose version` |
| **Git** | 2.30+ | `git --version` |
| **Node.js** | 18+ (for installer only) | `node --version` |

### Supported Operating Systems

- Windows 10/11 (with WSL 2 and Docker Desktop)
- macOS 12+ (Intel or Apple Silicon)
- Linux (Ubuntu 20.04+, Debian 11+, Fedora 36+, or any distro with Docker)

### LLM Provider Accounts

You need an API key from at least one provider:

| Provider | Sign Up | Free Tier | Key Format |
|----------|---------|-----------|------------|
| **Google Gemini** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Yes | `AIza...` |
| **OpenAI** | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | No | `sk-...` |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com/) | No | `sk-ant-...` |
| **xAI (Grok)** | [console.x.ai](https://console.x.ai/) | No | `xai-...` |
| **NVIDIA Nemotron** | [build.nvidia.com](https://build.nvidia.com/) | Yes | `nvapi-...` |

You can configure one or more providers. Lancelot routes between them based on task complexity and provider availability. API keys can be rotated from the War Room UI without restarting the container.

For `openai-codex`, the preferred production path is not an API key. Sign in with the Codex CLI on the host first so `~/.codex/auth.json` exists and can be mounted into the container. During onboarding, selecting Codex now checks for that mounted auth file first and proceeds immediately when it is present. Browser OAuth remains available only as a fallback when mounted Codex auth is missing.

---

## Docker Compose (Recommended)

Docker Compose is the primary and recommended deployment method. It runs two containers:

| Container | Port | Purpose |
|-----------|------|---------|
| `lancelot_core` | 8000 | FastAPI gateway, API endpoints, War Room React SPA |
| `lancelot_local_llm` | 8080 | Local GGUF model inference server |

Both containers communicate on an internal bridge network (`lancelot_net`). The core container depends on the local LLM readiness probe before starting. That probe now requires a real local inference smoke, not just "model loaded."

> **Production note:** installation success is not the same as production readiness. Before go-live, run the auth, runtime-control, federation, A2A, and compliance checks in the [Production Hardening Guide](production-hardening.md).

---

## One-Command Installer

The fastest path. Requires Docker Desktop and Node.js 18+:

```bash
npx create-lancelot
```

The installer handles:

- Prerequisites verification (Docker, Git, disk, RAM, GPU)
- Install location selection
- Provider and API key configuration (with live validation)
- War Room auth model selection (`local` account or enterprise `oidc`)
- Repository clone
- `.env` generation and `docker-compose.yml` patching for your hardware
- Model download (5 GB, with resume support)
- Docker build and startup
- Health check verification

**Installer options:**

| Flag | Description |
|------|-------------|
| `-d, --directory <path>` | Install location (default: `./lancelot`) |
| `--provider <name>` | Pre-select: `gemini`, `openai`, `anthropic`, `xai`, or `nvidia` |
| `--resume` | Resume an interrupted installation |

When the installer finishes, it automatically opens the **War Room** in your default browser at `http://localhost:8000`.

The repo's installer-owned smoke coverage now exercises the live CLI entrypoint plus the real clone/configure/build/start/health/host-agent code paths against a local harness, so the "one-command installer" claim is backed by the actual installer modules rather than only fixture-only flows.

### Fresh-Machine Proof Bundle

If you need due-diligence evidence from a real clean-host install, collect it immediately after the installer finishes:

```bash
cd /path/to/installed/lancelot
node installer/scripts/collect-install-proof.mjs \
  --install-dir . \
  --output-dir ./installer-proof
```

The collector emits:

- `installer-proof/installer-proof.json` - machine-readable artifact manifest, host details, command versions, health checks, and file hashes
- `installer-proof/installer-proof.md` - human-readable checklist for reviewers
- `installer-proof/sanitized.env` - redacted environment snapshot with secrets removed

The proof collector checks:

- generated `.env`
- patched `docker-compose.yml`
- onboarding snapshot and `USER.md`
- presence and hashes of downloaded model weights
- `/health`, `/health/live`, `/health/ready`, and Host Agent health
- local `git`, `docker`, and `docker compose` command availability

Use `--allow-partial` only when you need to gather a bundle from a degraded host for troubleshooting; for a real installer proof run, let the collector fail if required evidence is missing.

---

## Manual Docker Setup

If you prefer manual control over the setup process:

### 1. Clone the repository

```bash
git clone https://github.com/myles1663/lancelot.git
cd lancelot
```

### 2. Create your environment file

```bash
cp config/models.example.yaml config/models.yaml
```

Start from the canonical template in the project root:

```bash
cp .env.example .env
```

Then edit `.env` with your environment-specific values:

```ini
# LLM Provider (gemini, openai, anthropic, xai, or nvidia)
LANCELOT_PROVIDER=gemini

# LLM API Keys (at least one required, matching your provider)
GEMINI_API_KEY=your-key-here
# OPENAI_API_KEY=your-key-here
# ANTHROPIC_API_KEY=your-key-here
# XAI_API_KEY=your-key-here

# Optional Gemini ADC / OAuth bootstrap
# Only set this if you explicitly want Google ADC instead of an API key.
# LANCELOT_AUTH_MODE=OAUTH

# Security Tokens (auto-generated during onboarding if omitted)
# LANCELOT_OWNER_TOKEN=
# LANCELOT_API_TOKEN=
# LANCELOT_VAULT_KEY=
# Development-only vault escape hatch (do not enable in production)
# LANCELOT_ALLOW_EPHEMERAL_VAULT=false

# War Room authentication
# Choose exactly one model:
# LANCELOT_AUTH_PROVIDER=local
# WARROOM_USERNAME=choose-an-operator-username
# WARROOM_PASSWORD=choose-a-strong-password
# WARROOM_PASSWORD_RESET_CODE=store-this-reset-code-securely

# LANCELOT_AUTH_PROVIDER=oidc
# OIDC_ISSUER_URL=https://your-idp.example.com/realms/lancelot
# OIDC_CLIENT_ID=lancelot-war-room
# OIDC_CLIENT_SECRET=your-client-secret
# OIDC_REDIRECT_URI=http://localhost:8000/auth/oidc/callback
# OIDC_ALLOWED_GROUPS=lancelot-admins,lancelot-operators
# OIDC_ALLOW_ANY_AUTHENTICATED=false
# Set OIDC_ALLOW_ANY_AUTHENTICATED=true only when you explicitly intend to
# allow any authenticated OIDC user into the War Room.

# Local model settings
LOCAL_LLM_URL=http://local-llm:8080
LOCAL_MODEL_CTX=4096
LOCAL_MODEL_THREADS=4
LOCAL_MODEL_GPU_LAYERS=0
LOCAL_LLM_WHEEL_VARIANT=cpu
LOCAL_LLM_WHEEL_VERSION=0.3.19

# Logging
LANCELOT_LOG_LEVEL=INFO

# Feature Flags
FEATURE_SOUL=true
FEATURE_SKILLS=true
FEATURE_HEALTH_MONITOR=true
FEATURE_SCHEDULER=true
FEATURE_AGENTIC_LOOP=true
FEATURE_LOCAL_AGENTIC=true
```

> **Tip:** If you skip the security tokens, the in-app onboarding at `http://localhost:8000` will auto-generate them on first launch.

### 3. Download the local model

The local model is always installed as part of the supported deployment shape. It handles low-risk utility work and local frontier scrubbing based on the admin policy you set after install. See [Local Model Setup](#local-model-setup) for details.

### 4. Configure GPU (if available)

If you have an NVIDIA GPU, edit `.env` to offload model layers:

```ini
LOCAL_MODEL_GPU_LAYERS=15
LOCAL_LLM_WHEEL_VARIANT=cu123
```

The `docker-compose.yml` already includes GPU configuration. If you do **not** have an NVIDIA GPU, remove or comment out the `deploy` section under `local-llm`:

```yaml
# Remove this block if no NVIDIA GPU:
# deploy:
#   resources:
#     reservations:
#       devices:
#         - driver: nvidia
#           count: 1
#           capabilities: [gpu]
```

### 5. Build and start

```bash
# First build (compiles images)
docker compose up -d --build
```

First build takes 3-10 minutes. Watch logs with:

```bash
docker compose logs -f
```

Wait for:
```
lancelot_core       | INFO:     Uvicorn running on http://0.0.0.0:8000
lancelot_local_llm  | INFO:     Model loaded successfully
```

### 6. Open the War Room

```bash
# Auto-opens War Room in your browser when ready
./launch.sh            # Linux / macOS / Git Bash
.\launch.ps1           # PowerShell (Windows)
```

Or open `http://localhost:8000` manually. The in-app onboarding will guide you through any remaining configuration, including provider selection, API key validation, comms setup, security tokens, and War Room auth model selection. If you choose `OpenAI Codex (Pro)`, onboarding now prefers mounted host Codex auth at `~/.codex/auth.json`; complete host Codex sign-in before launching if you want to avoid the browser OAuth fallback.

### 7. Verify

```bash
curl http://localhost:8000/health/live
# Expected: {"status": "alive"}

curl http://localhost:8000/health/ready
# Expected: {"ready": true, "local_llm_ready": true, ...}
```

---

## Bare-Metal Python Installation

Running without Docker is supported but **not recommended**. Docker provides the sandboxed execution environment that the Tool Fabric relies on for security isolation.

### Prerequisites

- Python 3.11+
- pip

### Install dependencies

```bash
pip install -r requirements.txt
```

This installs the main Lancelot API/runtime environment. The local model service is a separate Python runtime and does **not** come from the root `requirements.txt`.

If you need the test toolchain in a legacy pip workflow, install:

```bash
pip install -r requirements-dev.txt
```

### Start the API server

```bash
PYTHONPATH=src/core:src/ui:src/agents:src/memory:src/shared:src/integrations:src \
  uvicorn gateway:app --host 0.0.0.0 --port 8000
```

### Start the local LLM server

```bash
cd local_models
pip install -r requirements-llm.txt
python server.py
```

#### Windows local-model note

On Windows, `llama-cpp-python` should not be installed through the root project sync path. Use one of these explicit local-model install paths instead:

- CPU / no NVIDIA acceleration:

```bash
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu "llama-cpp-python==0.3.19"
```

- NVIDIA CUDA 12.3 acceleration:

```bash
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu123 "llama-cpp-python==0.3.19"
```

If you skip the wheel index on Windows, `pip` / `uv` will fall back to a native source build and require Visual Studio C++ Build Tools, `nmake`, CMake, and a configured MSVC toolchain.

For the Docker local-model service, the repo now builds the CPU wheel by default. Opt into CUDA explicitly with `LOCAL_LLM_WHEEL_VARIANT=cu123` only after validating that the host can complete local inference smoke reliably.

### Environment variables

Set the same variables from the `.env` file as environment variables in your shell, plus:

```bash
export LOCAL_LLM_URL=http://localhost:8080
export FEATURE_TOOLS_HOST_EXECUTION=true
```

**Security warning:** Without Docker, tool execution runs directly on your host machine. The `FEATURE_TOOLS_HOST_EXECUTION=true` flag is required, but bypasses the Docker sandbox. All workspace boundary enforcement and command denylists still apply, command `cwd` is constrained to the configured workspace, and host execution now uses direct subprocess argument invocation instead of a generic host shell. There is still no container isolation.

---

## Local Model Setup

Lancelot uses a local GGUF model for routine tasks that don't need cloud APIs:

- **Intent classification** — routing messages to the right handler
- **Summarization** — condensing context
- **PII redaction** — stripping sensitive data before external API calls
- **JSON extraction** — structured data parsing

The current model is **Qwen3-8B Q4_K_M** (~5 GB).

### Download the model

**Via the installer** (recommended):
```bash
npx create-lancelot --resume
```

**Via the Python fetch script:**
```bash
python -c "from local_models.fetch_model import fetch_model; fetch_model()"
```

**Manual download:** Download the GGUF file and place it in `local_models/weights/`. The expected filename is defined in `local_models/models.lock.yaml`.

### Verify the model

```bash
ls -la local_models/weights/
```

You should see a `.gguf` file approximately 5 GB in size.

### GPU offloading

The local model supports NVIDIA CUDA GPU offloading. Configure the number of layers to offload in your `.env`:

| GPU VRAM | Recommended Layers | `.env` Setting |
|----------|-------------------|----------------|
| No GPU | 0 | `LOCAL_MODEL_GPU_LAYERS=0` |
| 4 GB | 8 | `LOCAL_MODEL_GPU_LAYERS=8` |
| 6 GB | 12 | `LOCAL_MODEL_GPU_LAYERS=12` |
| 8 GB | 15 | `LOCAL_MODEL_GPU_LAYERS=15` |
| 12+ GB | 20 | `LOCAL_MODEL_GPU_LAYERS=20` |

**Known constraint:** On a GTX 1070 (8 GB VRAM), 15 layers + 4096 context works reliably. 20+ layers causes out-of-memory errors.

### Context window

The default context window is 4096 tokens. You can adjust this in `.env`:

```ini
LOCAL_MODEL_CTX=4096     # Default
LOCAL_MODEL_CTX=2048     # Lower memory usage
LOCAL_MODEL_CTX=8192     # More context (needs more RAM/VRAM)
```

### Local model usage policy

The local model is installed on every supported instance. What changes is how the runtime uses it:

- `local_execution_mode`
  - `low_risk_only`: use the local model for bounded low-risk utility work to reduce frontier token usage
  - `disabled`: do not use the local model for utility execution
- `frontier_scrub_mode`
  - `required`: frontier-bound content must be scrubbed locally first; if local scrubbing is unavailable or the local scrub output still contains detectable structured PII, the request is blocked
  - `preferred`: use local scrubbing when available; if unavailable, allow direct frontier egress and record degraded privacy mode
  - `disabled`: do not require local scrubbing before frontier egress

These controls are runtime usage settings, not install options. The local model remains present on disk and available for readiness verification, recovery, and later policy changes.

---

## Multi-Provider LLM Configuration

Lancelot routes tasks across four lanes, using local and cloud models:

| Priority | Lane | Default Model | Purpose |
|----------|------|---------------|---------|
| 1 | `local_redaction` | Qwen3-8B (local) | Local scrub lane used when frontier scrub policy requires or prefers local redaction |
| 2 | `local_utility` | Qwen3-8B (local) | Classify, summarize, extract |
| 3 | `flagship_fast` | Gemini Flash / GPT-4o-mini / Claude Haiku / Nemotron Nano | Orchestration, tool calls |
| 4 | `flagship_deep` | Gemini Pro / GPT-4o / Claude Sonnet / Nemotron Super | Planning, complex reasoning |

### Model configuration

Edit `config/models.yaml` to configure which models each provider uses:

```yaml
models:
  primary:
    provider: google          # google, openai, or anthropic
    name: gemini-3-flash-preview
    temperature: 0.7
    max_tokens: 8192
  orchestrator:
    provider: google
    name: gemini-3-flash-preview
    temperature: 0.3
    max_tokens: 4096
  utility:
    provider: google
    name: gemini-3-flash-preview
    temperature: 0.5
    max_tokens: 2048
```

### Routing configuration

Edit `config/router.yaml` to control how tasks escalate between lanes. Tasks automatically escalate from fast to deep when:

- High-risk actions are detected
- Task complexity exceeds fast-lane capacity
- Fast-lane execution fails

### Using multiple providers

Configure all three API keys in `.env`. Lancelot will use the primary provider for most tasks and fail over to secondary providers if the primary is unavailable or rate-limited.

---

## Network Configuration

### Ports

| Port | Service | Purpose |
|------|---------|---------|
| 8000 | lancelot-core | FastAPI gateway, API, War Room React SPA |
| 8080 | local-llm | Local model inference |

Both are configurable in `docker-compose.yml` under the `ports` section.

### Domain allowlist

Lancelot restricts outbound network access to an explicit allowlist defined in `config/network_allowlist.yaml`:

```yaml
domains:
  - api.anthropic.com
  - api.github.com
  - api.telegram.org
  - generativelanguage.googleapis.com
  - github.com
  - raw.githubusercontent.com
```

If `FEATURE_NETWORK_ALLOWLIST=true`, an empty or missing allowlist is treated as a configuration error and outbound domains are blocked until the file is populated.

To allow additional domains (for connectors or integrations), add them to this file and restart.

### Firewall considerations

- **Inbound:** Only ports 8000 and 8080 need to be accessible (localhost only by default)
- **Outbound:** Allow HTTPS (443) to the domains in your allowlist
- The War Room is designed for local access only — do not expose it to the public internet without additional authentication

---

## Persistent Storage

### Volume mounts

The `docker-compose.yml` maps two key volumes:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./lancelot_data` | `/home/lancelot/data` | Runtime data (receipts, databases, registries) |
| `.` (project root) | `/home/lancelot/app` | Application code |

### Data directories

| Path | Contents |
|------|----------|
| `lancelot_data/receipts/` | Audit trail directory (`receipts.db` immutable log + staging tables, plus `receipt_integrity_key.json` for the persisted finalized-receipt signing key when no external key override is configured) |
| `lancelot_data/chat_log.json` | Chat history |
| `lancelot_data/USER.md` | Owner profile |
| `lancelot_data/RULES.md` | Runtime copy of the operating-rules bootstrap template |
| `lancelot_data/CAPABILITIES.md` | Runtime copy of the capabilities bootstrap template |
| `lancelot_data/scheduler.sqlite` | Scheduler job state |
| `lancelot_data/memory.sqlite` | Memory database (if Memory vNext enabled) |
| `lancelot_data/skills_registry.json` | Installed skills |
| `lancelot_data/soul_proposals.json` | Soul amendment proposals |
| `lancelot_data/governance/trust_ledger.json` | Persisted Trust Ledger state and graduation history |

All persistent data lives in `lancelot_data/`. Back up this directory to preserve your system state.

Canonical bootstrap text files are tracked under `config/bootstrap/`. The runtime seeds missing `RULES.md` and `CAPABILITIES.md` into `lancelot_data/` automatically so the live data directory no longer needs tracked seed content in git.

### Workspace

The Docker container can mount an optional shared workspace:

```yaml
volumes:
  - "/path/to/workspace:/home/lancelot/workspace"
```

This is the directory where Lancelot reads and writes files for tool execution. The workspace boundary is enforced — Tool Fabric operations cannot access files outside this directory.

---

## Verifying the Installation

Run through these checks to confirm everything is working:

### 1. Container health

```bash
docker compose ps
```

Both services should show `running` or `healthy`.

### 2. Health endpoints

```bash
# Liveness (always responds if the process is running)
curl http://localhost:8000/health/live
# Expected: {"status": "alive"}

# Readiness (all subsystems checked)
curl http://localhost:8000/health/ready
# Expected: {"ready": true, "local_llm_ready": true, ...}
```

### 3. Local model

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "local", "messages": [{"role": "user", "content": "hello"}]}'
```

Should return a JSON response with a completion. This is the same class of inference smoke Lancelot now uses to decide whether the local model is truly ready.

### 4. Soul status

```bash
curl http://localhost:8000/soul/status
```

Should show `active_version: "v1"` and `invariants_passing: true`.

### 5. Chat endpoint

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "hello"}'
```

Should return a governed response with receipt IDs.

### 6. War Room

Open `http://localhost:8000` in a browser. You should see the operator dashboard with health, governance, and system panels.

---

## Configuration Reference

### Environment Variables (`.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| **LLM Provider** | | | |
| `LANCELOT_PROVIDER` | Yes | — | Active provider: `gemini`, `openai`, `anthropic`, `xai`, or `nvidia` |
| `GEMINI_API_KEY` | One of five | — | Google Gemini API key |
| `OPENAI_API_KEY` | One of five | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | One of five | — | Anthropic API key |
| `XAI_API_KEY` | One of five | — | xAI (Grok) API key |
| `NVIDIA_API_KEY` | One of five | — | NVIDIA NIM API key |
| **Security** | | | |
| `LANCELOT_OWNER_TOKEN` | Yes | — | Token for Soul amendments, memory writes |
| `LANCELOT_API_TOKEN` | Yes | — | Token for API authentication |
| `LANCELOT_VAULT_KEY` | Yes | — | Encryption key for credential vault |
| **Local Model** | | | |
| `LOCAL_LLM_URL` | No | `http://local-llm:8080` | Local model server URL |
| `LOCAL_MODEL_CTX` | No | `4096` | Local model context window |
| `LOCAL_MODEL_THREADS` | No | `4` | CPU threads for local model |
| `LOCAL_MODEL_GPU_LAYERS` | No | `0` | GPU layers to offload |
| **General** | | | |
| `LANCELOT_LOG_LEVEL` | No | `INFO` | Logging level |
| **Communications** | | | |
| `LANCELOT_COMMS_TYPE` | No | — | Channel type: `telegram`, `google_chat`, or `none` |
| `LANCELOT_TELEGRAM_TOKEN` | No | — | Telegram bot token |
| `LANCELOT_TELEGRAM_CHAT_ID` | No | — | Telegram chat ID |
| `LANCELOT_TELEGRAM_DEBUG_DUMP` | No | `false` | Explicit debug mode that writes the last outbound Telegram message and metadata to `lancelot_data/chat/debug/` for operator troubleshooting. Leave disabled in normal operation. |
| `LANCELOT_CHAT_SPACE_NAME` | No | — | Google Chat space resource name |
| `LANCELOT_WEBHOOK_AUTH_MODE` | No | `google_signed` | Inbound webhook auth mode: `google_signed` or `bonded_bearer` |
| `LANCELOT_GOOGLE_CHAT_AUDIENCE` | No | — | Required when `LANCELOT_WEBHOOK_AUTH_MODE=google_signed`; expected audience for Google-signed Chat callback tokens |
| `LANCELOT_WEBHOOK_BEARER` | No | — | Dedicated bearer secret for `bonded_bearer` inbound comms callbacks; no fallback to `LANCELOT_API_TOKEN` |
> **Note:** `LANCELOT_OWNER_TOKEN` and `LANCELOT_API_TOKEN` can be generated during onboarding if omitted. The credential vault now fails closed when `LANCELOT_VAULT_KEY` is missing unless you explicitly opt into `LANCELOT_ALLOW_EPHEMERAL_VAULT=true` for development-only use.
>
> **Current runtime support:** bidirectional communications are currently implemented for `telegram` and `google_chat`. Other messaging and outreach services remain connector-level capabilities until dedicated comms runtimes are added.

### YAML Configuration Files

| File | Purpose |
|------|---------|
| `config/models.yaml` | LLM provider and model assignments |
| `config/router.yaml` | Routing lanes and escalation rules |
| `config/scheduler.yaml` | Automated job definitions |
| `config/governance.yaml` | Risk classification defaults and policy settings |
| `config/network_allowlist.yaml` | Allowed outbound domains |
| `config/connectors.yaml` | Connector registry and settings |
| `config/trust_graduation.yaml` | Trust graduation thresholds |
| `config/approval_learning.yaml` | APL pattern detection settings |
| `config/vault.yaml` | Credential vault configuration |

### Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_SOUL` | `true` | Constitutional governance |
| `FEATURE_SKILLS` | `true` | Modular skill system |
| `FEATURE_HEALTH_MONITOR` | `true` | Background health monitoring |
| `FEATURE_SCHEDULER` | `true` | Job scheduling |
| `FEATURE_MEMORY_VNEXT` | `false` | Tiered memory system |
| `FEATURE_TOOLS_FABRIC` | `true` | Tool execution layer |
| `FEATURE_TOOLS_CLI_PROVIDERS` | `false` | CLI tool adapters |
| `FEATURE_TOOLS_ANTIGRAVITY` | `false` | Generative UI/Vision |
| `FEATURE_TOOLS_NETWORK` | `false` | Network access in sandbox |
| `FEATURE_TOOLS_HOST_EXECUTION` | `false` | Host execution (no sandbox) |
| `FEATURE_AGENTIC_LOOP` | `false` | Agentic tool loop |
| `FEATURE_LOCAL_AGENTIC` | `false` | Enable the local utility execution lane for low-risk token-saving work |

Set to `true`, `1`, or `yes` to enable; anything else disables.

---

## Stopping, Restarting, and Updating

### Stop (preserves data)

```bash
docker compose down
```

### Start / Restart

```bash
# Recommended — auto-opens War Room in browser when ready
./launch.sh            # Linux / macOS / Git Bash
.\launch.ps1           # PowerShell (Windows)

# Or start without auto-open
docker compose up -d
```

### Restart after config change

```bash
docker compose restart
```

### Update to latest version

```bash
git pull origin master
docker compose build
docker compose up -d
```

Your data in `lancelot_data/` is preserved across updates.

### Full reset (destroys data)

```bash
docker compose down -v
```

**Warning:** The `-v` flag deletes all persistent data — receipts, memory, registries. Only use this for a complete fresh start.

### Rebuild from scratch

```bash
docker compose build --no-cache
docker compose up -d
```

---

## Troubleshooting

### Docker not running

**Symptom:** `docker compose up` fails immediately.

**Fix:** Open Docker Desktop and verify it's running. On Windows, ensure WSL 2 is installed: `wsl --install`. Verify with `docker info`.

### Local LLM won't start

**Symptom:** `lancelot_local_llm` keeps restarting, shows `unhealthy`, or War Room reports the model as loaded but not ready.

**Fix:**
1. Verify model weights exist: `ls local_models/weights/` (should show a ~5 GB `.gguf` file)
2. Check logs: `docker compose logs local-llm`
3. If out of memory, reduce context: `LOCAL_MODEL_CTX=2048` in `.env`
4. If no NVIDIA GPU, remove the `deploy.resources` block from `docker-compose.yml`
5. If the container stays up but readiness is still false, the inference smoke is failing — inspect the last readiness error in War Room or the local-llm logs

### Port conflicts

**Symptom:** `Bind for 0.0.0.0:8000: address already in use`

**Fix:** Change the host port in `docker-compose.yml`:
```yaml
ports:
  - "9000:8000"
```

### API key errors

**Symptom:** Chat requests return authentication errors.

**Fix:** Verify your API key in `.env` — no extra spaces, no quotes. Restart: `docker compose restart`.

### GPU not detected

**Symptom:** `LOCAL_MODEL_GPU_LAYERS` is set but the model runs on CPU only.

**Fix:**
1. Verify NVIDIA drivers: `nvidia-smi`
2. Verify Docker GPU access: `docker run --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi`
3. Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) if missing

### Permission denied errors

**Symptom:** Container logs show "Permission denied" for data files.

**Fix:** Ensure `lancelot_data/` is writable:
```bash
mkdir -p lancelot_data
chmod 777 lancelot_data
```

### UAB Daemon Setup

The Universal Application Bridge requires a Node.js daemon running on the host machine (outside Docker).

**Prerequisites:** Node.js 18+

**Install and start (Linux/macOS):**
```bash
./scripts/install-uab.sh --start
```

**Install with auto-start (Windows):**
```batch
scripts\install-uab.bat
```
Registers a `LancelotUABDaemon` Scheduled Task that starts the daemon on login. Idempotent — safe to re-run.

**Manual foreground start (Windows — for debugging):**
```batch
scripts\start-uab.bat
```

**Manual setup:**
```bash
cd packages/uab
npm install
npm run build
node dist/daemon.js --port 7900
```

**Verify:**
```bash
curl http://localhost:7900 -d '{"jsonrpc":"2.0","method":"getStatus","params":{},"id":1}'
```

**Enable in Lancelot:** Set `FEATURE_TOOLS_UAB=true` in `.env` (requires `FEATURE_TOOLS_FABRIC=true`).

### Hive Agent Mesh Configuration

To enable the Hive Agent Mesh:

1. Set `FEATURE_HIVE=true` in `.env`
2. Ensure `config/hive.yaml` exists (see [Configuration Reference](configuration-reference.md) for all fields)
3. Optionally enable UAB integration: set `FEATURE_HIVE_UAB=true` (requires `FEATURE_TOOLS_UAB=true`)

**Verify:**
```bash
curl http://localhost:8000/api/hive/status
```

Expected: `{"status": "idle", "enabled": true, ...}`

### Windows Git Bash path mangling

**Symptom:** Docker exec commands fail with mangled paths (e.g., `C:/Program Files/Git/home/...`).

**Fix:** Prefix Docker commands with `MSYS_NO_PATHCONV=1`:
```bash
MSYS_NO_PATHCONV=1 docker exec -it lancelot_core bash
```
