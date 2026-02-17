# Lancelot Host Agent

A lightweight bridge that allows Lancelot (running inside Docker) to execute commands on your actual host operating system.

## Why This Exists

Lancelot runs inside a Docker container (Debian Linux). When you enable the **Host OS Bridge** (`FEATURE_TOOLS_HOST_BRIDGE`) in Kill Switches, Lancelot needs a way to reach your real OS. This agent runs on your host machine and accepts command execution requests from the container via HTTP.

## Quick Start

### 1. Start the Agent

**Windows:**
```
double-click start_agent.bat
```

**Or from terminal:**
```bash
cd host_agent
python agent.py
```

### 2. Enable in Lancelot

Go to **War Room > Kill Switches** and enable `FEATURE_TOOLS_HOST_BRIDGE`.

### 3. Test It

Ask Lancelot: *"What's my OS version?"* — it should now report your actual OS (e.g., Windows 11) instead of Debian Linux.

## Configuration

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| Port | `HOST_AGENT_PORT` | `9111` | Port the agent listens on |
| Token | `HOST_AGENT_TOKEN` | `lancelot-host-agent` | Shared auth token |

### Custom Token

Set the same token on both sides:

**Host (before starting agent):**
```bash
set HOST_AGENT_TOKEN=my-secret-token
python agent.py
```

**Container (in `.env` file):**
```
HOST_AGENT_TOKEN=my-secret-token
```

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Health check — returns platform info |
| GET | `/info` | Yes | Detailed host information |
| POST | `/execute` | Yes | Execute a command on the host |

### POST /execute

```json
{
  "command": "whoami",
  "cwd": "C:\\Users\\MyUser",
  "env": {"MY_VAR": "value"},
  "timeout": 60
}
```

Response:
```json
{
  "exit_code": 0,
  "stdout": "myuser\n",
  "stderr": "",
  "timed_out": false,
  "duration_ms": 45
}
```

## Security

- **Localhost only**: Binds to `127.0.0.1` — not reachable from the network
- **Token auth**: Every request (except `/health`) requires a Bearer token
- **Command denylist**: Dangerous commands (format drives, delete system files, fork bombs) are blocked
- **Output limits**: stdout capped at 100KB, stderr at 50KB
- **Timeout cap**: Maximum 10 minutes per command

## Requirements

- Python 3.8+ (no additional packages needed — stdlib only)
- The Lancelot container must be able to reach `host.docker.internal:9111`
