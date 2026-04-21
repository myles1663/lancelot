# create-lancelot

Interactive `npx` installer for [Project Lancelot](https://github.com/myles1663/lancelot) - your AI-powered autonomous agent.

## Usage

```bash
npx create-lancelot
```

The installer guides you through the standard local setup process:

1. **Prerequisites check** - Node.js, Git, Docker Desktop, Docker Compose, disk space, RAM, GPU detection
2. **Install location** - choose where to install Lancelot
3. **LLM Provider** - select Gemini (recommended), OpenAI, Anthropic, xAI, or NVIDIA and enter your API key
4. **Communications** - configure Telegram, Google Chat, or skip
5. **War Room auth** - choose local credentials or enterprise OIDC; local auth requires an operator-chosen username and never falls back to a default `admin` account
6. **Repository clone** - pulls the latest Lancelot from GitHub
7. **Configuration** - generates `.env`, patches `docker-compose.yml` for your system
8. **Model download** - downloads the local utility model with progress and resume support
9. **Docker build and start** - builds images, starts services, waits for health check
10. **Done** - War Room is live at `http://localhost:8000/war-room`

## Options

| Flag | Description |
|------|-------------|
| `-d, --directory <path>` | Installation directory (default: `./lancelot`) |
| `--provider <name>` | Pre-select provider: `gemini`, `openai`, `anthropic`, `xai`, or `nvidia` |
| `--resume` | Resume an interrupted installation |

## Features

- **Cross-platform**: Windows, macOS, and Linux
- **Resume support**: Ctrl+C during install, then `npx create-lancelot --resume` to continue
- **GPU detection**: Automatically detects NVIDIA GPUs and configures GPU layers
- **API key validation**: Validates your API key against the provider API before proceeding
- **Smart patching**: Adjusts `docker-compose.yml` for your hardware
- **Onboarding bypass**: Writes the onboarding snapshot so War Room is ready immediately
- **Automated smoke coverage**: Installer-owned tests verify the CLI contract, run the real `create-lancelot` entrypoint through a fixture-backed happy path, cover the resume-path behavior, execute a real filesystem smoke that writes `.env`, patches `docker-compose.yml`, and marks onboarding complete through the live CLI entrypoint, and run the actual clone/build/up/health/host-agent modules against a local harness instead of a substitute runtime
- **No default local admin**: local War Room auth requires an explicit operator-chosen username; the installer does not silently provision `admin`

## Requirements

- **Node.js 18+** (for `npx`)
- **Git** (to clone the repository)
- **Docker Desktop** (or Docker Engine plus Compose v2 on Linux)
- **10+ GB disk space** (model plus Docker images)
- **8+ GB RAM** (recommended)

## Development

```bash
cd installer
npm install
node bin/create-lancelot.mjs --help
npm test
```

## Fresh-Machine Proof Collection

For enterprise reviews, collect a sanitized proof bundle immediately after a real install on the target machine:

```bash
cd /path/to/installed/lancelot
node installer/scripts/collect-install-proof.mjs \
  --install-dir . \
  --output-dir ./installer-proof
```

The collector writes:

- `installer-proof/installer-proof.json` - machine-readable evidence
- `installer-proof/installer-proof.md` - reviewer-friendly summary
- `installer-proof/sanitized.env` - redacted environment snapshot

It verifies the generated `.env`, `docker-compose.yml`, onboarding snapshot, model weights, Lancelot health endpoints, Host Agent health, and local Git/Docker tooling without copying raw secrets into the bundle.

## License

Business Source License 1.1 (BSL 1.1) - See the main [Lancelot LICENSE](../LICENSE) for details.
