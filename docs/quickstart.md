# Quickstart

**Goal:** Clone to first governed action in 15 minutes.

This guide gets you from zero to a working Lancelot instance as fast as possible. No explanations of why things work this way — just what to type and what you should see. For deeper understanding, see the [Architecture](architecture.md) and [Installation Guide](installation.md).

---

## Prerequisites

Before you start, make sure you have:

- **Docker Desktop** running (verify: `docker info` shows system info, not an error)
- **Node.js 18+** installed (verify: `node --version` shows v18 or higher)
- **An LLM API key** from at least one provider:
  - [Google Gemini](https://aistudio.google.com/apikey) (recommended — has a free tier)
  - [OpenAI](https://platform.openai.com/api-keys) (pay-as-you-go)
  - [Anthropic](https://console.anthropic.com/) (pay-as-you-go)
  - [xAI](https://console.x.ai/) (Grok, pay-as-you-go)

---

## Step 1: Install Lancelot

Run the one-command installer:

```bash
npx create-lancelot
```

The installer walks you through each step interactively:

1. **Prerequisites check** — verifies Docker, Git, disk space, RAM, GPU
2. **Install location** — where to put Lancelot (default: `./lancelot`)
3. **LLM provider** — select Gemini, OpenAI, Anthropic, or xAI and paste your API key
4. **Communications** — optionally configure a messaging channel: Telegram, Google Chat, Slack, Discord, Teams, WhatsApp, Email, or SMS (you can skip this)
5. **Repository clone** — pulls the latest code from GitHub
6. **Configuration** — generates your `.env` file automatically
7. **Model download** — downloads the 5GB local utility model (with progress bar)
8. **Docker build & start** — builds images, starts services, waits for health

When it finishes, the **War Room** opens automatically in your default browser and you'll see:

```
  ╔══════════════════════════════════════════╗
  ║        LANCELOT IS READY!                ║
  ╠══════════════════════════════════════════╣
  ║  War Room: http://localhost:8501         ║
  ║  API:      http://localhost:8000         ║
  ╚══════════════════════════════════════════╝
```

> **Tip:** If the install is interrupted, resume with `npx create-lancelot --resume`. To skip the 5GB model download, use `--skip-model` (the local model handles routine tasks like PII redaction — Lancelot still works without it, but routes everything to cloud APIs).

---

## Step 2: Open the War Room

The War Room opens automatically after install. If you need to open it manually:

```
http://localhost:8501
```

**Starting Lancelot after first install:**

```bash
# Auto-opens War Room in your browser when ready
./launch.sh            # Linux / macOS / Git Bash
.\launch.ps1           # PowerShell (Windows)

# Or start manually (no auto-open)
docker compose up -d
```

You should see the Lancelot War Room dashboard with panels for health, governance, and system status. The health dashboard should show all subsystems as operational.

---

## Step 3: Verify the System

Run a quick health check from your terminal:

```bash
curl http://localhost:8000/health/live
```

**Expected output:**

```json
{"status": "alive"}
```

Now check readiness (all subsystems reporting in):

```bash
curl http://localhost:8000/health/ready
```

**Expected output:**

```json
{
  "ready": true,
  "local_llm_ready": true,
  "scheduler_running": true,
  "degraded_reasons": []
}
```

If `ready` is `false`, check `degraded_reasons` — it tells you exactly what's not ready yet. The local LLM can take up to 2 minutes to load the model on first start.

---

## Step 4: View the Soul

Lancelot ships with a default Soul (constitutional governance document) already active. Check it:

```bash
curl http://localhost:8000/soul/status
```

**Expected output:**

```json
{
  "active_version": "v1",
  "mission": "Serve as a loyal, transparent, and capable AI agent...",
  "autonomy_level": "supervised",
  "invariants_passing": true
}
```

The Soul defines what Lancelot can do autonomously (classify, summarize, redact) and what requires your approval (deploy, delete, financial transactions). You can customize it later — see [Authoring Souls](authoring-souls.md).

---

## Step 5: Send Your First Governed Action

Send a message through the governed pipeline:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "Summarize the key principles of good software architecture"}'
```

**What happens behind the scenes:**

1. Input is sanitized (prompt injection detection, homoglyph normalization)
2. Intent is classified (this is a `KNOWLEDGE_REQUEST`)
3. The model router selects the appropriate LLM lane
4. The response is generated
5. A receipt is created recording the entire action

**Expected output:**

```json
{
  "response": "Here are the key principles of good software architecture...",
  "receipts": ["receipt_abc123"]
}
```

You just ran your first governed action. Every step was policy-checked, routed, and receipted.

---

## Step 6: View the Receipt

Every action in Lancelot produces a receipt — an auditable record of what happened, which model was used, what policy decisions were made, and whether the action succeeded.

You can view recent receipts in the War Room under the **Receipts** panel, or query the API:

```bash
curl http://localhost:8000/router/decisions
```

This shows recent routing decisions — which lane was selected, which model handled the request, and the reasoning behind it.

In the War Room, navigate to the governance panel to see:

- The risk tier assigned to your action
- The policy decision (approved/denied/escalated)
- The complete governance trace

---

## Step 7: Try a Governed Tool Action (Optional)

If you want to see governance in action on a real tool execution, try asking Lancelot to do something that involves the Tool Fabric:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "Write a Python function that calculates fibonacci numbers and save it to workspace"}'
```

This triggers the full governance pipeline:

1. Intent classified as `EXEC_REQUEST`
2. Planning pipeline builds a PlanArtifact
3. Policy engine evaluates the risk tier
4. Tool Fabric executes in the Docker sandbox
5. Verifier checks the result
6. Receipt captures the entire trace

---

## What to Explore Next

Now that you have a running instance:

| Want to... | Go to... |
|-----------|---------|
| Understand the full architecture | [Architecture](architecture.md) |
| Learn how governance works | [Governance](governance.md) |
| Customize Lancelot's behavior | [Authoring Souls](authoring-souls.md) |
| Configure providers and models | [Configuration Reference](configuration-reference.md) |
| Set up integrations (Telegram, Slack, Discord, etc.) | [Installation Guide](installation.md) |
| Understand the security model | [Security Posture](security.md) |
| Operate the War Room dashboard | [War Room Guide](war-room.md) |

---

## Troubleshooting

### Docker not running

**Symptom:** Installer fails with "Docker is not running."

**Fix:** Open Docker Desktop and wait for it to fully start. On Windows, ensure WSL 2 is installed (`wsl --install`). Verify with `docker info`.

### Local LLM won't start

**Symptom:** Health check shows `local_llm_ready: false` after 2+ minutes.

**Fix:** Check if model weights were downloaded:
```bash
ls local_models/weights/
```
If empty, re-run the installer with `npx create-lancelot --resume`, or manually download the model (see the [Installation Guide](installation.md)).

### Port already in use

**Symptom:** `Bind for 0.0.0.0:8000: address already in use`

**Fix:** Another service is using port 8000 or 8080. Stop it, or edit `docker-compose.yml` to change the port mapping (e.g., `"9000:8000"`).

### API key errors

**Symptom:** Chat requests fail with authentication errors.

**Fix:** Open your `.env` file and verify the API key has no extra spaces or quotes. Restart after editing: `docker compose restart`.

### Out of memory

**Symptom:** Container keeps restarting, logs show memory errors.

**Fix:** The local model needs ~6GB RAM. If your system is constrained, either skip the local model (`--skip-model` during install) or reduce context in `.env`:
```ini
LOCAL_MODEL_CTX=2048
```
