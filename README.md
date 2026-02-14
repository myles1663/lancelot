# Project Lancelot

**Your AI-Powered Digital Knight** - An autonomous agent system with constitutional governance, tiered memory, provider-agnostic tool execution, and receipt-based accountability.

![Lancelot Logo](static/logo.jpeg)

## What is Lancelot?

Lancelot is a self-hosted AI assistant that operates as your digital knight. It combines multi-provider LLM routing (Gemini, OpenAI, Anthropic) with autonomous execution capabilities, constitutional governance (Soul), governed memory self-edits, and a provider-agnostic tool fabric for sandboxed code and file operations. Think of it as an AI agent that can actually *do* things, not just talk about them.

## Quickstart

### One-Command Install (Recommended)

The fastest way to get Lancelot running — just have **Docker Desktop** and **Node.js 18+** installed:

```bash
npx create-lancelot
```

The installer will guide you through everything: prerequisites check, provider and API key setup, communications configuration, model download, Docker build, and service startup. In about 5 minutes you'll have a fully running Lancelot instance.

Options:
- `npx create-lancelot --resume` — resume an interrupted install
- `npx create-lancelot --skip-model` — skip the 5GB local model download
- `npx create-lancelot --provider gemini` — pre-select a provider

### Manual Installation

<details>
<summary>Click to expand manual installation steps</summary>

#### Prerequisites
- Docker Desktop
- At least one LLM API key (Gemini, OpenAI, or Anthropic)

#### Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/myles1663/lancelot.git
   cd lancelot
   ```

2. **Create your environment file**
   ```bash
   cp config/example.env .env
   ```

3. **Edit `.env` with your settings**
   - Add your `GEMINI_API_KEY`, `OPENAI_API_KEY`, and/or `ANTHROPIC_API_KEY`
   - Configure Telegram or Google Chat (optional)

4. **Start Lancelot**
   ```bash
   docker-compose up -d
   ```

5. **Open the War Room**
   - **React War Room (recommended):** Navigate to `http://localhost:8000/war-room/`
   - **Legacy Streamlit UI:** Navigate to `http://localhost:8501`
   - Or run `python src/ui/lancelot_gui.py` for the native launcher

</details>

## Architecture

Lancelot is organized into six major subsystems, each gated by a feature flag:

| Subsystem | Feature Flag | Description |
|-----------|-------------|-------------|
| **Soul** | `FEATURE_SOUL` | Constitutional identity, versioned governance, amendment workflow |
| **Skills** | `FEATURE_SKILLS` | Modular capabilities with manifests, factory pipeline, marketplace |
| **Heartbeat** | `FEATURE_HEALTH_MONITOR` | Liveness/readiness probes, state transition receipts |
| **Scheduler** | `FEATURE_SCHEDULER` | Cron/interval job scheduling with gating pipeline |
| **Memory vNext** | `FEATURE_MEMORY_VNEXT` | Tiered memory (working/episodic/archival), context compiler, governed self-edits |
| **Tool Fabric** | `FEATURE_TOOLS_FABRIC` | Provider-agnostic tool execution, Docker sandbox, policy engine |

## Project Structure

```
lancelot/
├── src/
│   ├── core/              # Orchestration, routing, security
│   │   ├── memory/        # Memory vNext: block store, tiered storage, commits, compiler
│   │   ├── soul/          # Constitutional identity: store, linter, amendments, API
│   │   ├── skills/        # Modular skills: schema, registry, executor, factory
│   │   ├── health/        # Heartbeat: health types, monitor, API
│   │   ├── scheduler/     # Job scheduling: schema, service, executor
│   │   └── feature_flags.py
│   ├── tools/             # Tool Fabric
│   │   ├── contracts.py   # Capability interfaces (7 protocols)
│   │   ├── fabric.py      # Main orchestrator
│   │   ├── policies.py    # Security policy engine
│   │   ├── health.py      # Provider health monitoring
│   │   ├── router.py      # Capability-based provider routing
│   │   ├── receipts.py    # Tool-specific receipt extensions
│   │   └── providers/     # Local sandbox, UI templates, Antigravity
│   ├── warroom/           # React SPA (Vite + React 18 + TypeScript + Tailwind)
│   │   ├── src/api/       # Typed API client layer
│   │   ├── src/components/# Design system (MetricCard, TierBadge, StatusDot, etc.)
│   │   ├── src/layouts/   # Shell layout (Sidebar, Header, VitalsBar, NotificationTray)
│   │   ├── src/pages/     # 12+ tab pages (Command, Governance, Trust, APL, etc.)
│   │   └── src/hooks/     # usePolling, useWebSocket, useKeyboardShortcuts
│   ├── agents/            # Planner, Verifier, Crusader
│   ├── ui/                # Legacy Streamlit War Room, Launcher, Onboarding
│   │   └── panels/        # Soul, Skills, Health, Scheduler, Memory, Tool Fabric panels
│   ├── integrations/      # Telegram, Google Chat, MCP
│   └── shared/            # Utilities, logging, receipts
├── installer/             # create-lancelot CLI installer (npm package)
├── config/                # YAML configuration files
├── docs/                  # Documentation
│   ├── specs/             # Product, Functional, and Technical specifications
│   ├── blueprints/        # Implementation blueprints
│   └── operations/        # Runbooks
├── soul/                  # Soul version files (constitutional identity)
├── tests/                 # Test suite (1900+ tests)
└── static/                # UI assets
```

## Configuration

All configuration is done through environment variables and YAML files. See [`config/example.env`](config/example.env) for all options.

### Model Configuration
Models can be configured in `config/models.yaml`. Lancelot supports:
- **Local Model**: Mandatory GGUF model for utility/redaction tasks
- **Flagship Fast**: Standard reasoning (Gemini Flash, GPT-4o-mini, Claude Haiku)
- **Flagship Deep**: Complex reasoning (Gemini Pro, GPT-4o, Claude Sonnet)

### Feature Flags
All subsystems can be independently enabled or disabled via environment variables. See `src/core/feature_flags.py` for the full list.

## Documentation

- [Product Requirements](docs/specs/Product_Requirements_Document.md) - What Lancelot does and why
- [Functional Specifications](docs/specs/Functional_Specifications.md) - How each feature works
- [Technical Specifications](docs/specs/Technical_Specifications.md) - Architecture and component details
- [Tool Fabric Spec](docs/specs/Lancelot_ToolFabric_Spec.md) - Tool execution subsystem
- [Memory vNext Spec](docs/specs/Lancelot_vNext3_Spec_Memory_BlockMemory_ContextCompiler.md) - Memory subsystem
- [Operational Runbooks](docs/operations/runbooks/) - Day-to-day operations guides

## Security

- 96 security vulnerabilities identified and remediated across two hardening passes
- Symlink-safe workspace boundary enforcement
- Command denylist with precise token matching (shlex-based)
- Docker env var sanitization to prevent shell injection
- Atomic file writes with backup recovery for crash safety
- Thread-safe singletons with double-checked locking
- Input sanitization blocking prompt injection (16 patterns + homoglyph normalization)
- PII redaction via local model before external API calls
- All secrets stored in `.env` (never committed)
- Rate limiting and action receipts for every operation

## License

MIT License - See [LICENSE](LICENSE) for details.
