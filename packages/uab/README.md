# Universal App Bridge (UAB)

**Framework detection and desktop app control for AI agents.**

UAB identifies the best available control path for a running application, records that choice, and reuses it on subsequent sessions. The goal is explicit desktop control with predictable fallbacks, not generic screen automation.

The runtime also exposes its discovery model directly. Embedding systems can inspect the registered framework hooks, framework-detection signatures, method inventory, and per-operation control plans instead of treating UAB as a black box.

## Install

UAB can run as a local library, CLI, or HTTP service. The installer sets up the daemon, browser bridge, authentication key, and optional agent integration files on the host machine.

```bash
# GUI installer
cd installer && npm install && npx electron src/main.js

# CLI install
uab-bridge install
```

The installer:
- Starts UABServer as a system service (auto-starts on boot)
- Installs the Chrome extension for browser bridge
- Writes optional skill files for Claude Co-work and Claude Code
- Generates an API key for authenticated access
- Detects host network for VM accessibility

Agents can also use UAB directly through the CLI or HTTP JSON API.

## The Core Runtime: Framework Detection And Control Routing

Most automation tools require you to know what app you're controlling and how to connect. UAB instead scans the host, identifies framework signatures, records app profiles, and selects a route from the available adapters:

```
        +----------------------------------------------------------+
        |       Framework Detection And Route Selection             |
        |                                                          |
        |  1. SCAN ---------> DLL module scanning                  |
        |     "What's running?"   Batch process enumeration        |
        |                         Window title fetching            |
        |                                   |                      |
        |  2. IDENTIFY -----> Framework signature matching         |
        |     "What framework?"   electron.exe -> Electron          |
        |                         qt6core.dll  -> Qt6               |
        |                         xlcall32.dll -> Office            |
        |                         jvm.dll      -> Java              |
        |                                   |                      |
        |  3. REGISTER -----> Runtime map + JSON persistence       |
        |     "Profile index"     O(1) lookup by PID or name       |
        |                         Dual-indexed (exe + PID)         |
        |                         Git-friendly registry.json       |
        |                                   |                      |
        |  4. CONNECT ------> Plugin cascade with fallback         |
        |     "Route order"       CDP -> COM -> UIA (automatic)     |
        |                         Preferred method stored          |
        |                                   |                      |
        |  5. RECORD -------> Update registry with results         |
        |     "Reuse route"      Store preferred control method    |
        |                         Cache element trees              |
        |                         Track connection health          |
        +----------------------------------------------------------+
```

### What The Runtime Adds

| Traditional Automation | UAB Runtime |
|----------------------|---------------------|
| You specify the app and how to connect | UAB scans the system and builds profiles for running apps |
| Hard-coded framework assumptions | DLL scanning identifies the exact framework with confidence scores |
| No persisted route state | Registry persists detected profiles and selected routes in JSON |
| Single control method | Route planner tries the preferred method first, then fallbacks |
| Manual configuration per app | Defaults to scan and registry persistence; configurable when needed |

## Quick Start

### As a Library

```typescript
import { UABConnector } from 'universal-app-bridge';

const uab = new UABConnector();
await uab.start();

// 1. SCAN - Discover everything running
const apps = await uab.scan();
// -> 79 apps found, frameworks identified, profiles registered

// 2. FIND - Registry-first lookup (live detection fallback)
const excel = await uab.find('excel');
// -> Instant hit from registry (O(1) Map lookup)

// 3. CONNECT - Route selected from the available adapters
const conn = await uab.connect('excel');
// -> { pid: 5678, name: 'EXCEL', framework: 'office', method: 'office-com+uia', elementCount: 342 }

// 4. QUERY - Search the UI tree
const buttons = await uab.query(conn.pid, { type: 'button', label: 'Save' });

// 5. ACT - Perform actions (permission-checked, retried, cache-aware)
await uab.act(conn.pid, buttons[0].id, 'click');

// Next session: scan() can reuse registry.json instead of rediscovering everything
await uab.stop();
```

### As a CLI (for any AI agent)

The CLI outputs pure JSON - designed for Claude, GPT, or any agent calling via bash:

```bash
# Scan and register all running apps
uab scan
# -> { "success": true, "apps": [...79 apps with frameworks...] }

# List known apps from registry (no scan needed)
uab apps
# -> Instant recall from registry.json

# Registry-first search with live detection fallback
uab find "notepad"

# Connect with automatic method selection
uab connect notepad
# -> { "pid": 1234, "method": "win-uia", "elementCount": 15 }

# Query and act
uab query 1234 --type button --label "Save"
uab act 1234 btn_42 click

# Probe Excel COM readiness
uab excel-probe

# Build a reproducible Excel workbook benchmark artifact plus proof manifest
uab excel-benchmark --rows 2000 --output data/uab-benchmarks/demo.xlsx --manifest data/uab-benchmarks/demo.benchmark.json

# Registry persists between sessions - next time reuses saved discovery state
uab profiles
# -> Shows all known apps with framework info and preferred methods
```

### As an HTTP Server (for remote / server-side agents)

Run UAB as a REST API so agents on other machines, in containers, or in cloud environments can control desktop apps remotely:

```bash
# Start the server (localhost only)
uab serve --port 3100

# Listen on all interfaces (for VM or remote access)
uab serve --port 3100 --host 0.0.0.0

# With authentication (recommended for non-localhost)
uab serve --port 3100 --host 0.0.0.0 --api-key my-secret-key
```

```bash
# From any HTTP client or remote agent:
curl -X POST http://localhost:3100/scan
curl -X POST http://localhost:3100/find -d '{"query":"notepad"}'
curl -X POST http://localhost:3100/connect -d '{"target":"notepad"}'
curl -X POST http://localhost:3100/query -d '{"pid":1234,"selector":{"type":"button"}}'
curl -X POST http://localhost:3100/act -d '{"pid":1234,"elementId":"btn_1","action":"click"}'
curl -X POST http://localhost:3100/plan -d '{"pid":1234,"action":"hotkey"}'
curl -X POST http://localhost:3100/plan -d '{"pid":1234,"action":"describe","context":{"preferredRole":"connection","preferredControl":"precise","weights":{"cost":4,"control":4,"affinity":0},"note":"Prefer structured low-cost inspection over API-backed vision"}}'
curl -X POST http://localhost:3100/excel/probe -d '{}'
curl -X POST http://localhost:3100/excel/benchmark -d '{"rowCount":2000,"outputPath":"data/uab-benchmarks/demo.xlsx","manifestPath":"data/uab-benchmarks/demo.benchmark.json"}'
curl -X POST http://localhost:3100/open -d '{"target":"notepad"}'
curl -X POST http://localhost:3100/focus -d '{"pid":1234}'
curl -X POST http://localhost:3100/describe -d '{"pid":1234}'

# P6 - OS raw input injection for spatial gestures
curl -X POST http://localhost:3100/drag -d '{"pid":1234,"path":[{"x":100,"y":200},{"x":300,"y":200}],"button":"left"}'
curl -X POST http://localhost:3100/scroll -d '{"pid":1234,"x":500,"y":400,"amount":3}'

# Health check
curl http://localhost:3100/health
```

```typescript
// Or programmatically:
import { UABServer } from 'universal-app-bridge/server';

const server = new UABServer({ port: 3100, host: '0.0.0.0', apiKey: 'secret' });
await server.start();
// Clients POST JSON to /scan, /connect, /query, /act, /open, /focus, /describe, etc.
```

### Environment Auto-Detection

UAB automatically detects its runtime context and tunes behavior accordingly:

| Environment | Session | Persistence | Rate Limit | Extension Bridge |
|-------------|---------|-------------|------------|-----------------|
| **Desktop** | Session 1+ | Persistent connections | 100/min/PID | Enabled |
| **Server** | Session 0 (SSH/service) | Stateless | 60/min/PID | Disabled |
| **Container** | Docker/WSL | Stateless | 30/min/PID | Disabled |

```bash
# Check what UAB detected:
uab env
# -> { "environment": { "mode": "desktop", "hasDesktop": true, ... }, "defaults": { ... } }
```

**One codebase with environment defaults** - UAB selects runtime defaults from the detected host context.

## Architecture

```
Agent Runtime (Claude / GPT / Any AI Agent)
         |
    Library API  or  CLI (JSON)  or  HTTP Server (REST)
         |
+--------+---------------------------------------------------+
|              Universal App Bridge (UAB)                      |
|                                                             |
|  +-------------+  +------------+  +---------------------+  |
|  | Framework    |  |  App       |  |   UAB Connector     |  |
|  |  Detector    |  |  Registry  |  |   (Public API)      |  |
|  |             |  |  (Brain)   |  |                     |  |
|  | DLL scan    |  | Map + JSON |  | scan() find()       |  |
|  | Batch enum  |  | O(1) lookup|  | connect() query()   |  |
|  | Signatures  |  | Persist    |  | act() state()       |  |
|  +------+------+  +-----+------+  +----------+----------+  |
|         |               |                     |             |
|         +---------------+---------------------+             |
|                         |                                   |
|  +----------------------+--------------------------------+  |
|  |                  Plugin Manager                        |  |
|  |  +----------+ +----------+ +----------+ +----------+ |  |
|  |  |Chrome Ext| | Browser  | | Electron | |  Office  | |  |
|  |  |  (WS)    | |  (CDP)   | |  (CDP)   | |(COM+UIA) | |  |
|  |  +----------+ +----------+ +----------+ +----------+ |  |
|  |  +----------+ +----------+ +----------+ +----------+ |  |
|  |  |   Qt     | |   GTK    | |  Java    | | Flutter  | |  |
|  |  |  (UIA)   | |  (UIA)   | |(JAB->UIA) | |  (UIA)   | |  |
|  |  +----------+ +----------+ +----------+ +----------+ |  |
|  |  +----------+ +----------+                              |  |
|  |  | Win-UIA  | |  Vision  |                              |  |
|  |  | (A11y)   | |(AI last  |                              |  |
|  |  |          | | resort)  |                              |  |
|  |  +----------+ +----------+                              |  |
|  +-------------------------------------------------------+  |
|                                                             |
|  +----------+ +----------+ +----------+ +--------------+   |
|  |  Cache   | |Permission| |  Retry   | | Chain Engine |   |
|  | (3-tier) | | (Audit)  | |(Backoff) | | (Workflows)  |   |
|  +----------+ +----------+ +----------+ +--------------+   |
|                                                             |
|  +------------------+  +--------------------------------+   |
|  | Control Router   |  |  Connection Manager            |   |
|  | (Cascade+Fallback|  |  (Health+Reconnect+Cleanup)    |   |
|  +------------------+  +--------------------------------+   |
+-------------------------------------------------------------+
         |
    Operating System (CDP, UIA, COM, PowerShell, WMI)
         |
    Desktop Applications
```

### The Cascade Pattern

UAB picks a control method for each operation automatically. The standalone runtime exposes this through a method inventory plus a runtime planner that scores every available method against the current operation. The method inventory records speed, outcome quality, control precision, and cost descriptors, and the planner combines those with action-specific role/control preferences, method boosts, and live penalties to rank the active connection path and fallbacks.

The important runtime detail is that `/plan` is not just a thin wrapper over static metadata. `UABConnector.planOperation()` now injects live route order, the active connected method, environment mode, Session 0 bridge requirements, direct-API availability, vision availability, and connection-health signals before scoring each method. The returned plan includes a `runtimeEvidence` array explaining which live facts changed the ranking:

```
Priority 1: Direct API / MCP endpoint (when the app exposes one)
Priority 2: Chrome Extension Bridge (browsers - no relaunch needed)
Priority 3: Browser CDP (browsers - with debug flag)
Priority 4: Framework Hook (Electron CDP, Office COM, Qt/GTK/Java/Flutter hook wrappers)
Priority 5: Windows UI Automation (win-uia fallback - any windowed app)
Priority 6: Keyboard Native (shortcuts, hotkeys, text input - fastest for commands)
Priority 7: OS Raw Input Injection (drag, scroll, gestures - SendInput/CGEventPost/xdotool)
     Vision Analysis: Screenshot + AI (reading state, verifying results - the agent's eyes)
```

The cascade is operation-specific, not application-specific. A single Blender sculpting session can use keyboard shortcuts for commands, drag events for brush strokes, scroll events for zooming, and screenshots for verification.

> **P6 - OS Raw Input Injection** injects mouse drag, scroll, and gesture events directly into the OS input stream via `SendInput()`. Any application receives these exactly as if a human moved the mouse. This enables sculpting in Blender, painting in Photoshop, drawing in any canvas app - operations that require continuous held-button mouse movement.

## Framework Detection Deep Dive

The standalone runtime now exposes this contract directly:
- `hookInventory()` / `GET /info.frameworkHooks` - all registered framework hooks
- `signatureInventory()` / `GET /info.frameworkSignatures` - framework detection signatures
- `concertoInventory()` / `GET /info.concertoMethods` - operation-level method inventory
- `planOperation()` / `POST /plan` - per-operation control-method planning with live runtime evidence plus optional caller overrides for weights, preferred role/control, and method penalties

### Phase 1: Detection

UAB scans the system using **three batched PowerShell calls** (not per-process - batched for speed):

1. **WMI Process Enumeration** - Get all running processes with PIDs, names, paths, command lines
2. **Batch DLL Module Scan** - One PowerShell call scans loaded modules for ALL processes (batches of 50)
3. **Batch Window Title Scan** - One P/Invoke call via `EnumWindows` gets all visible window titles

**Result:** Batched full-system scans stay interactive without per-process PowerShell startup overhead, but actual timing and controllable-app counts vary significantly by host, process mix, and PowerShell responsiveness. Use `uab scan` on the target machine for the real measurement.

### Phase 2: Framework Identification

Each detected process is matched against **framework signatures**:

```typescript
// Example: How UAB identifies an Electron app
{
  framework: 'electron',
  modules: ['electron.exe', 'libcef.dll', 'chrome_elf.dll', 'v8.dll'],
  filePatterns: ['resources/app.asar', 'resources/app.asar.unpacked'],
  commandLine: ['--type=renderer', 'electron', 'app.asar'],
  baseConfidence: 0.9
}
```

Confidence accumulates: base score + module matches + command-line matches + file pattern matches. An Electron app loading `chrome_elf.dll` AND having `resources/app.asar` gets confidence 0.95.

**10 framework signatures** built in: Electron, Qt5, Qt6, GTK3, GTK4, WPF, .NET, Flutter, Java, Office.

Plus **fast-path detection** for browsers (Chrome, Edge, Brave) and Office apps (Word, Excel, PowerPoint, Outlook) by executable name.

### Phase 3: Registry & Persistence

Every detected app is registered in the **App Registry** - UAB's brain:

```typescript
// What the registry stores per app
interface AppProfile {
  executable: string;       // Stable key: "code.exe"
  name: string;             // "Visual Studio Code"
  pid: number;              // Last known PID
  framework: FrameworkType; // "electron"
  confidence: number;       // 0.95
  preferredMethod: string;  // "browser-cdp", "office-com+uia", "win-uia", etc.
  path: string;             // Full executable path
  windowTitle: string;      // "project - Visual Studio Code"
  lastSeen: number;         // Unix timestamp
  tags: string[];           // User-defined categorization
}
```

The registry uses **dual-indexed Maps** for O(1) lookups:
- `Map<executable, AppProfile>` - lookup by executable name
- `Map<pid, executable>` - lookup by PID -> executable -> profile

**JSON persistence:** The entire registry is saved to `data/uab-profiles/registry.json` - a single, git-friendly file with readable diffs. No database required.

### Phase 4: Registry-First Lookup

When you call `find("excel")`, UAB doesn't scan the system again. It:

1. **Checks the registry first** - O(1) Map lookup, case-insensitive substring match
2. **Returns the registry hit without rescanning**
3. **Only falls back to live detection** if not in registry

This is why the first `scan()` can take several seconds, while subsequent `find()` calls usually return much faster from the persisted registry.

### Phase 5: Registry Update

After each successful connection, UAB updates the registry with what worked:

```typescript
// After connecting to VS Code via CDP:
registry.update('code.exe', {
  preferredMethod: 'browser-cdp',  // Store the exact working method
  pid: 12345,                // Update last known PID
  lastSeen: Date.now()       // Update timestamp
});
```

Next time you connect to VS Code, UAB tries CDP first because the registry recorded it as the preferred method.

## Supported Adapters

The source tree currently contains **11 adapter directories** under `src/plugins/`, but the two public runtimes do not register the exact same set. `UABConnector` registers `direct-api`, an optional `chrome-extension` bridge, and the structured framework hooks. `UABService` uses the same structured hooks but swaps the connector-only direct API / extension paths for the universal `vision` fallback.

| Runtime Route | Available In | Plugin | Method | Apps Covered |
|---------------|--------------|--------|--------|--------------|
| **Direct API apps** | Connector | DirectApiPlugin | `direct-api` | Apps that expose a local control endpoint |
| **Chrome/Edge/Brave** | Connector (optional) | ChromeExtPlugin | `chrome-extension` | Any Chromium browser - tabs, cookies, DOM, storage, JS exec |
| **Chrome/Edge/Brave** | Connector + Service | BrowserPlugin | `browser-cdp` | Same browsers, requires `--remote-debugging-port` |
| **Electron** | Connector + Service | ElectronPlugin | `electron-cdp` | VS Code, Slack, Discord, Notion, Obsidian, Spotify, Teams |
| **MS Office** | Connector + Service | OfficePlugin | `office-com+uia` | Word, Excel, PowerPoint, Outlook |
| **Qt 5/6** | Connector + Service | QtPlugin | `qt-uia` | VLC, Telegram Desktop, OBS Studio, VirtualBox, Wireshark |
| **GTK 3/4** | Connector + Service | GtkPlugin | `gtk-uia` | GIMP, Inkscape, GNOME apps |
| **WPF/.NET / Win32** | Connector + Service | WinUIAPlugin | `win-uia` | Windows enterprise apps, Visual Studio, generic fallback |
| **Flutter** | Connector + Service | FlutterPlugin | `flutter-uia` | Google apps, Ubuntu desktop apps |
| **Java Swing/FX** | Connector + Service | JavaPlugin | `java-jab-uia` | JetBrains IDEs, Android Studio |
| **Vision fallback** | Service | VisionPlugin | `vision` | Universal screenshot + input fallback when structured routes fail |

## Unified API

Every framework plugin maps its native UI tree into the same types:

### `uab.scan()` - Discover & Register

```typescript
const apps = await uab.scan();
// Apps are detected, frameworks identified, and profiles registered
// Registry persists to disk - subsequent sessions start from cached profiles
```

### `uab.find(name)` - Registry-First Lookup

```typescript
const results = await uab.find('slack');
// 1. Checks registry first -> returns if found without a fresh scan
// 2. Falls back to live detection -> registers result
```

### `uab.connect(target)` - Auto-Connect

```typescript
// By name (searches registry, then live-detects)
const conn = await uab.connect('notepad');

// By PID (checks registry, auto-detects if not found)
const conn = await uab.connect(1234);

// Returns: { pid, name, framework, method, elementCount }
```

### `uab.enumerate(pid)` - List UI Elements

```typescript
const tree = await uab.enumerate(pid);
// Cached for 5 seconds - repeated calls avoid a fresh lookup
```

### `uab.query(pid, selector)` - Search Elements

```typescript
const btns = await uab.query(pid, { type: 'button', label: 'Save' });
// Cached for 3 seconds, auto-invalidated after mutating actions
```

### `uab.act(pid, elementId, action, params?)` - Perform Actions

```typescript
await uab.act(pid, 'btn_1', 'click');
await uab.act(pid, 'input_3', 'type', { text: 'Hello' });
// Permission-checked -> retried on transient failure -> cache invalidated
```

## Production Hardening

### Three-Tier Cache

```
+------------------------------------------+
|              Element Cache               |
|                                          |
|  Tree Cache    |  5s TTL per PID         |
|  Query Cache   |  3s TTL, 50 max/PID    |
|  State Cache   |  2s TTL per PID         |
|                                          |
|  Auto-invalidation on mutating actions:  |
|  click, type, keypress, navigate, etc.   |
|                                          |
|  Safe (no invalidation):                 |
|  focus, hover, scroll, screenshot, etc.  |
+------------------------------------------+
```

### Permission & Safety Model

- **Risk classification:** safe / moderate / destructive
- **Rate limiting:** 100 actions/min per PID (configurable)
- **Audit log:** Last 1000 actions with timestamps, PIDs, elements, risk levels
- **Destructive action gating:** `close` requires explicit confirmation when blocking is enabled

### Health Monitoring

- 30-second health check intervals
- Auto-reconnect with exponential backoff (1s -> 2s -> 4s -> 8s)
- Stale connection cleanup after 5 minutes of failure
- Event callbacks for connection state changes

### Retry with Backoff

- Exponential backoff with 0-30% jitter
- Retryable error detection (ECONNRESET, timeout, EPIPE, socket hang up)
- Per-operation timeout with configurable limits
- Labeled operations for debugging

## Action Chains

Multi-step workflows with verification between steps:

```typescript
const chain = {
  name: 'fill-form',
  pid: 1234,
  steps: [
    { type: 'action', selector: { label: 'Name' }, action: 'type', params: { text: 'John' } },
    { type: 'wait', selector: { type: 'button', label: 'Submit' }, timeoutMs: 5000 },
    { type: 'action', selector: { label: 'Submit' }, action: 'click' },
  ],
};

const result = await chainExecutor.execute(chain);
```

## Chrome Extension Bridge

UAB includes a Chrome Extension (Manifest V3) that connects to your running browser via WebSocket - **no browser relaunch required**.

```
+--------------------+    WebSocket     +--------------------+
|   UAB Service      |<---(port 8787)-->|  Chrome Extension  |
|   (Node.js)        |    JSON protocol |  (Manifest V3)     |
+--------------------+                  +--------------------+
```

**Browser operations:** Tabs, cookies, localStorage, sessionStorage, navigation, JavaScript execution, and screenshots can be routed through the extension bridge without relaunching the browser.

## Co-work Bridge

UAB can be used from Claude Co-work through installer-written skill files. Co-work reaches UABServer through Chrome's localhost access, so it does not require VM port forwarding for the default local setup.

The Chrome extension acts as a relay: Co-work -> Chrome extension -> localhost:3100 -> UABServer -> desktop apps.

### Recursive Application Bridge

UAB stores successful control paths and can reuse them on later runs.

The **Flow Library** (`data/flow-library/`) stores pre-built interaction sequences for every app UAB has successfully controlled. Each flow captures the exact steps, input method, and known quirks discovered through real-world testing:

- **ChatGPT**: 1 Tab -> type -> Enter
- **Grok**: 2 Tabs -> keystroke activate -> clipboard paste -> Enter
- **Excel**: COM API methods (no UI automation needed)
- **Notepad**: Direct SendKeys type

When an agent encounters a new app, it checks `GET /flow/{appname}`. If a flow exists, the agent replays the recorded sequence. If no flow exists, UAB provides a framework-based default, and the agent can save the working sequence via `POST /flow` after success.

This creates a reusable execution loop: **Attempt -> Verify -> Store -> Reuse.** The flow library keeps successful paths as explicit records that any connected agent can replay.

### Structured UI Inspection

UAB exposes application structure as data so callers can inspect named controls before choosing an action path.

`POST /deep-query` scans the UI tree exposed by the active control route and returns named elements - buttons, inputs, links, menus, text - with their types, supported actions, and screen positions where available.

`POST /invoke` acts on an element by name through the route planner. This avoids tab-order automation or coordinate clicks when the accessibility tree exposes a stable control.

```bash
# Inspect ChatGPT controls
curl -X POST localhost:3100/deep-query -H "X-API-Key: KEY" -d '{"pid":28968}'
# -> 123 elements: buttons, links, inputs, conversations, model selector...

# Invoke a named button
curl -X POST localhost:3100/invoke -H "X-API-Key: KEY" -d '{"pid":28968, "name":"Copy", "occurrence":"last"}'
# -> Invokes the last Copy button, returns clipboard text
```

### Structured Inspection SDK

UAB prefers structured inspection before screenshots. Vision remains available as a fallback when framework hooks and accessibility APIs cannot expose enough state.

**Spatial Maps** organize UI elements into rows and columns so callers can reason over layout as structured data.

**Composite Engine** combines UIA tree, bounding rects, and text reading in speed-priority order. Vision is the fallback path, not the primary method.

**MCP Server** exposes native desktop-control tools (`desktop_scan`, `desktop_spatial_map`, action-chain tools, etc.) with stable names. MCP-compatible agents receive them through the standard tool handshake.

**Atomic Chains** execute multi-step action sequences in a single PowerShell session to reduce focus churn between steps.

**Invoke fallback cascade** tries 6 methods to click any element: InvokePattern -> SetFocus -> ValuePattern -> ExpandCollapse -> coordinate click -> parent invoke.

## MCP Setup

UAB exposes 17 native desktop control tools via the [Model Context Protocol](https://modelcontextprotocol.io). Any MCP-compatible agent gets direct access to `desktop_scan`, `desktop_spatial_map`, `desktop_invoke`, `desktop_flow`, and more - no skill files or HTTP calls needed.

**The UAB installer configures MCP automatically for Claude Desktop and Claude Code.** For other agents, follow the instructions below.

### Claude Desktop (auto-configured by installer)

The installer writes to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "node",
      "args": ["/path/to/uab/dist/mcp-server.js"]
    }
  }
}
```

Restart Claude Desktop after installation. The desktop control tools appear automatically in both chat and code mode.

### Claude Code (auto-configured by installer)

The installer adds the MCP permission to `~/.claude/settings.json`. To add manually via CLI:

```bash
claude mcp add desktop-control node /path/to/uab/dist/mcp-server.js
```

### Cursor

Add to Cursor's MCP settings (Settings > MCP Servers > Add):

```json
{
  "command": "node",
  "args": ["/path/to/uab/dist/mcp-server.js"]
}
```

### Windsurf / Other Editors

Add to your editor's MCP configuration:

```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "node",
      "args": ["/path/to/uab/dist/mcp-server.js"]
    }
  }
}
```

### Generic MCP Client

Any agent that supports MCP stdio transport can connect:

- **Command**: `node`
- **Args**: `["/path/to/uab/dist/mcp-server.js"]`
- **Transport**: stdio (JSON-RPC 2.0 over stdin/stdout)

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `desktop_scan` | Discover all running GUI applications |
| `desktop_connect` | Connect to an app by name or PID |
| `desktop_spatial_map` | Full UI layout as structured rows/columns (RawViewWalker) |
| `desktop_deep_query` | Find exposed elements, including inner Electron web content when the route supports it |
| `desktop_invoke` | Directly activate a named element through the route planner |
| `desktop_flow` | Read cached interaction flow for specific apps |
| `desktop_chain` | Atomic multi-step action sequence (no focus loss) |
| `desktop_keypress` | Send keyboard key |
| `desktop_hotkey` | Send keyboard shortcut |
| `desktop_act` | Click, type, select, expand, invoke by element ID |
| `desktop_ui_tree` | Get UI element tree |
| `desktop_find_elements` | Find elements by type/label |
| `desktop_window` | Window management (minimize, maximize, etc.) |
| `desktop_state` | Get app state and window properties |
| `desktop_focused` | Get currently focused element |
| `desktop_apps` | List previously discovered apps (registry read) |

## Session 0 Bridge

UAB works even when running in Session 0 (SSH, Windows Services). It automatically detects Session 0 and routes PowerShell through the Task Scheduler with `/IT` flag to bridge to the interactive desktop session.

## Documentation

| Document | What's Inside |
|----------|--------------|
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | Framework detection pipeline, cascade routing, plugin architecture, data flow |
| [**GETTING_STARTED.md**](GETTING_STARTED.md) | Install -> scan -> discover -> connect -> control walkthrough |
| [**API_REFERENCE.md**](API_REFERENCE.md) | Every method, parameter, and return type for UABConnector & AppRegistry |
| [**SUPPORTED_APPLICATIONS.md**](SUPPORTED_APPLICATIONS.md) | Tested apps with specific operations and benchmarks |
| [**SECURITY.md**](SECURITY.md) | Trust boundaries, permission model, audit trail |
| [**CONTRIBUTING.md**](CONTRIBUTING.md) | How to contribute, write plugins, code standards |
| [**CHANGELOG.md**](CHANGELOG.md) | Version history |

## Key Numbers

| Metric | Value |
|--------|-------|
| Runtime adapter directories | **11** under `src/plugins/` |
| Connector runtime adapters | **Up to 10** (`direct-api`, optional `chrome-extension`, Browser, Electron, Office, Qt, GTK, Java, Flutter, Win-UIA) |
| Service runtime adapters | **9** (Browser, Electron, Office, Qt, GTK, Java, Flutter, Win-UIA, Vision) |
| Framework signatures | **10** (Electron, Qt5, Qt6, GTK3, GTK4, WPF, .NET, Flutter, Java, Office) |
| Element types | **32** normalized types |
| Action types | **61** (UI + keyboard + window + Office + browser) |
| CLI commands | **20+** (all JSON output) |
| Source files | **30** TypeScript files (~11,700 LOC) |
| Apps detected | **79+** on typical Windows desktop |
| Registry lookup | **O(1)** via dual-indexed Maps |

## Design Rationale

Desktop applications expose different control surfaces: COM for Office, CDP for browsers and Electron, UI Automation for native Windows applications, and screenshots only when structured routes fail. UAB keeps those routes explicit and records which one worked for each application.

Framework detection plus route planning lets a caller scan a system, identify what is running, and choose an adapter based on runtime evidence. The registry persists profiles across sessions so later runs can skip rediscovery when the host state has not changed.

## Requirements

- **Node.js** >= 18.0.0
- **Windows** (primary platform - UIA, COM, PowerShell)
- Linux/macOS support via framework-specific plugins

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UAB_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warn`, `error` |
| `UAB_LOG_FILE` | _(none)_ | Optional file path for log output |
| `LOG_LEVEL` | `info` | Fallback log level (if UAB_LOG_LEVEL not set) |

## License

Universal App Bridge is licensed under the **Business Source License 1.1**.

**Permitted:** Personal use, academic research, evaluation, testing, open source projects.

**Requires commercial license:** Commercial agent runtimes, SaaS platforms, enterprise internal use (25+ employees), competing products, and deployments to 5+ users/devices.

**Patent notice:** This software is subject to pending patent applications. The Change Date license conversion does not grant patent rights beyond those stated in the License.

Each version converts to Apache 2.0 four years after release.

See [LICENSE](./LICENSE) for full terms.
