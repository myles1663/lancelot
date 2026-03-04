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
│  └── ChainExecutor   │                │  ├── ProviderRouter       │
│                      │                │  └── ToolReceipt          │
│  Framework Plugins:  │                └──────────────────────────┘
│  ├── Electron (CDP)  │
│  ├── Qt (UIA)        │
│  ├── GTK (UIA)       │
│  ├── WPF (UIA)       │
│  ├── Flutter (UIA)   │
│  ├── Java (JAB→UIA)  │
│  ├── Office (COM)    │
│  └── Win32 (UIA)     │
└─────────────────────┘
```

**Why two layers?** The UAB daemon must run on the host machine (outside Docker) because it needs direct access to the desktop's UI frameworks, process list, and accessibility APIs. The Python bridge inside the container communicates with the daemon via JSON-RPC 2.0 over HTTP.

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

## Supported Frameworks

| Framework | Plugin | Connection Method | Detection |
|-----------|--------|-------------------|-----------|
| **Electron** | ElectronPlugin | Chrome DevTools Protocol (CDP) | Process binary inspection, `--remote-debugging-port` |
| **Qt 5/6** | QtPlugin | Windows UI Automation (MOC bridge) | Process binary/DLL inspection |
| **GTK 3/4** | GtkPlugin | Windows UI Automation (GIR bridge) | Process binary/DLL inspection |
| **WPF/.NET** | WinUIAPlugin | Native Windows UI Automation | .NET runtime detection |
| **Flutter** | FlutterPlugin | Windows UI Automation (semantics) | Flutter engine DLL detection |
| **Java Swing/FX** | JavaPlugin | Java Accessibility Bridge → UIA | JVM process detection |
| **Office** | OfficePlugin | COM Automation | Process name matching (WINWORD, EXCEL, etc.) |
| **Win32** | WinUIAPlugin | Universal UIA fallback | Fallback for all unmatched processes |

Each plugin implements the same `PluginConnection` interface: `enumerate()`, `query()`, `act()`, `state()`, `subscribe()`, `disconnect()`.

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
    connection_method: Optional[str]       # cdp, moc, gir, clr, dart_vm, jvm, com
    error_message: Optional[str]
```

### Element Types (24)

`window`, `button`, `textfield`, `textarea`, `checkbox`, `radio`, `select`, `menu`, `menuitem`, `list`, `listitem`, `table`, `tablerow`, `tablecell`, `tab`, `tabpanel`, `tree`, `treeitem`, `slider`, `progressbar`, `scrollbar`, `toolbar`, `statusbar`, `dialog`, `tooltip`, `image`, `link`, `label`, `heading`, `separator`, `container`, `unknown`

### Action Types (29+)

**Basic:** `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `scroll`, `focus`, `hover`, `expand`, `collapse`, `invoke`, `check`, `uncheck`, `toggle`

**Keyboard:** `keypress`, `hotkey`

**Window:** `minimize`, `maximize`, `restore`, `close`, `move`, `resize`, `screenshot`, `contextmenu`

**Office:** `readDocument`, `readCell`, `writeCell`, `readRange`, `writeRange`, `getSheets`, `readFormula`, `readSlides`, `readSlideText`, `readEmails`, `composeEmail`, `sendEmail`

---

## Risk Classification

Every UAB action is classified into one of three risk levels. The classification determines governance requirements.

### Risk Levels

| Level | Actions | Governance |
|-------|---------|------------|
| **LOW** | `detect`, `enumerate`, `query`, `state`, `screenshot`, all read operations (`readDocument`, `readCell`, `readRange`, `getSheets`, `readFormula`, `readSlides`, `readSlideText`, `readEmails`) | Autonomous — no approval needed |
| **MEDIUM** | `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `scroll`, `focus`, `hover`, `expand`, `collapse`, `check`, `uncheck`, `toggle`, `keypress`, `hotkey`, `contextmenu`, `writeCell`, `writeRange`, `composeEmail` | May require governance approval |
| **HIGH** | `close`, `invoke`, `minimize`, `maximize`, `restore`, `move`, `resize`, `sendEmail` | Always requires approval |

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
| `FEATURE_TOOLS_UAB` | `false` | `FEATURE_TOOLS_FABRIC` | Enable UAB bridge provider |
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
node dist/daemon.js --port 7900
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UAB_DAEMON_URL` | `http://host.docker.internal:7900` | Daemon address (set in container `.env`) |
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
- Connected application count
- Supported framework list
- Connected apps table: name, PID, framework, connection method
- Instructions to start the daemon if offline

The panel polls every 5 seconds for live status updates.

---

## Key Files

| Path | Purpose |
|------|---------|
| `packages/uab/` | Host daemon (TypeScript/Node.js) |
| `packages/uab/src/types.ts` | Unified type definitions |
| `packages/uab/src/service.ts` | UABService singleton |
| `packages/uab/src/detector.ts` | Framework detection |
| `packages/uab/src/plugins/` | 8 framework plugin implementations |
| `packages/uab/src/cache.ts` | Smart element caching |
| `packages/uab/src/permissions.ts` | Risk-based access control |
| `packages/uab/src/chains.ts` | Multi-step action workflows |
| `packages/uab/src/connection-manager.ts` | Health monitoring and auto-reconnect |
| `src/tools/providers/uab_bridge.py` | Python JSON-RPC 2.0 bridge |
| `src/tools/receipts_uab.py` | Receipt types and storage |
| `src/tools/contracts.py` | AppControlCapability protocol and data types |
| `scripts/install-uab.sh` | Linux/macOS install script |
| `scripts/install-uab.bat` | Windows installer (auto-start via Scheduled Task) |
| `scripts/uninstall-uab.bat` | Windows uninstaller (removes task + stops daemon) |
| `scripts/start-uab.bat` | Windows manual foreground startup (debugging) |
