# create-lancelot

Single-command installer for [Project Lancelot](https://github.com/myles1663/lancelot) — your AI-powered autonomous agent.

## Usage

```bash
npx create-lancelot
```

The installer guides you through the entire setup process:

1. **Prerequisites check** — Node.js, Git, Docker Desktop, Docker Compose, disk space, RAM, GPU detection
2. **Install location** — choose where to install Lancelot
3. **LLM Provider** — select Gemini (recommended), OpenAI, or Anthropic and enter your API key
4. **Communications** — configure Telegram, Google Chat, or skip
5. **Repository clone** — pulls the latest Lancelot from GitHub
6. **Configuration** — generates `.env`, patches `docker-compose.yml` for your system
7. **Model download** — downloads the 5GB local utility model (with progress bar and resume support)
8. **Docker build & start** — builds images, starts services, waits for health check
9. **Done!** — War Room is live at `http://localhost:8000/war-room`

## Options

| Flag | Description |
|------|-------------|
| `-d, --directory <path>` | Installation directory (default: `./lancelot`) |
| `--provider <name>` | Pre-select provider: `gemini`, `openai`, or `anthropic` |
| `--skip-model` | Skip the local model download |
| `--resume` | Resume an interrupted installation |

## Features

- **Cross-platform**: Windows, macOS, and Linux
- **Resume support**: Ctrl+C during install, then `npx create-lancelot --resume` to continue
- **GPU detection**: Automatically detects NVIDIA GPUs and configures GPU layers
- **API key validation**: Validates your API key against the provider's API before proceeding
- **Smart patching**: Adjusts `docker-compose.yml` for your hardware (removes GPU blocks if no NVIDIA GPU)
- **Onboarding bypass**: Writes the onboarding snapshot so the War Room is ready immediately — no setup wizard

## Requirements

- **Node.js 18+** (for `npx`)
- **Git** (to clone the repository)
- **Docker Desktop** (or Docker Engine + Compose v2 on Linux)
- **10+ GB disk space** (5GB model + Docker images)
- **8+ GB RAM** (recommended)

## Development

```bash
cd installer
npm install
node bin/create-lancelot.mjs --help
```

## License

MIT — See the main [Lancelot LICENSE](../LICENSE) for details.
