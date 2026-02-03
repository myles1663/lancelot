# 🛡️ Project Lancelot

**Your AI-Powered Digital Knight** - An autonomous agent system that executes tasks, manages files, and communicates through your preferred channels (Telegram, Google Chat).

![Lancelot Logo](static/logo.jpeg)

## What is Lancelot?

Lancelot is a self-hosted AI assistant that operates as your digital knight. It combines the power of Google's Gemini AI with autonomous execution capabilities, allowing it to plan, research, and execute complex tasks on your behalf. Think of it as an AI agent that can actually *do* things, not just talk about them.

## 🚀 Quickstart

### Prerequisites
- Docker Desktop
- Python 3.11+ (for the launcher)
- A Gemini API Key ([Get one free](https://aistudio.google.com/app/apikey))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/lancelot.git
   cd lancelot
   ```

2. **Create your environment file**
   ```bash
   cp config/example.env .env
   ```

3. **Edit `.env` with your settings**
   - Add your `GEMINI_API_KEY`
   - Configure Telegram or Google Chat

4. **Start Lancelot**
   ```bash
   docker-compose up -d
   ```

5. **Open the War Room**
   - Navigate to `http://localhost:8501`
   - Or run `python src/ui/lancelot_gui.py` for the native launcher

## 📁 Project Structure

```
lancelot/
├── src/
│   ├── core/          # Orchestration, routing, security
│   ├── agents/        # Planner, Verifier, Crusader
│   ├── ui/            # War Room, Launcher, Onboarding
│   ├── integrations/  # Telegram, Google Chat, MCP
│   ├── memory/        # RAG, indexing, vault
│   └── shared/        # Utilities, logging
├── config/            # Example configuration files
├── docs/              # Documentation
├── tests/             # Test suite
└── static/            # UI assets
```

## ⚙️ Configuration

All configuration is done through environment variables. See [`config/example.env`](config/example.env) for all options.

### Model Configuration
Models can be configured in `config/models.example.yaml`. Lancelot supports:
- **Primary Model**: Main conversation and reasoning
- **Orchestrator**: Planning and task delegation
- **Utility**: Quick, lightweight tasks

## 📖 Documentation

- [Onboarding Guide](docs/onboarding/)
- [War Room Usage](docs/war-room/)
- [Architecture Overview](docs/architecture/)
- [Specifications](docs/specs/)

## 🔐 Security

- All secrets are stored in `.env` (never committed)
- Vault encryption for sensitive data
- Rate limiting and action receipts
- Sandboxed code execution

## 📜 License

MIT License - See [LICENSE](LICENSE) for details.
