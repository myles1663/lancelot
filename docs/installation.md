# Installation Guide

Comprehensive installation guide for Lancelot covering all supported deployment methods, hardware configuration, and provider setup.

If you just want to get running quickly, see the [Quickstart](quickstart.md) instead. This guide is for custom deployments, non-Docker setups, GPU configuration, and detailed tuning.

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

You can configure one or more providers. Lancelot routes between them based on task complexity and provider availability. API keys can be rotated from the War Room UI without restarting the container.

---

## Docker Compose (Recommended)

Docker Compose is the primary and recommended deployment method. It runs two containers:

| Container | Port | Purpose |
|-----------|------|---------|
| `lancelot_core` | 8000 | FastAPI gateway, War Room, all subsystems |
| `lancelot_local_llm` | 8080 | Local GGUF model inference server |

Both containers communicate on an internal bridge network (`lancelot_net`). The core container depends on the local LLM being healthy before starting.

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
- Repository clone
- `.env` generation and `docker-compose.yml` patching for your hardware
- Model download (5 GB, with resume support)
- Docker build and startup
- Health check verification

**Installer options:**

| Flag | Description |
|------|-------------|
| `-d, --directory <path>` | Install location (default: `./lancelot`) |
| `--provider <name>` | Pre-select: `gemini`, `openai`, or `anthropic` |
| `--skip-model` | Skip the local model download |
| `--resume` | Resume an interrupted installation |

When the installer finishes, the War Room is live at `http://localhost:8000/war-room/`.

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

Create a `.env` file in the project root with your configuration:

```ini
# LLM API Keys (at least one required)
GEMINI_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here

# Owner authentication token (required for Soul amendments, memory writes)
LANCELOT_OWNER_TOKEN=choose-a-secure-token

# Local model settings
LOCAL_LLM_URL=http://local-llm:8080
LOCAL_MODEL_CTX=4096
LOCAL_MODEL_THREADS=4
LOCAL_MODEL_GPU_LAYERS=0

# Logging
LANCELOT_LOG_LEVEL=INFO
```

### 3. Download the local model

The local model handles routine tasks (classification, summarization, PII redaction) without sending data to cloud APIs. See [Local Model Setup](#local-model-setup) for details.

### 4. Configure GPU (if available)

If you have an NVIDIA GPU, edit `.env` to offload model layers:

```ini
LOCAL_MODEL_GPU_LAYERS=15
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
docker compose up -d
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

### 6. Verify

```bash
curl http://localhost:8000/health/live
# Expected: {"status": "alive"}

curl http://localhost:8000/health/ready
# Expected: {"ready": true, "local_llm_ready": true, ...}
```

Open the War Room at `http://localhost:8000/war-room/`.

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

### Start the API server

```bash
PYTHONPATH=src/core:src/ui:src/agents:src/memory:src/shared:src/integrations:src \
  uvicorn gateway:app --host 0.0.0.0 --port 8000
```

### Start the local LLM server

```bash
cd local_models
pip install llama-cpp-python>=0.2.0
python server.py
```

### Environment variables

Set the same variables from the `.env` file as environment variables in your shell, plus:

```bash
export LOCAL_LLM_URL=http://localhost:8080
export FEATURE_TOOLS_HOST_EXECUTION=true
```

**Security warning:** Without Docker, tool execution runs directly on your host machine. The `FEATURE_TOOLS_HOST_EXECUTION=true` flag is required, but bypasses the Docker sandbox. All workspace boundary enforcement and command denylists still apply, but there is no container isolation.

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

### Running without a local model

Lancelot works without the local model — it routes all tasks (including classification and redaction) to cloud APIs. This means:

- Higher API costs (routine tasks use cloud tokens)
- PII redaction happens via cloud APIs (data leaves your machine)
- Slightly higher latency for classification tasks

To skip the local model during install: `npx create-lancelot --skip-model`

---

## Multi-Provider LLM Configuration

Lancelot routes tasks across four lanes, using local and cloud models:

| Priority | Lane | Default Model | Purpose |
|----------|------|---------------|---------|
| 1 | `local_redaction` | Qwen3-8B (local) | PII redaction — always local |
| 2 | `local_utility` | Qwen3-8B (local) | Classify, summarize, extract |
| 3 | `flagship_fast` | Gemini Flash / GPT-4o-mini / Claude Haiku | Orchestration, tool calls |
| 4 | `flagship_deep` | Gemini Pro / GPT-4o / Claude Sonnet | Planning, complex reasoning |

### Model configuration

Edit `config/models.yaml` to configure which models each provider uses:

```yaml
models:
  primary:
    provider: google          # google, openai, or anthropic
    name: gemini-2.0-flash
    temperature: 0.7
    max_tokens: 8192
  orchestrator:
    provider: google
    name: gemini-2.0-flash
    temperature: 0.3
    max_tokens: 4096
  utility:
    provider: google
    name: gemini-2.0-flash
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
| 8000 | lancelot-core | FastAPI gateway + War Room |
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
| `lancelot_data/receipts/` | Audit trail (JSON files) |
| `lancelot_data/chat_log.json` | Chat history |
| `lancelot_data/USER.md` | Owner profile |
| `lancelot_data/scheduler.sqlite` | Scheduler job state |
| `lancelot_data/memory.sqlite` | Memory database (if Memory vNext enabled) |
| `lancelot_data/skills_registry.json` | Installed skills |
| `lancelot_data/soul_proposals.json` | Soul amendment proposals |

All persistent data lives in `lancelot_data/`. Back up this directory to preserve your system state.

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

Should return a JSON response with a completion.

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

Open `http://localhost:8000/war-room/` in a browser. You should see the operator dashboard with health, governance, and system panels.

---

## Configuration Reference

### Environment Variables (`.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | One of three | — | Google Gemini API key |
| `OPENAI_API_KEY` | One of three | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | One of three | — | Anthropic API key |
| `LANCELOT_OWNER_TOKEN` | Yes | — | Token for administrative actions |
| `LOCAL_LLM_URL` | No | `http://local-llm:8080` | Local model server URL |
| `LOCAL_MODEL_CTX` | No | `4096` | Local model context window |
| `LOCAL_MODEL_THREADS` | No | `4` | CPU threads for local model |
| `LOCAL_MODEL_GPU_LAYERS` | No | `0` | GPU layers to offload |
| `LANCELOT_LOG_LEVEL` | No | `INFO` | Logging level |
| `TELEGRAM_BOT_TOKEN` | No | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat ID |
| `GOOGLE_CHAT_WEBHOOK_URL` | No | — | Google Chat webhook URL |

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
| `FEATURE_LOCAL_AGENTIC` | `false` | Route simple queries to local model |

Set to `true`, `1`, or `yes` to enable; anything else disables.

---

## Stopping, Restarting, and Updating

### Stop (preserves data)

```bash
docker compose down
```

### Restart

```bash
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

**Symptom:** `lancelot_local_llm` keeps restarting or shows `unhealthy`.

**Fix:**
1. Verify model weights exist: `ls local_models/weights/` (should show a ~5 GB `.gguf` file)
2. Check logs: `docker compose logs local-llm`
3. If out of memory, reduce context: `LOCAL_MODEL_CTX=2048` in `.env`
4. If no NVIDIA GPU, remove the `deploy.resources` block from `docker-compose.yml`

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

### Windows Git Bash path mangling

**Symptom:** Docker exec commands fail with mangled paths (e.g., `C:/Program Files/Git/home/...`).

**Fix:** Prefix Docker commands with `MSYS_NO_PATHCONV=1`:
```bash
MSYS_NO_PATHCONV=1 docker exec -it lancelot_core bash
```
