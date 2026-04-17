# Getting Started

Step-by-step guide: install UAB, discover your apps, and control your first desktop application — all through the Smart Function Discovery pipeline.

## Prerequisites

- **Node.js** >= 18.0.0
- **Windows 10/11** (primary platform)
- **PowerShell** 5.1+ (included with Windows)
- A desktop application to control (we'll use Notepad)

## Installation

### Option 1: GUI Installer (Recommended)
```bash
cd installer
npm install
npx electron src/main.js
```
The installer handles everything: service setup, Chrome extension, skill files, API key.

### Option 2: CLI Install
```bash
npm install
npm run build
node dist/cli.js install
```

### Option 3: Manual Setup
```bash
npm install
npm run build
node dist/cli.js serve --host 0.0.0.0 --port 3100 --api-key YOUR_KEY
```

## Quick Start

```bash
# Check server health (no auth needed)
curl http://localhost:3100/health

# Scan for apps (auth required)
curl -X POST http://localhost:3100/scan -H "X-API-Key: YOUR_KEY"

# Connect to an app
curl -X POST http://localhost:3100/connect -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" -d '{"target":"notepad"}'
```

## Verify Installation

```bash
node dist/uab/cli.js help
```

You should see a list of all available CLI commands with descriptions.

---

## The Smart Discovery Walkthrough

### Step 3: Scan the System

This is where the magic starts. Open Notepad (or any application), then:

```bash
node dist/uab/cli.js scan
```

UAB performs the full Smart Function Discovery pipeline:

1. **Enumerates all running processes** via WMI (batched)
2. **Scans loaded DLLs** for each process (batched, 50 at a time)
3. **Fetches window titles** via P/Invoke EnumWindows (single call)
4. **Matches framework signatures** (Electron, Qt, Office, etc.)
5. **Registers everything** in the App Registry

Output:

```json
{
  "success": true,
  "apps": [
    {
      "executable": "notepad.exe",
      "name": "notepad",
      "pid": 12340,
      "framework": "unknown",
      "confidence": 0.5,
      "windowTitle": "Untitled - Notepad"
    },
    {
      "executable": "code.exe",
      "name": "Code",
      "pid": 5678,
      "framework": "electron",
      "confidence": 0.95,
      "windowTitle": "project - Visual Studio Code"
    },
    {
      "executable": "excel.exe",
      "name": "EXCEL",
      "pid": 9012,
      "framework": "office",
      "confidence": 0.95,
      "windowTitle": "Budget.xlsx - Excel"
    }
  ],
  "count": 79
}
```

**What just happened:**
- UAB found 79+ controllable apps on the system
- Each app was identified by framework (Electron, Office, etc.)
- Everything was registered in `data/uab-profiles/registry.json`
- This knowledge persists across sessions

### Step 4: View Known Apps (Instant Recall)

Now that UAB has scanned once, it remembers everything:

```bash
node dist/uab/cli.js apps
```

This returns the full registry **instantly** (no scan needed). The data comes from the in-memory Map, backed by `registry.json`.

### Step 5: Smart Find

Search for an app by name — the registry is checked first:

```bash
node dist/uab/cli.js find notepad
```

Output:

```json
{
  "success": true,
  "apps": [
    {
      "executable": "notepad.exe",
      "name": "notepad",
      "pid": 12340,
      "framework": "unknown",
      "confidence": 0.5,
      "windowTitle": "Untitled - Notepad"
    }
  ]
}
```

**Smart lookup flow:**
1. Check registry (O(1) Map lookup) → **Found!** → Return immediately
2. If not in registry → live detect → register → return

First `find()` after a `scan()` is always instant. If you search for an app UAB hasn't seen before, it live-detects it and registers it for next time.

### Step 6: Connect

Connect to Notepad. UAB selects the best control method automatically:

```bash
# By name (fuzzy match)
node dist/uab/cli.js connect notepad

# Or by PID (exact)
node dist/uab/cli.js connect 12340
```

Output:

```json
{
  "success": true,
  "pid": 12340,
  "name": "notepad",
  "framework": "unknown",
  "method": "accessibility",
  "elementCount": 15
}
```

**What happened internally:**
1. Registry lookup for "notepad" → found profile
2. Plugin cascade: tried each plugin in order
3. No framework-specific plugin matched (framework: "unknown")
4. Win-UIA fallback succeeded → `method: "accessibility"`
5. Registry updated with `preferredMethod: "accessibility"` → **learning!**
6. Element tree enumerated → 15 elements found

### Step 7: Explore the UI Tree

See what elements are available:

```bash
node dist/uab/cli.js enumerate 12340
```

Returns a flattened list of all UI elements with IDs, types, labels, and available actions. Use `--depth 2` to limit tree depth.

### Step 8: Find Specific Elements

Search for elements by type and/or label:

```bash
# Find all buttons
node dist/uab/cli.js query 12340 --type button

# Find the text area
node dist/uab/cli.js query 12340 --type textarea

# Find by label
node dist/uab/cli.js query 12340 --label "File"
```

Results are cached for 3 seconds — repeated queries are instant.

### Step 9: Take Action

Interact with the app:

```bash
# Type text into the editor
node dist/uab/cli.js act 12340 <textareaId> type --text "Hello from UAB!"

# Send a keyboard shortcut
node dist/uab/cli.js hotkey 12340 ctrl+a   # Select all

# Take a screenshot
node dist/uab/cli.js screenshot 12340 --output notepad.png
```

Replace `<textareaId>` with the actual element ID from enumerate/query results.

Each action is:
- **Permission-checked** (rate limit + risk assessment)
- **Audit-logged** (timestamp, PID, action, element, risk level)
- **Cache-invalidating** (mutating actions clear the element cache)

### Step 10: Window Management

```bash
node dist/uab/cli.js window 12340 maximize
node dist/uab/cli.js window 12340 minimize
node dist/uab/cli.js window 12340 restore
node dist/uab/cli.js window 12340 move --x 100 --y 100
node dist/uab/cli.js window 12340 resize --width 800 --height 600
```

### Step 11: View the Registry (Persistent Knowledge)

See everything UAB has learned:

```bash
node dist/uab/cli.js profiles
```

Shows all registered apps with their full profiles — framework, confidence, preferred method, last seen timestamp. This data survives restarts.

---

## Using UAB as a Library

For programmatic use in your own agent:

```typescript
import { UABConnector } from 'universal-app-bridge';

async function main() {
  // Create a connector instance (framework-independent)
  const uab = new UABConnector({
    profileDir: 'data/uab-profiles',  // Where to persist registry
    persistent: true,                  // Enable health monitoring
    rateLimit: 100,                    // Max actions/min per PID
  });

  await uab.start();

  // Phase 1-3: Scan, identify, register
  const apps = await uab.scan();
  console.log(`Discovered ${apps.length} apps`);

  // Phase 4: Smart find + connect
  const results = await uab.find('notepad');
  if (results.length === 0) {
    console.log('Notepad not found. Please open it first.');
    await uab.stop();
    return;
  }

  const conn = await uab.connect(results[0].pid!);
  console.log(`Connected: ${conn.name} via ${conn.method} (${conn.elementCount} elements)`);

  // Interact
  const textAreas = await uab.query(conn.pid, { type: 'textarea' });
  if (textAreas.length > 0) {
    await uab.act(conn.pid, textAreas[0].id, 'type', { text: 'Hello from UAB!' });
    console.log('Typed text successfully');
  }

  // Check what UAB has learned
  const profile = uab.registry.byExecutable('notepad.exe');
  console.log(`Preferred method: ${profile?.preferredMethod}`);
  console.log(`Last seen: ${new Date(profile?.lastSeen || 0).toISOString()}`);

  await uab.stop();
}

main().catch(console.error);
```

---

## Using UAB with an AI Agent

UAB's CLI is designed for AI agent integration. The agent calls CLI commands via shell and parses JSON responses.

### Claude Code Integration

Add this to your agent's system prompt or CLAUDE.md:

```
You have access to the Universal App Bridge (UAB) CLI for controlling desktop apps.
UAB CLI: node dist/uab/cli.js <command>

Smart Discovery Commands:
  scan                    Scan system, identify all apps and frameworks
  apps                    List known apps (instant, from registry)
  find <name>             Smart lookup (registry first, live detection fallback)
  profiles                Show full registry with metadata

Connection & Control:
  connect <name|pid>      Connect to an app (auto-selects best method)
  enumerate <pid>         List all UI elements
  query <pid> --type T --label L  Search for elements
  act <pid> <id> <action> Perform an action
  state <pid>             Get app state
  keypress <pid> <key>    Send keypress
  hotkey <pid> key1+key2  Send hotkey combo
  window <pid> min|max|restore|close|move|resize  Window management
  screenshot <pid>        Capture screenshot

All output is JSON. Use scan first, then find/connect for subsequent interactions.
```

### Agent Workflow Pattern

The recommended pattern for AI agents:

1. **First interaction:** `scan` → discover all apps, register in registry
2. **Subsequent interactions:** `find <name>` → instant lookup from registry
3. **Control:** `connect` → `query` → `act` → `state` (verify)
4. **Repeat:** Registry remembers everything — no re-scanning needed

---

## Environment Configuration

```bash
# Logging
UAB_LOG_LEVEL=info          # debug, info, warn, error
UAB_LOG_FILE=./uab.log      # Optional file logging

# These are set programmatically via ConnectorOptions:
# profileDir, persistent, extensionBridge, loadProfiles, rateLimit
```

---

## Server Mode (Remote Access)

If your agent runs on a different machine or in a container, start UAB as an HTTP server:

### Quick Start

```bash
# Start the server (localhost only, port 3100)
uab serve

# With authentication
uab serve --port 3100 --api-key my-secret-key

# Bind to all interfaces (for remote access — use with caution)
uab serve --host 0.0.0.0 --api-key my-secret-key
```

### Making API Calls

```bash
# Health check
curl http://localhost:3100/health

# Scan for apps
curl -X POST http://localhost:3100/scan

# Find and connect to an app
curl -X POST http://localhost:3100/find -d '{"query":"notepad"}'
curl -X POST http://localhost:3100/connect -d '{"target":"notepad"}'

# Query and interact
curl -X POST http://localhost:3100/query -d '{"pid":1234,"selector":{"type":"button"}}'
curl -X POST http://localhost:3100/act -d '{"pid":1234,"elementId":"btn_1","action":"click"}'
```

### Programmatic Server Usage

```typescript
import { UABServer } from 'universal-app-bridge/server';

const server = new UABServer({
  port: 3100,
  apiKey: 'my-secret-key',
});

await server.start();
console.log(`UAB Server running at ${server.address}`);

// Clients can now POST to /scan, /connect, /query, /act, etc.
```

### Environment Detection

Check what UAB detected about your runtime:

```bash
uab env
# → { "environment": { "mode": "desktop", "hasDesktop": true, ... }, "defaults": { ... } }
```

UAB auto-tunes for desktop, server (SSH/service), and container environments — no config needed.

---

## Troubleshooting

### "No apps detected"
- Make sure the target app is running and has a visible window
- Check PowerShell works: `powershell -Command "Get-Process"`
- System processes (services without windows) are filtered out

### "Connection failed"
- **Electron apps:** May need `--remote-debugging-port=9222` flag
- **Office apps:** Must be fully loaded (not in splash screen)
- **Any app:** Win-UIA fallback should always work for windowed apps

### "Session 0" errors
- If running via SSH or as a Windows Service, UAB auto-bridges to the interactive session
- The desktop session must be logged in and not locked

### Slow first scan
- First scan takes 2-5 seconds (PowerShell startup + WMI + DLL scanning)
- Subsequent `find()` calls are instant (registry hit)
- Use `scan --electron` for faster Electron-only scans

### Stale PIDs in registry
- PIDs change when apps restart
- `scan()` updates PIDs for running apps
- `find()` falls back to live detection if registry PID is stale

---

## Next Steps

- [**API Reference**](API_REFERENCE.md) — Every method, parameter, and return type
- [**Architecture**](ARCHITECTURE.md) — Smart discovery pipeline internals
- [**Supported Applications**](SUPPORTED_APPLICATIONS.md) — Tested apps and operations
- [**Design Decisions**](docs/design-decisions.md) — Why UAB works the way it does
