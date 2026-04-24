# Universal Application Bridge (UAB)

Lancelot's framework-level desktop application control system — structured, reliable UI automation without requiring app cooperation.

For the system architecture overview, see [Architecture](architecture.md). For security considerations, see [Security Posture](security.md). For operational procedures, see [UAB Runbook](operations/runbooks/uab.md).

---

## What UAB Is and Why

Most desktop automation approaches use either brittle vision+mouse techniques (screenshot → OCR → click at coordinates) or require the target application to expose an API. UAB takes a third path: **framework-level hooking**.

UAB connects to applications at the UI toolkit level — Chrome DevTools Protocol for Electron apps, COM Automation for Office, Windows UI Automation for native apps — to provide structured, programmatic access to any desktop application's UI. This gives Lancelot the ability to read, query, and manipulate application interfaces with the same precision a developer would have, without requiring app cooperation.

**Key design principles:**
- **Framework-level control** — hooks at the toolkit layer (CDP, COM, UIA), not at the pixel layer
- **Unified element model** — every framework maps to the same `UIElement` → `ActionType` → `ActionResult` types
- **Risk-aware** — every action is classified as LOW, MEDIUM, or HIGH risk
- **Receipt-traced** — every action produces a durable `AppControlReceipt`
- **Performance-optimized** — smart caching with automatic invalidation on mutations

**Feature flag:** `FEATURE_TOOLS_UAB` (default: `false`)

---

## Architecture

UAB operates across two layers connected by JSON-RPC 2.0:

```
Host Machine                           Docker Container (lancelot-core)
┌─────────────────────┐                ┌──────────────────────────┐
│  UAB Daemon          │  JSON-RPC 2.0  │  UABProvider              │
│  (Node.js, :7900)    │◄──────────────►│  (Python bridge)          │
│                      │   over HTTP     │                          │
│  ├── PluginManager   │                │  ├── Risk classification  │
│  ├── Detector        │                │  ├── Receipt emission     │
│  ├── ControlRouter   │                │  └── Governance gates     │
│  ├── ConnectionMgr   │                │                          │
│  ├── ElementCache    │                │  Tool Fabric              │
│  ├── PermissionMgr   │                │  ├── PolicyEngine         │
│  ├── ChainExecutor   │                │  ├── ProviderRouter       │
│  ├── CompositeEngine │                │  └── ToolReceipt          │
│  ├── SpatialIndex    │                └──────────────────────────┘
│  └── AppRegistry     │
│                      │
│  Framework Plugins:  │
│  ├── Electron (CDP)  │
│  ├── Browser (CDP)   │  ← Chrome, Edge, Brave, Vivaldi, Opera
│  ├── ChromeExt (WS)  │  ← No browser relaunch needed
│  ├── Qt (UIA)        │
│  ├── GTK (UIA)       │
│  ├── WPF (UIA)       │
│  ├── Flutter (UIA)   │
│  ├── Java (JAB→UIA)  │
│  ├── Office (COM)    │
│  ├── Win32 (UIA)     │
│  └── Vision (AI)     │  ← Claude Vision fallback
└─────────────────────┘
```

**Why two layers?** The UAB daemon must run on the host machine (outside Docker) because it needs direct access to the desktop's UI frameworks, process list, and accessibility APIs. The Python bridge inside the container communicates with the daemon via JSON-RPC 2.0 over HTTP.

**Current runtime shape:** Lancelot now embeds the standalone UAB 1.3.0 core inside `packages/uab`, but keeps the host contract stable through a JSON-RPC compatibility daemon on `:7900`. That lets the newer standalone connector and server internals evolve without breaking the existing Python governance bridge.

The source tree currently contains **11 runtime adapter directories** under `packages/uab/src/plugins/`, but the two host entrypoints do not register the same set:
- **`UABConnector`** — the path Lancelot embeds behind the governed bridge — always registers `direct-api`, conditionally adds `chrome-extension`, then falls through `browser-cdp`, `electron-cdp`, `office-com+uia`, `qt-uia`, `gtk-uia`, `java-jab-uia`, `flutter-uia`, and `win-uia`.
- **`UABService`** — the standalone singleton/server path — registers the structured framework hooks plus `vision` as the universal fallback, and does not host the `direct-api` or extension bridge paths itself.

The embedded engine now exposes the same first-class discovery surfaces as the standalone package:
- framework hook inventory
- framework-detection signature inventory
- Concerto method inventory
- per-operation control planning

### JSON-RPC 2.0 Protocol

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "detect",
  "params": {},
  "id": 1
}
```

**Response (success):**
```json
{
  "jsonrpc": "2.0",
  "result": [
    {
      "pid": 1234,
      "name": "Slack",
      "framework": "electron",
      "confidence": 0.95,
      "windowTitle": "Slack — #general"
    }
  ],
  "id": 1
}
```

**Connection details:**

| Property | Value |
|----------|-------|
| Daemon URL | `http://host.docker.internal:7900` (configurable via `UAB_DAEMON_URL`) |
| Protocol | HTTP POST, JSON-RPC 2.0 |
| Connect timeout | 5 seconds |
| Read timeout | 30 seconds |
| Default port | 7900 |
| Health check | `getStatus` method, 30-second interval |

---

## Supported Runtime Adapters

| Runtime Route | Available In | Plugin | Connection Method | Detection / Use |
|---------------|--------------|--------|-------------------|-----------------|
| **Direct API apps** | Connector | DirectApiPlugin | `direct-api` | Apps that expose a local control endpoint |
| **Chrome Extension** | Connector (optional) | ChromeExtPlugin | `chrome-extension` | No browser relaunch needed — extension connects via WS |
| **Browser** | Connector + Service | BrowserPlugin | `browser-cdp` | Chrome, Edge, Brave, Vivaldi, Opera process detection |
| **Electron** | Connector + Service | ElectronPlugin | `electron-cdp` | Process binary inspection, `--remote-debugging-port` |
| **Office** | Connector + Service | OfficePlugin | `office-com+uia` | Process name matching (WINWORD, EXCEL, etc.) |
| **Qt 5/6** | Connector + Service | QtPlugin | `qt-uia` | Process binary/DLL inspection |
| **GTK 3/4** | Connector + Service | GtkPlugin | `gtk-uia` | Process binary/DLL inspection |
| **Java Swing/FX** | Connector + Service | JavaPlugin | `java-jab-uia` | JVM process detection |
| **Flutter** | Connector + Service | FlutterPlugin | `flutter-uia` | Flutter engine DLL detection |
| **WPF/.NET / Win32** | Connector + Service | WinUIAPlugin | `win-uia` | .NET runtime detection and universal Windows UIA fallback |
| **Vision** | Service | VisionPlugin | `vision` | Universal fallback — works with any application |

Each plugin implements the same `PluginConnection` interface: `enumerate()`, `query()`, `act()`, `state()`, `subscribe()`, `disconnect()`.

### Browser Plugin

The `BrowserPlugin` (`packages/uab/src/plugins/browser/index.ts`) enables native CDP control of standard web browsers — Chrome, Edge, Brave, Vivaldi, and Opera — without requiring Electron. It auto-detects browser processes and attaches via their remote debugging port.

**Supported browsers:** Chrome, Edge, Brave, Vivaldi, Opera (any Chromium-based browser).

**Connection method:** `browser-cdp`. The plugin discovers the browser's debug port via process inspection and connects to the DevTools WebSocket endpoint.

### Chrome Extension Plugin

The `ChromeExtPlugin` (`packages/uab/src/plugins/chrome-ext/index.ts`) provides zero-relaunch browser control via a companion Chrome extension. Unlike the Browser plugin, it does not require the browser to be launched with `--remote-debugging-port` — the extension acts as a bridge.

**Components:**
- `index.ts` — Plugin entry point, WebSocket client
- `ws-server.ts` — `ExtensionWSServer`, WebSocket server that the extension connects to
- `installer.ts` — Automated extension installation helper

**How it works:** The Chrome extension connects to UAB's `ExtensionWSServer` via WebSocket. UAB sends commands through this channel and receives DOM/accessibility data back. The runtime reports this as the `chrome-extension` control method and prefers it ahead of `browser-cdp` when available.

### Vision Plugin

The `VisionPlugin` (`packages/uab/src/plugins/vision/`) is a universal fallback that works with any application — no accessibility API, no framework hooks, no special setup. It operates like Anthropic's computer use tool:

1. Capture screenshot of target window
2. Send to Claude Vision API (`claude-sonnet-4-20250514`) for element detection
3. Map detected elements to `UIElement[]` with bounding boxes
4. Execute actions via coordinate-based input injection (`packages/uab/src/plugins/vision/input.ts`)

**Trade-offs:**
- Expensive (API call per enumerate/query)
- Slower (screenshot + API round-trip + input injection)
- Less precise than native accessibility APIs
- But **universal** — works when nothing else does

**Priority:** Last resort in the `ControlRouter`. Native hooks, keyboard-native operations, and raw-input injection are considered first; `vision` is the universal fallback when those paths are unavailable.

**Components:**
- `index.ts` — VisionPlugin, implements `FrameworkPlugin` interface
- `analyzer.ts` — `VisionAnalyzer`, sends screenshots to Claude Vision
- `input.ts` — Coordinate-based click, type, and key injection

---

## Unified Element Model

All frameworks map to a common set of types. These are defined in `src/tools/contracts.py` (Python) and `packages/uab/src/types.ts` (TypeScript).

### UIElement

```python
@dataclass
class UIElement:
    id: str                          # Unique element identifier
    type: str                        # Element type (see table below)
    label: Optional[str]             # Human-readable label
    properties: Dict[str, Any]       # Framework-specific properties
    bounds: Optional[Dict[str, int]] # {x, y, width, height}
    children: List["UIElement"]      # Child elements (tree structure)
    actions: List[str]               # Supported action types
    visible: bool                    # Currently visible
    enabled: bool                    # Currently enabled/interactive
    meta: Optional[Dict[str, Any]]   # Framework-specific metadata
```

### DetectedApp

```python
@dataclass
class DetectedApp:
    pid: int                               # Process ID
    name: str                              # Application name
    path: Optional[str]                    # Executable path
    framework: Optional[str]               # Detected framework
    confidence: float                      # Detection confidence (0.0–1.0)
    window_title: Optional[str]            # Active window title
    connection_info: Optional[Dict]        # Framework-specific connection data
```

### AppActionResult

```python
@dataclass
class AppActionResult:
    success: bool
    action: str                            # Action performed
    element_id: Optional[str]              # Target element
    state_changes: List[Dict[str, Any]]    # Observable state changes
    error_message: Optional[str]
    duration_ms: int
    result_data: Optional[Any]             # Read results, screenshot paths, etc.
```

### AppState

```python
@dataclass
class AppState:
    pid: int
    window_title: Optional[str]
    window_size: Optional[Dict[str, int]]  # {width, height}
    window_position: Optional[Dict[str, int]]  # {x, y}
    focused: bool
    active_element: Optional[Dict]
    modals: List[Dict]
    menus: List[Dict]
    clipboard: Optional[str]
```

### ConnectionResult

```python
@dataclass
class ConnectionResult:
    success: bool
    pid: int
    framework: Optional[str]
    connection_method: Optional[str]       # direct-api, chrome-extension, browser-cdp, electron-cdp, office-com+uia, qt-uia, gtk-uia, java-jab-uia, flutter-uia, win-uia, vision
    error_message: Optional[str]
```

### Element Types (24)

`window`, `button`, `textfield`, `textarea`, `checkbox`, `radio`, `select`, `menu`, `menuitem`, `list`, `listitem`, `table`, `tablerow`, `tablecell`, `tab`, `tabpanel`, `tree`, `treeitem`, `slider`, `progressbar`, `scrollbar`, `toolbar`, `statusbar`, `dialog`, `tooltip`, `image`, `link`, `label`, `heading`, `separator`, `container`, `unknown`

### Action Types (61)

**Basic (15):** `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `scroll`, `focus`, `hover`, `expand`, `collapse`, `invoke`, `check`, `uncheck`, `toggle`

**Keyboard (2):** `keypress`, `hotkey`

**Window (6):** `minimize`, `maximize`, `restore`, `close`, `move`, `resize`, `screenshot`, `contextmenu`

**Office (12):** `readDocument`, `readCell`, `writeCell`, `readRange`, `writeRange`, `getSheets`, `readFormula`, `readSlides`, `readSlideText`, `readEmails`, `composeEmail`, `sendEmail`

**Browser Session/Cookies (4):** `getCookies`, `setCookie`, `deleteCookie`, `clearCookies`

**Browser Storage (8):** `getLocalStorage`, `setLocalStorage`, `deleteLocalStorage`, `clearLocalStorage`, `getSessionStorage`, `setSessionStorage`, `deleteSessionStorage`, `clearSessionStorage`

**Browser Navigation (4):** `navigate`, `goBack`, `goForward`, `reload`

**Browser Tab Management (4):** `getTabs`, `switchTab`, `closeTab`, `newTab`

**Browser Script (1):** `executeScript`

---

## Risk Classification

Every UAB action is classified into one of three risk levels. The classification determines governance requirements.

### Risk Levels

| Level | Actions | Governance |
|-------|---------|------------|
| **LOW** | `detect`, `enumerate`, `query`, `state`, `screenshot`, all read operations (`readDocument`, `readCell`, `readRange`, `getSheets`, `readFormula`, `readSlides`, `readSlideText`, `readEmails`), browser reads (`getCookies`, `getLocalStorage`, `getSessionStorage`, `getTabs`) | Autonomous — no approval needed |
| **MEDIUM** | `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `scroll`, `focus`, `hover`, `expand`, `collapse`, `check`, `uncheck`, `toggle`, `keypress`, `hotkey`, `contextmenu`, `writeCell`, `writeRange`, `composeEmail`, `navigate`, `goBack`, `goForward`, `reload`, `switchTab`, `newTab`, `setCookie`, `setLocalStorage`, `setSessionStorage`, `executeScript` | May require governance approval |
| **HIGH** | `close`, `invoke`, `minimize`, `maximize`, `restore`, `move`, `resize`, `sendEmail`, `deleteCookie`, `clearCookies`, `deleteLocalStorage`, `clearLocalStorage`, `deleteSessionStorage`, `clearSessionStorage`, `closeTab` | Always requires approval |

### Sensitive App Auto-Escalation

When the target application matches a sensitive pattern, risk levels are automatically escalated:

| App Pattern | Read-Only Actions | Mutating Actions |
|-------------|-------------------|------------------|
| Password managers (`1password`, `bitwarden`, `keepass`, `lastpass`) | LOW → MEDIUM | MEDIUM → HIGH |
| Banking (`bank`, `chase`, `wells fargo`, `capital one`) | LOW → MEDIUM | MEDIUM → HIGH |
| Financial (`venmo`, `paypal`, `stripe`) | LOW → MEDIUM | MEDIUM → HIGH |
| Email clients (`outlook`, `thunderbird`, `gmail`) | LOW → MEDIUM | MEDIUM → HIGH |
| Shells (`terminal`, `powershell`, `cmd`) | LOW → MEDIUM | MEDIUM → HIGH |

---

## Receipt System

Every UAB action produces an `AppControlReceipt` for full auditability. Sessions are tracked via `AppSessionEntry`.

### AppControlReceipt

```python
@dataclass
class AppControlReceipt:
    # Identity
    receipt_id: str                  # UUID
    timestamp: str                   # ISO 8601
    session_id: Optional[str]
    parent_receipt_id: Optional[str]

    # App context
    app_name: str
    app_pid: int
    app_framework: Optional[str]
    window_title: Optional[str]
    connection_method: Optional[str]

    # Action classification
    action_type: str                 # detect, connect, enumerate, query, act, state
    mutating: bool                   # Computed from action
    risk_level: str                  # LOW, MEDIUM, HIGH

    # Element targeted
    element_id: Optional[str]
    element_type: Optional[str]
    element_label: Optional[str]
    element_path: Optional[str]      # UI tree path

    # Action details
    action_performed: Optional[str]  # click, type, select, etc.
    action_params: Dict[str, Any]

    # State snapshots
    pre_state: Dict[str, Any]
    post_state: Dict[str, Any]

    # Chain context (multi-step workflows)
    chain_id: Optional[str]
    chain_name: Optional[str]
    step_index: Optional[int]
    total_steps: Optional[int]

    # Governance
    governance_gate: str             # "autonomous" or "required_approval"
    approval_id: Optional[str]

    # Result
    success: bool
    error_message: Optional[str]
    duration_ms: Optional[int]
```

### AppSessionEntry

```python
@dataclass
class AppSessionEntry:
    session_id: str
    app_name: str
    app_pid: int
    app_framework: Optional[str]
    connected_at: str
    disconnected_at: Optional[str]
    total_actions: int
    mutating_actions: int
    read_only_actions: int
    action_summary: Dict[str, int]   # Action type → count
    elements_touched: List[str]      # Unique element IDs
    max_risk_level: str              # Highest risk seen
    receipt_ids: List[str]           # Links to individual receipts
```

### Storage Layout

```
data/receipts/uab/
├── {receipt_id}.json          # Individual action receipts
└── sessions/
    └── {session_id}.json      # Per-app session summaries
```

In-memory cache: last 500 receipts for fast queries.

---

## Action Chains

UAB supports multi-step workflows via action chains — sequences of actions with conditional logic, waits, and delays.

### Step Types

| Step Type | Description |
|-----------|-------------|
| `action` | Execute a UI action on an element |
| `wait` | Wait for an element matching a selector (with timeout) |
| `conditional` | Branch based on element visibility/enabled state |
| `delay` | Fixed time delay |
| `keypress` | Single key press |
| `hotkey` | Key combination (e.g., `["ctrl", "s"]`) |
| `typeText` | Type text into an element |

### Chain Definition

```python
{
    "name": "Save Document",
    "pid": 1234,
    "steps": [
        {"type": "hotkey", "keys": ["ctrl", "s"], "label": "Save"},
        {"type": "wait", "selector": {"type": "dialog"}, "timeoutMs": 3000, "label": "Wait for save dialog"},
        {"type": "conditional",
         "condition": "element_visible",
         "selector": {"type": "dialog"},
         "onTrue": [{"type": "action", "selector": {"label": "Save"}, "action": "click"}],
         "onFalse": []
        }
    ],
    "timeout": 10000,
    "continueOnError": false
}
```

### Chain Result

```python
{
    "success": true,
    "name": "Save Document",
    "totalSteps": 3,
    "stepsCompleted": 3,
    "steps": [
        {"stepIndex": 0, "success": true, "durationMs": 15},
        {"stepIndex": 1, "success": true, "durationMs": 1200},
        {"stepIndex": 2, "success": true, "durationMs": 45}
    ],
    "durationMs": 1260
}
```

All chain steps are individually receipt-traced via `chain_id` and `step_index` fields in `AppControlReceipt`.

---

## Connection Health Monitoring

The daemon monitors all active connections at 30-second intervals.

**Health check behavior:**
- Each connected app is polled via its plugin connection
- Failed connections trigger exponential backoff reconnection: 1s → 2s → 4s → 8s (max)
- Connections failing for 5+ minutes are classified as stale and cleaned up
- Health summary available via `health()` RPC method

### Caching

Smart element caching reduces framework overhead:

| Cache Type | TTL | Invalidation |
|------------|-----|--------------|
| **Tree cache** (enumerate results) | 5 seconds | On any mutating action |
| **Query cache** (selector results) | 3 seconds | On any mutating action |
| **State cache** (app state) | 2 seconds | On any mutating action |

**Cache capacity:** Max 50 queries per PID.

**Mutating actions that invalidate cache:** `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `check`, `uncheck`, `toggle`, `expand`, `collapse`, `invoke`, `keypress`, `hotkey`, `close`

**Read-only actions that preserve cache:** `focus`, `hover`, `scroll`, `screenshot`, `minimize`, `maximize`, `restore`, `move`, `resize`

### Rate Limiting

Implicit rate limiting: 100 requests per minute per PID (enforced at the daemon level).

---

## Permission Model and Audit Log

### Permission Checks

Every action goes through the `PermissionManager` before execution:

1. **Classify risk** — determine if the action is safe, moderate, or destructive
2. **Check app sensitivity** — auto-escalate if targeting a sensitive application
3. **Evaluate policy** — check against current permission rules
4. **Record audit entry** — log the check result regardless of outcome

### Audit Log

Every permission check produces an `AuditEntry`:

```json
{
    "timestamp": "2026-03-01T10:30:00Z",
    "action": "click",
    "appName": "Slack",
    "elementId": "btn_send",
    "riskLevel": "moderate",
    "allowed": true,
    "reason": "Action within policy"
}
```

Audit log queryable via `auditLog({limit})` RPC method.

---

## UABProvider API (Python Bridge)

The `UABProvider` class in `src/tools/providers/uab_bridge.py` implements the `AppControlCapability` protocol:

```python
class UABProvider(BaseProvider):
    # Discovery
    def detect() -> List[DetectedApp]
    def connect(target: Union[int, str]) -> ConnectionResult
    def disconnect(pid: int) -> bool

    # Unified API
    def enumerate(pid: int) -> List[UIElement]
    def query(pid: int, selector: Dict) -> List[UIElement]
    def act(pid: int, element_id: str, action: str, params: Dict = None) -> AppActionResult
    def state(pid: int) -> AppState

    # Keyboard
    def keypress(pid: int, key: str) -> AppActionResult
    def hotkey(pid: int, keys: List[str]) -> AppActionResult

    # Window management
    def minimize(pid: int) -> AppActionResult
    def maximize(pid: int) -> AppActionResult
    def restore(pid: int) -> AppActionResult
    def close_window(pid: int) -> AppActionResult
    def move_window(pid: int, x: int, y: int) -> AppActionResult
    def resize_window(pid: int, width: int, height: int) -> AppActionResult

    # Screenshot
    def screenshot(pid: int, output_path: str = None) -> AppActionResult

    # Action chains
    def execute_chain(chain_definition: Dict) -> Dict

    # Office operations
    def read_document(pid: int) -> AppActionResult
    def read_cell(pid: int, row: int, col: int, sheet: str = "") -> AppActionResult
    def write_cell(pid: int, row: int, col: int, value: str, sheet: str = "") -> AppActionResult
    def read_range(pid: int, cell_range: str, sheet: str = "") -> AppActionResult
    def write_range(pid: int, cell_range: str, values: List[List[str]], sheet: str = "") -> AppActionResult
    def get_sheets(pid: int) -> AppActionResult
    def read_emails(pid: int) -> AppActionResult
    def compose_email(pid: int, to: str, subject: str, body: str, cc: str = "") -> AppActionResult
    def send_email(pid: int, to: str, subject: str, body: str, cc: str = "") -> AppActionResult

    # Spatial Map / Composite Engine
    def spatial_map(pid: int, format: str = "detailed") -> Dict[str, Any]
    def text_map(pid: int, format: str = "detailed") -> Dict[str, Any]
    def find_by_description(pid: int, description: str) -> List[Dict[str, Any]]

    # Browser Operations
    def navigate(pid: int, url: str) -> AppActionResult
    def get_tabs(pid: int) -> AppActionResult
    def switch_tab(pid: int, tab_id: str) -> AppActionResult
    def execute_script(pid: int, script: str) -> AppActionResult
    def get_cookies(pid: int, url: str = "", domain: str = "") -> AppActionResult
    def set_cookie(pid: int, name: str, value: str, domain: str = "", url: str = "") -> AppActionResult
    def get_local_storage(pid: int, key: str = "") -> AppActionResult
    def set_local_storage(pid: int, key: str, value: str) -> AppActionResult

    # Diagnostics
    def health_check() -> ProviderHealth
    def get_health_summary() -> List[Dict]
    def get_cache_stats() -> Dict
    def get_audit_log(limit: int = 50) -> List[Dict]
```

### Configuration

```python
@dataclass
class UABConfig:
    daemon_url: str = "http://host.docker.internal:7900"
    connect_timeout_s: int = 5
    read_timeout_s: int = 30
    rpc_version: str = "2.0"
    max_elements: int = 5000         # Output depth limit
    max_element_depth: int = 20      # Tree traversal limit
```

---

## API Endpoints

UAB status is exposed through the Gateway flags API:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/flags/uab-status` | Daemon health: reachable, version, connected app count, frameworks |
| GET | `/api/flags/uab-apps` | List of connected applications with PID, framework, connection method |
| GET | `/api/flags/uab-receipts` | Recent UAB action receipts (filterable) |
| GET | `/api/flags/uab-sessions` | Session summaries (active + recent) |

---

## Feature Flags

| Flag | Default | Dependencies | Description |
|------|---------|--------------|-------------|
| `FEATURE_TOOLS_UAB` | `false` | `FEATURE_TOOLS_FABRIC` + `FEATURE_TOOLS_HOST_BRIDGE` | Enable UAB bridge provider |
| `FEATURE_HIVE_UAB` | `false` | `FEATURE_HIVE`, `FEATURE_TOOLS_UAB` | Enable UAB for Hive sub-agents |

---

## Installation

### Prerequisites

- **Node.js 18+** on the host machine (not inside Docker)
- **Windows 10/11** for full framework support (UIA, COM, JAB)

### Install and Build

**Linux/macOS:**
```bash
./scripts/install-uab.sh
./scripts/install-uab.sh --start  # Install and start immediately
```

**Windows (auto-start on login):**
```batch
scripts\install-uab.bat
```
This checks Node.js >= 18, builds if needed, registers a `LancelotUABDaemon` Scheduled Task (runs on logon), starts the daemon immediately, and verifies the health check. Idempotent — safe to run multiple times.

**Windows (manual foreground — for debugging):**
```batch
scripts\start-uab.bat
```

**Windows (uninstall):**
```batch
scripts\uninstall-uab.bat
```

**Manual:**
```bash
cd packages/uab
npm install
npm run build
node dist/daemon.js --host 127.0.0.1 --port 7900
```

On Windows, the persistent install now launches `scripts\run-uab-daemon.bat` from the `LancelotUABDaemon` Scheduled Task. That avoids brittle `schtasks /TR` quoting and keeps the startup path stable even when the repo lives under a spaced directory.

The daemon binds to `127.0.0.1` by default. Set `UAB_DAEMON_HOST` or pass `--host 0.0.0.0` only when a trusted local bridge explicitly requires non-loopback access.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UAB_DAEMON_URL` | `http://host.docker.internal:7900` | Daemon address (set in container `.env`) |
| `UAB_DAEMON_HOST` | `127.0.0.1` | Host-side bind address for the compatibility daemon |
| `UAB_DAEMON_PORT` | `7900` | Daemon listen port |
| `UAB_LOG_LEVEL` | `info` | Daemon log level: `debug`, `info`, `warn`, `error` |
| `UAB_LOG_FILE` | _(none)_ | Optional log file path |

### Verify

```bash
# Check daemon is running
curl http://localhost:7900 -d '{"jsonrpc":"2.0","method":"getStatus","params":{},"id":1}'

# Check from inside container
curl http://localhost:8000/api/flags/uab-status
```

---

## War Room Integration

The UAB status panel appears on the **Kill Switches** page when `FEATURE_TOOLS_UAB` is enabled.

**UABPanel displays:**
- Daemon status (running/offline with green pulse animation)
- Daemon version and uptime
- Bridge transport (`json-rpc-compat` when backed by the standalone 1.3.0 core)
- Exposed standalone feature set
- Connected application count
- Supported framework list
- Connected apps table: name, PID, framework, connection method
- Instructions to start the daemon if offline

The panel polls every 5 seconds for live status updates.

---

## Spatial Map Engine

The Spatial Map (`packages/uab/src/spatial.ts`) converts flat `UIElement[]` with bounding rects into a spatial index that enables fast positional queries, row/column detection, and compact text-based maps for AI consumption.

**This is UAB's core speed advantage over vision-only approaches:**
- Data is faster than screenshots for AI to process
- Bounding rects are free from UIA (no extra API calls)
- The spatial map eliminates the need for screenshots in most cases
- Vision becomes complementary, not primary

### Key Types

- **`SpatialElement`** — element with `id`, `type`, `label`, `bounds`, `center`, `row`, `col`, optional `text`/`value`
- **`SpatialRow`** — detected visual row band with `index`, `y`, `height`
- **`SpatialMap`** — full spatial index with elements, rows, window bounds

### Output Formats

| Format | Method | Description |
|--------|--------|-------------|
| `detailed` | `renderTextMap()` | Human/AI-readable text layout with positions |
| `compact` | `renderTextMap()` | Condensed version for smaller context windows |
| `json` | `renderJsonMap()` | JSON-serializable spatial index |

### RPC Methods

| Method | Params | Returns |
|--------|--------|---------|
| `spatialMap` | `{pid, options: {mapFormat}}` | `CompositeResult` with spatial map, timing, text content |
| `textMap` | `{pid, format}` | `{text, timing}` |
| `findByDescription` | `{pid, description}` | `SpatialElement[]` matching natural language description |

---

## Composite Engine

The `CompositeEngine` (`packages/uab/src/composite.ts`) is UAB's fastest query mode. It combines all available data sources in speed-priority order:

1. **UIA Tree** (direct) — element IDs, types, states, structure
2. **Bounding Rects** (direct) — spatial positions, sizes, builds spatial map
3. **Text Reading** (fast) — `TextPattern`/`ValuePattern` content extraction
4. **Vision** (slow) — screenshot + Claude Vision (ONLY when needed)

The engine accepts a `UABLike` interface (implemented by both `UABService` and `UABConnector`), making it usable in both daemon and standalone modes.

### CompositeResult

```typescript
interface CompositeResult {
  spatialMap: SpatialMap;       // Full spatial index
  textMap: string;              // Text-based UI map for AI
  timing: { total: number };    // Performance metrics
  textContent: string[];        // Extracted text values
}
```

---

## UABConnector

The `UABConnector` (`packages/uab/src/connector.ts`) is a framework-independent, instantiable (non-singleton) desktop control API designed for use by ANY agent framework:

- Claude Code (via Bash or MCP)
- Codex CLI (via Bash)
- Custom agents (import as library)
- MD-only agents (via CLI JSON output)

**Design principles:** Zero dependencies on any agent framework, in-memory registry for fast lookups, JSON profiles for persistence, returns plain JSON-serializable objects.

```typescript
const uab = new UABConnector();
await uab.start();
const apps = await uab.scan();
await uab.connect(apps[0].pid);
const buttons = await uab.query(apps[0].pid, { type: 'button' });
await uab.act(apps[0].pid, buttons[0].id, 'click');
await uab.stop();
```

### ConnectorOptions

| Option | Default | Description |
|--------|---------|-------------|
| `profileDir` | `data/uab-profiles` | JSON profile persistence directory |
| `persistent` | auto-detected | Enable persistent connections with health monitoring |
| `extensionBridge` | auto-detected | Enable Chrome extension WebSocket bridge |
| `rateLimit` | auto-detected | Max actions per minute per PID |
| `mode` | auto-detected | Force `desktop`, `server`, or `container` mode |

---

## App Registry

The `AppRegistry` (`packages/uab/src/registry.ts`) is an in-memory knowledge base with optional JSON profile persistence. It remembers apps across sessions without requiring a database.

**Design principles:** Zero dependencies, O(1) in-memory Map lookups, git-friendly single JSON file with readable diffs, scales to 1000+ apps.

### AppProfile

Each registered app has an `AppProfile` with:
- `executable` — stable key (lowercase executable name)
- `name` — human-readable app name
- `framework` — detected UI framework
- `confidence` — detection confidence (0.0-1.0)
- `preferredMethod` — best control method found
- `connectionInfo` — framework-specific connection params

---

## MCP Server

The UAB MCP Server (`packages/uab/src/mcp-server.ts`) exposes UAB as Model Context Protocol tools over stdio. When an MCP-compatible AI agent connects, it discovers UAB tools natively — no need to decide to use UAB over screenshots.

**Configuration (for `claude_desktop_config.json` or any MCP agent):**

```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "node",
      "args": ["dist/uab/mcp-server.js"]
    }
  }
}
```

The server implements JSON-RPC over stdio with no external dependencies. It includes raw UIA tree walking via PowerShell for low-level element inspection.

---

## Agent SDK

The `AgentSDK` (`packages/uab/src/sdk.ts`) provides a dead-simple wrapper that makes UAB easier to use than screenshots, so agents naturally prefer structured control.

```typescript
import { desktop } from './sdk.js';

// One-liner: click a button in any app
await desktop.click('Notepad', 'File');

// Type into a field
await desktop.type('Notepad', 'Edit area', 'Hello world');

// Get what's on screen (no screenshot needed)
const layout = await desktop.look('Notepad');

// Full workflow
await desktop.do('Notepad', [
  { click: 'File' },
  { click: 'Save As...' },
  { type: { field: 'File name', text: 'document.txt' } },
  { click: 'Save' },
]);
```

### Workflow Steps

| Step Type | Example | Description |
|-----------|---------|-------------|
| `click` | `{ click: 'Save' }` | Click an element by label |
| `type` | `{ type: { field: 'Name', text: 'hello' } }` | Type text into a field |
| `hotkey` | `{ hotkey: 'ctrl+s' }` | Send a keyboard shortcut |
| `key` | `{ key: 'Enter' }` | Send a single keypress |
| `wait` | `{ wait: 1000 }` | Wait N milliseconds |

---

## Agent Prompts

The `agent-prompt.ts` module (`packages/uab/src/agent-prompt.ts`) provides drop-in system prompt templates that teach ANY AI agent to prefer UAB's structured APIs over screenshots.

**Available modes:**
- `mcp` — for Claude Code / MCP agents
- `cli` — for CLI-based agents (Codex, custom agents)
- `http` — for HTTP API agents

```typescript
import { getAgentPrompt } from './agent-prompt.js';
const prompt = getAgentPrompt('mcp');
```

The prompts override agents' default screenshot-taking behavior by explaining that structured UI queries are typically much faster than screenshot capture plus vision analysis on the same host, more reliable, cheaper, and provide element IDs directly.

---

## Environment Detection

The `environment.ts` module (`packages/uab/src/environment.ts`) auto-detects the runtime context and adapts UAB behavior accordingly.

### Runtime Modes

| Mode | Description | UIA Access | Example |
|------|-------------|------------|---------|
| `desktop` | Interactive Windows session (Session 1+) | Full | Developer workstation |
| `server` | Non-interactive (SSH, service) | Via Session Bridge | Remote server |
| `container` | Docker, WSL, Hyper-V | Limited/none | CI/CD pipeline |

### EnvironmentInfo

```typescript
interface EnvironmentInfo {
  mode: RuntimeMode;        // 'desktop' | 'server' | 'container'
  hasDesktop: boolean;      // Whether a desktop session is reachable
  sessionId: number;        // Windows session ID (0 = non-interactive)
  isContainer: boolean;     // Docker, WSL, etc.
  needsBridge: boolean;     // Session 0→1 bridge needed
  platform: NodeJS.Platform;
  arch: string;
  nodeVersion: string;
}
```

---

## JSON-RPC Methods (Complete)

All methods available on the daemon's JSON-RPC 2.0 endpoint (`http://host.docker.internal:7900`):

| Method | Category | Description |
|--------|----------|-------------|
| `ping` | Status | Liveness check |
| `version` | Status | Daemon version |
| `status` | Status | Basic status |
| `getStatus` | Status | Full status with frameworks, connected apps |
| `detect` / `detect.all` | Discovery | Scan for all controllable apps |
| `detect.electron` | Discovery | Scan for Electron apps only |
| `detect.byPid` | Discovery | Detect framework for a specific PID |
| `detect.byName` | Discovery | Detect framework by app name |
| `connect` | Connection | Connect to an app by PID |
| `disconnect` | Connection | Disconnect from an app |
| `disconnectAll` | Connection | Disconnect all apps |
| `connections` | Connection | List active connections |
| `enumerate` | Query | Get full UI element tree |
| `query` | Query | Search for elements by selector |
| `act` | Action | Execute a UI action on an element |
| `state` | Query | Get current app state |
| `keypress` | Action | Send a keypress |
| `hotkey` | Action | Send a key combination |
| `minimize` / `maximize` / `restore` | Window | Window management |
| `closeWindow` | Window | Close a window |
| `moveWindow` / `resizeWindow` | Window | Reposition/resize |
| `screenshot` | Capture | Capture window screenshot |
| `chain` | Workflow | Execute an action chain |
| `health` | Diagnostics | Connection health summary |
| `cacheStats` | Diagnostics | Cache statistics |
| `auditLog` | Diagnostics | Recent audit entries |
| `checkHealth` | Diagnostics | Force health check cycle |
| `spatialMap` | Composite | Get spatial map of an app's UI |
| `textMap` | Composite | Get text-based UI map |
| `findByDescription` | Composite | Find elements by natural language |
| `scan` | Connector | Connector-backed app discovery alias |
| `apps` | Connector | List active connector-managed applications |
| `find` | Connector | High-level element lookup helper |
| `focused` | Connector | Return the currently focused element |
| `findByPath` | Connector | Find elements by path or parent context |
| `watchChanges` | Connector | Watch focused window changes over a short interval |
| `atomicChain` | Connector | Execute an atomic multi-step input chain |
| `smartInvoke` | Connector | Best-effort resolve and invoke a control |

---

## Key Files

| Path | Purpose |
|------|---------|
| `packages/uab/` | Host daemon (TypeScript/Node.js) |
| `packages/uab/src/types.ts` | Unified type definitions (61 action types) |
| `packages/uab/src/service.ts` | UABService singleton |
| `packages/uab/src/detector.ts` | Framework detection |
| `packages/uab/src/plugins/` | 11 runtime adapter directories used by the connector and service entrypoints |
| `packages/uab/src/plugins/browser/` | Browser plugin (CDP for Chrome, Edge, Brave, etc.) |
| `packages/uab/src/plugins/chrome-ext/` | Chrome Extension plugin (WebSocket bridge, installer) |
| `packages/uab/src/plugins/vision/` | Vision plugin (Claude AI screenshot analysis + input injection) |
| `packages/uab/src/cache.ts` | Smart element caching |
| `packages/uab/src/permissions.ts` | Risk-based access control |
| `packages/uab/src/chains.ts` | Multi-step action workflows |
| `packages/uab/src/connection-manager.ts` | Health monitoring and auto-reconnect |
| `packages/uab/src/composite.ts` | CompositeEngine — multi-source query |
| `packages/uab/src/spatial.ts` | SpatialMap/SpatialIndex — positional queries |
| `packages/uab/src/connector.ts` | UABConnector — framework-independent public API |
| `packages/uab/src/registry.ts` | AppRegistry — in-memory app knowledge base |
| `packages/uab/src/mcp-server.ts` | MCP Server — UAB as MCP tools over stdio |
| `packages/uab/src/sdk.ts` | AgentSDK — dead-simple wrapper for agents |
| `packages/uab/src/agent-prompt.ts` | Agent Prompts — system prompts for AI agents |
| `packages/uab/src/environment.ts` | Environment Detection — desktop/server/container |
| `packages/uab/src/router.ts` | ControlRouter — plugin selection and priority |
| `packages/uab/src/cli.ts` | CLI interface for any agent framework |
| `packages/uab/src/commands.ts` | Telegram bot commands for UAB |
| `src/tools/providers/uab_bridge.py` | Python JSON-RPC 2.0 bridge |
| `src/tools/receipts_uab.py` | Receipt types and storage |
| `src/tools/contracts.py` | AppControlCapability protocol and data types |
| `scripts/install-uab.sh` | Linux/macOS install script |
| `scripts/install-uab.bat` | Windows installer (auto-start via Scheduled Task) |
| `scripts/uninstall-uab.bat` | Windows uninstaller (removes task + stops daemon) |
| `scripts/start-uab.bat` | Windows manual foreground startup (debugging) |

Standalone-core merge files of interest:
- `packages/uab/src/server.ts`
- `packages/uab/src/cowork-bridge/`
- `packages/uab/src/daemon.ts`
