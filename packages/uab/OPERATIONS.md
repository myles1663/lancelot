# UAB Operations Guide

Day-to-day operations, troubleshooting, and maintenance for UAB Bridge.

## Service Management

### Check Status

```bash
# CLI status (reports daemon, extension, skill file, server health)
uab-bridge status

# Quick health check
curl http://localhost:3100/health
```

### Start / Stop / Restart

**Windows (Task Scheduler):**
```cmd
:: Start
schtasks /run /tn "UAB Bridge"

:: Stop
schtasks /end /tn "UAB Bridge"

:: Check if running
schtasks /query /tn "UAB Bridge" /v /fo LIST | findstr "Status"
```

**macOS (launchd):**
```bash
# Start
launchctl start com.lancelot.uab-bridge

# Stop
launchctl stop com.lancelot.uab-bridge

# Restart
launchctl stop com.lancelot.uab-bridge && launchctl start com.lancelot.uab-bridge

# Check if running
launchctl list | grep uab-bridge
```

### Reinstall Service

If the scheduled task or launchd plist gets corrupted:

```bash
uab-bridge uninstall
uab-bridge install
```

## API Key

The API key is generated during installation and stored locally:

| Platform | Location |
|----------|----------|
| Windows  | `%LOCALAPPDATA%\UAB Bridge\api-key` |
| macOS    | `~/Library/Application Support/UAB Bridge/api-key` |

To view your key:
```bash
# Windows
type "%LOCALAPPDATA%\UAB Bridge\api-key"

# macOS
cat ~/Library/Application\ Support/UAB\ Bridge/api-key
```

To regenerate: delete the file and run `uab-bridge install` again.

All POST requests require the `X-API-Key` header. GET `/health` is exempt.

## Logs

| Platform | Location |
|----------|----------|
| Windows  | `%LOCALAPPDATA%\UAB Bridge\Logs\uab-bridge.log` |
| macOS    | `~/Library/Logs/UAB Bridge/uab-bridge.log` |

```bash
# Windows — tail the log
powershell Get-Content "%LOCALAPPDATA%\UAB Bridge\Logs\uab-bridge.log" -Tail 50 -Wait

# macOS
tail -f ~/Library/Logs/UAB\ Bridge/uab-bridge.log
```

## Skill File Locations

UAB writes skill files to multiple locations so all Claude products can find them:

### Claude Code CLI
```
~/.claude/plugins/marketplaces/claude-plugins-official/plugins/uab-bridge/skills/uab-bridge/SKILL.md
```

### Claude Co-work
```
# Windows
%APPDATA%\Claude\local-agent-mode-sessions\{session}\{workspace}\cowork_plugins\marketplaces\knowledge-work-plugins\uab-desktop-control\skills\uab-bridge\SKILL.md

# macOS
~/Library/Application Support/Claude/local-agent-mode-sessions/{session}/{workspace}/cowork_plugins/marketplaces/knowledge-work-plugins/uab-desktop-control/skills/uab-bridge/SKILL.md
```

If Co-work doesn't see UAB, run `uab-bridge install` to re-deploy the skill files to any new sessions.

## Chrome Extension

### Check Extension Status
```bash
uab-bridge ext-status
```

### Extension Not Loading

1. Verify the CRX file exists: `data/uab-bridge.crx`
2. Verify the extension ID: `cat data/extension-id.txt`
3. Check registry (Windows):
   ```cmd
   reg query "HKCU\SOFTWARE\Google\Chrome\Extensions\{ID}" /v path
   ```
4. Restart Chrome/Edge after installation — extensions load on browser startup

### Rebuild Extension
```bash
node scripts/pack-extension.js
```

## Troubleshooting

### Server Not Responding

```bash
# Check if anything is on port 3100
netstat -ano | findstr :3100      # Windows
lsof -i :3100                     # macOS

# If port is occupied by old process, kill it
# Windows:
for /f "tokens=5" %a in ('netstat -ano ^| findstr :3100 ^| findstr LISTENING') do taskkill /pid %a /f
# macOS:
kill $(lsof -ti :3100)

# Restart
uab-bridge install
```

### Cannot Connect to an App

1. **App not detected**: Run `/scan` first to refresh the process list
2. **Wrong PID**: Clear the registry cache — delete `data/uab-profiles/registry.json` and restart the server
3. **Electron app (no window)**: UAB picks the wrong process. The fix in v1.0.0 prefers the process with a window title. If it still fails, connect by PID directly:
   ```bash
   # Find the right PID
   tasklist /v /fi "imagename eq ChatGPT.exe"
   # Connect to the one with a window title
   curl -X POST localhost:3100/connect -H "X-API-Key: KEY" -d '{"target": 28968}'
   ```
4. **UWP app (Store apps)**: Some UWP apps sandbox their windows. Try focusing first with `/focus`, then connecting.

### Keystrokes Going to Wrong Window

This was fixed in v1.0.0. The WinUIA plugin now uses the Vision plugin's `EnumWindows` + `ForceForeground` approach which correctly targets the app window. If it still happens:

1. Use `/focus` to bring the app to foreground first
2. Then send keystrokes

### Co-work Doesn't See UAB

1. Restart Co-work (close fully, reopen)
2. Run `uab-bridge install` to re-deploy skill files to new sessions
3. Check the skill file exists in Co-work's directory (see Skill File Locations above)
4. Verify the API key is in the SKILL.md: `grep X-API-Key <skill-file-path>`

### Screenshots Are Blurry or Small

v1.0.0 added DPI-aware capture. If screenshots are still small:

1. Maximize the window before screenshotting: `POST /act {"pid": X, "action": "maximize"}`
2. Check your display scaling: 100% = 96 DPI (no scaling), 125% = 120 DPI, 150% = 144 DPI
3. The capture should automatically adjust for DPI scaling

## Network Configuration

### Default Binding

UABServer binds to `0.0.0.0:3100` — all network interfaces. This allows VMs (Co-work, WSL2) to reach the server.

### Firewall

Windows Firewall may prompt on first run. UAB only needs inbound TCP on port 3100. The server does NOT need internet access — it's localhost-only in practice.

If using a third-party firewall:
```
Allow inbound TCP port 3100 from 127.0.0.1 and 172.x.x.x (WSL subnet)
```

### Host Gateway IP

The installer detects the host IP that VMs can reach:
- **WSL/Hyper-V adapter**: Usually `172.x.x.1`
- **vmnet (VMware)**: Usually `192.168.x.1`
- **LAN**: Your Wi-Fi/Ethernet IP

View the detected IP:
```bash
node -e "const os = require('os'); const n = os.networkInterfaces(); for (const [name, addrs] of Object.entries(n)) { for (const a of addrs) { if (a.family === 'IPv4' && !a.internal) console.log(name, a.address); } }"
```

## Electron App Deep Inspection

### ELECTRON_ENABLE_REMOTE_DEBUGGING

The installer sets `ELECTRON_ENABLE_REMOTE_DEBUGGING=1` as a user environment variable. This enables Chrome DevTools Protocol for all Electron apps, giving UAB full DOM access instead of just the UIA shell.

**Verify it's set:**
```cmd
reg query "HKCU\Environment" /v ELECTRON_ENABLE_REMOTE_DEBUGGING
```

**Apps must be restarted** after setting this variable. The variable is picked up at app launch time.

**Does not work with**: UWP-packaged Electron apps from the Microsoft Store (e.g., ChatGPT Desktop). These apps run in a sandbox that strips environment variables. UAB falls back to UIA for these apps.

### CDP vs UIA

| Method | Depth | Speed | Works With |
|--------|-------|-------|------------|
| CDP    | Full DOM (every element) | Fast | Electron apps launched with debug flag |
| UIA    | Window + web content area | Moderate | Everything (universal fallback) |

When CDP is available, UAB uses it automatically. When not, it falls back to UIA + input injection.

### Flow Library

View available flows:
```bash
curl -s http://localhost:3100/flow/list
```

Get a specific app's flow:
```bash
curl -s http://localhost:3100/flow/grok
```

Save a new flow after discovering a working sequence:
```bash
curl -s -X POST http://localhost:3100/flow -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" -d '{"app_name":"myapp", "input_method":"...", "navigation_plan":[...]}'
```

Flow files are stored in `data/flow-library/`. To add flows for a new app, create a JSON file there or use the POST endpoint.

## MCP Server

Add to Claude Desktop config (`%APPDATA%/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "node",
      "args": ["C:\\path\\to\\UAB-repo\\dist\\mcp-server.js"]
    }
  }
}
```

Restart Claude Desktop. The desktop control tools will appear automatically.

## Maintenance

### Update UAB

```bash
git pull
npm run build
uab-bridge install   # Re-deploys skill files and restarts service
```

### Clear Caches

```bash
# Clear app registry (forces re-detection)
echo '{}' > data/uab-profiles/registry.json

# Clear screenshots
rm -rf data/screenshots/*
```

### Uninstall Completely

```bash
uab-bridge uninstall
```

This removes:
- Scheduled task / launchd plist
- CLI plugin registration
- Co-work skill files
- Does NOT remove: Chrome extension registry keys (become inert), API key file, environment variables
