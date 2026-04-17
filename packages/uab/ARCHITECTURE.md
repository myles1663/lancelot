# Architecture

> How UAB discovers apps, identifies frameworks, builds persistent knowledge, and achieves framework-level control — all automatically.

## Table of Contents

- [System Overview](#system-overview)
- [Smart Function Discovery Pipeline](#smart-function-discovery-pipeline)
- [The App Registry (UAB's Brain)](#the-app-registry-uabs-brain)
- [The Cascade Pattern](#the-cascade-pattern)
- [Framework Detection Engine](#framework-detection-engine)
- [Control Router](#control-router)
- [Plugin Architecture](#plugin-architecture)
- [UABConnector vs UABService](#uabconnector-vs-uabservice)
- [Data Flow: End-to-End](#data-flow-end-to-end)
- [Production Hardening](#production-hardening)
- [Session Bridge](#session-bridge)
- [Desktop + Server Dual-Mode](#desktop--server-dual-mode)
- [Co-work Bridge Architecture](#co-work-bridge-architecture)
- [Installer Architecture](#installer-architecture)
- [Input Injection](#input-injection)

---

## System Overview

UAB sits between the agent runtime and the desktop OS. It provides a unified API while using framework-specific protocols underneath. The key innovation is the **Smart Function Discovery Pipeline** — a five-phase process that scans, identifies, registers, connects, and learns.

```
┌──────────────────────────────────────────────────────────────┐
│                      Agent Runtime                           │
│              (Claude, GPT, Custom Agent)                     │
└──────────────────────┬───────────────────────────────────────┘
                       │  UABConnector API  or  CLI (JSON)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Universal App Bridge                        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │         Smart Function Discovery Pipeline            │    │
│  │                                                      │    │
│  │  SCAN ──► IDENTIFY ──► REGISTER ──► CONNECT ──► LEARN│    │
│  │   │          │            │            │           │  │    │
│  │  WMI     DLL scan     AppRegistry   Plugin     Update │    │
│  │  batch   signatures   Map + JSON    cascade    registry│    │
│  │  enum    matching     dual-index    fallback   persist │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              Plugin Manager (9 plugins)               │    │
│  │                                                       │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│    │
│  │  │Chrome Ext│ │ Browser  │ │ Electron │ │  Office  ││    │
│  │  │  (WS)    │ │  (CDP)   │ │  (CDP)   │ │(COM+UIA) ││    │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘│    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│    │
│  │  │   Qt     │ │   GTK    │ │  Java    │ │ Flutter  ││    │
│  │  │  (UIA)   │ │  (UIA)   │ │(JAB→UIA) │ │  (UIA)   ││    │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘│    │
│  │  ┌──────────┐ ┌──────────┐                             │    │
│  │  │ Win-UIA  │ │  Vision  │                             │    │
│  │  │(A11y fb) │ │(AI last  │                             │    │
│  │  │          │ │ resort)  │                             │    │
│  │  └──────────┘ └──────────┘                             │    │
│  └───────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │  Cache   │ │Permission│ │  Retry   │ │ Chain Engine │   │
│  │ (3-tier) │ │ (Audit)  │ │(Backoff) │ │ (Workflows)  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│                                                              │
│  ┌──────────────────┐  ┌────────────────────────────────┐   │
│  │ Control Router   │  │  Connection Manager            │   │
│  │ (Cascade)        │  │  (Health + Reconnect)          │   │
│  └──────────────────┘  └────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    Operating System                           │
│  CDP │ Windows UI Automation │ COM Automation │ PowerShell   │
└──────────────────────────────────────────────────────────────┘
```

---

## Smart Function Discovery Pipeline

This is the core of UAB. Five phases that transform a running desktop into a controllable interface:

### Phase 1: SCAN — "What's running?"

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 1: SCAN                             │
│                                                             │
│  Step 1: WMI Process Enumeration                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ PowerShell: Get-CimInstance Win32_Process              │  │
│  │ Returns: PID, Name, ExecutablePath, CommandLine        │  │
│  │ Single call for ALL processes (not per-process)        │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 2: System Process Filter                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Skip 40+ known system processes:                      │  │
│  │ svchost, csrss, dwm, conhost, audiodg, etc.           │  │
│  │ Only keep user-facing applications                    │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 3: Batch DLL Module Scan                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ PowerShell: Get-Process → Modules                     │  │
│  │ Batches of 50 PIDs per call (avoids PS limits)        │  │
│  │ Returns: PID → [loaded DLL names]                     │  │
│  │ This is how frameworks are identified!                 │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 4: Batch Window Title Scan                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ P/Invoke: EnumWindows + GetWindowText                 │  │
│  │ C# compiled inline in PowerShell                      │  │
│  │ Single call for ALL visible windows                   │  │
│  │ Returns: PID → window title                           │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Result: ProcessInfo[] with pid, name, path, modules, title │
│  Typical: 150+ processes → 79+ controllable apps            │
│  Time: 2-5 seconds for full scan                            │
└─────────────────────────────────────────────────────────────┘
```

**Key design decision:** All three scans are **batched**. A naive approach would call PowerShell once per process (150+ calls, ~30 seconds). UAB batches everything into 3-5 PowerShell calls total.

### Phase 2: IDENTIFY — "What framework is each app?"

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 2: IDENTIFY                         │
│                                                             │
│  For each candidate process:                                │
│                                                             │
│  Step 1: Fast-Path Detection (by executable name)           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ chrome.exe → 'browser'                                │  │
│  │ msedge.exe → 'browser'                                │  │
│  │ winword.exe → 'office'                                │  │
│  │ excel.exe → 'office'                                  │  │
│  │ Confidence: 0.95 (these are guaranteed matches)        │  │
│  └───────────────────────────────────────────────────────┘  │
│          │ not matched                                       │
│          ▼                                                   │
│  Step 2: Framework Signature Matching                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ For each of 10 framework signatures:                  │  │
│  │                                                       │  │
│  │   Score = 0                                           │  │
│  │   + 0.3 per command-line pattern match                │  │
│  │   + 0.4 per process name match                        │  │
│  │   + 0.5 per loaded DLL module match  ← highest signal │  │
│  │   + 0.4 per file pattern match                        │  │
│  │                                                       │  │
│  │   confidence = min(baseConfidence + score × 0.1, 1.0) │  │
│  │                                                       │  │
│  │   First signature with matches > 0 wins               │  │
│  └───────────────────────────────────────────────────────┘  │
│          │ no signature matched                              │
│          ▼                                                   │
│  Step 3: Unknown Framework (has window title)               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Framework: 'unknown', confidence: 0.5                 │  │
│  │ Still controllable via Win-UIA universal fallback!     │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Result: DetectedApp[] with framework + confidence          │
└─────────────────────────────────────────────────────────────┘
```

**The 10 built-in signatures:**

| Framework | Key DLL Modules | Base Confidence |
|-----------|----------------|-----------------|
| Electron | `electron.exe`, `libcef.dll`, `chrome_elf.dll`, `v8.dll` | 0.9 |
| Qt6 | `qt6core.dll`, `qt6gui.dll`, `qt6widgets.dll` | 0.85 |
| Qt5 | `qt5core.dll`, `qt5gui.dll`, `qt5widgets.dll` | 0.85 |
| GTK4 | `libgtk-4-1.dll`, `libgtk-4.dll` | 0.85 |
| GTK3 | `libgtk-3-0.dll`, `libgtk-3.dll` | 0.85 |
| WPF | `wpfgfx_cor3.dll`, `presentationframework.dll` | 0.85 |
| .NET | `coreclr.dll`, `clrjit.dll`, `system.windows.forms.dll` | 0.7 |
| Flutter | `flutter_windows.dll`, `flutter_engine.dll`, `dart.dll` | 0.85 |
| Java | `jvm.dll`, `java.dll`, `jawt.dll` | 0.7 |
| Office | `wwlib.dll`, `xlcall32.dll`, `olmapi32.dll` | 0.9 |

### Phase 3: REGISTER — "Remember everything"

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 3: REGISTER                         │
│                                                             │
│  Every DetectedApp becomes an AppProfile in the registry:   │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  In-Memory Maps (O(1) lookups)                        │  │
│  │                                                       │  │
│  │  apps: Map<executable, AppProfile>                    │  │
│  │    "code.exe"    → { name: "VS Code", framework: ... }│  │
│  │    "excel.exe"   → { name: "EXCEL", framework: ... }  │  │
│  │    "notepad.exe" → { name: "notepad", framework: ... }│  │
│  │                                                       │  │
│  │  pidIndex: Map<pid, executable>                       │  │
│  │    12345 → "code.exe"                                 │  │
│  │    5678  → "excel.exe"                                │  │
│  │    9999  → "notepad.exe"                              │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  JSON Persistence (cross-session survival)            │  │
│  │                                                       │  │
│  │  data/uab-profiles/registry.json                      │  │
│  │  {                                                    │  │
│  │    "version": 1,                                      │  │
│  │    "lastScan": 1709500000000,                         │  │
│  │    "appCount": 79,                                    │  │
│  │    "apps": {                                          │  │
│  │      "code.exe": { ... full profile ... },            │  │
│  │      "excel.exe": { ... full profile ... }            │  │
│  │    }                                                  │  │
│  │  }                                                    │  │
│  │                                                       │  │
│  │  Single file, readable diffs, git-friendly            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Auto-save: Every mutation triggers save (configurable)     │
│  Bulk operations: Batch register defers save to end          │
└─────────────────────────────────────────────────────────────┘
```

**Why dual-indexed Maps?**

- Agents often know the PID (from `scan()` results) → `byPid(pid)` is O(1)
- Agents sometimes know the name → `byName("excel")` does substring search across Map values
- The executable name is the stable key (PIDs change on restart, names don't)

### Phase 4: CONNECT — "Best method, automatically"

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 4: CONNECT                          │
│                                                             │
│  uab.connect("excel")                                       │
│          │                                                   │
│          ▼                                                   │
│  Step 1: Smart Lookup                                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Registry hit? → Use cached AppProfile                 │  │
│  │ Registry miss? → Live detect by name → Register       │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 2: Plugin Selection (cascade)                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ PluginManager.findPlugin(app)                         │  │
│  │ Tries plugins in priority order:                      │  │
│  │   1. ChromeExtPlugin.canHandle()? → WebSocket         │  │
│  │   2. BrowserPlugin.canHandle()?   → CDP               │  │
│  │   3. ElectronPlugin.canHandle()?  → CDP               │  │
│  │   4. OfficePlugin.canHandle()?    → COM+UIA           │  │
│  │   5. QtPlugin.canHandle()?        → UIA               │  │
│  │   6-8. GTK, Java, Flutter         → UIA               │  │
│  │   9. WinUIAPlugin.canHandle()?    → UIA (always true) │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 3: Connection with Retry                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ withRetry(() => router.connect(app), { maxRetries: 1 })│  │
│  │                                                       │  │
│  │ If best plugin fails → RoutedConnection wraps with    │  │
│  │ automatic fallback to next method in cascade          │  │
│  └───────────────────────────────────────────────────────┘  │
│          │                                                   │
│          ▼                                                   │
│  Step 4: Track Connection                                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ConnectionManager tracks: health, uptime, failures    │  │
│  │ ElementCache: ready for enumerate/query calls         │  │
│  │ PermissionManager: ready for action gating            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Returns: { pid, name, framework, method, elementCount }    │
└─────────────────────────────────────────────────────────────┘
```

### Phase 5: LEARN — "Remember what works"

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 5: LEARN                            │
│                                                             │
│  After successful connection:                               │
│                                                             │
│  registry.update('excel.exe', {                             │
│    preferredMethod: 'com+uia',  // This method worked!     │
│    pid: 5678,                   // Update last known PID    │
│    lastSeen: Date.now()         // Update timestamp         │
│  });                                                        │
│                                                             │
│  After successful action:                                   │
│                                                             │
│  cache.invalidateIfNeeded(pid, 'click');                    │
│  // 'click' is mutating → clear cached tree + queries       │
│  // Next query will fetch fresh data                        │
│                                                             │
│  After connection failure:                                  │
│                                                             │
│  connectionManager detects unhealthy → auto-reconnect       │
│  Router tries fallback method → learns new preferred method │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Learning Loop                                        │  │
│  │                                                       │  │
│  │  scan() → register → connect → learn → persist        │  │
│  │     ↑                                      │          │  │
│  │     └──────────────────────────────────────┘          │  │
│  │  Next session: load() → find() → instant connect      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**What UAB learns and remembers:**

| Knowledge | Where Stored | Survives Restart? |
|-----------|-------------|-------------------|
| App exists | AppRegistry Map | Yes (JSON file) |
| Framework type | AppProfile.framework | Yes |
| Best control method | AppProfile.preferredMethod | Yes |
| Last PID | AppProfile.pid | Yes (may be stale) |
| Window title | AppProfile.windowTitle | Yes |
| UI tree structure | ElementCache | No (rebuilt per session) |
| Connection health | ConnectionManager | No (rebuilt per session) |
| Action history | PermissionManager audit | No (in-memory) |

---

## The App Registry (UAB's Brain)

The registry is the central knowledge store. It's what makes UAB "smart" — instead of scanning the system every time, it remembers what it's learned.

### Data Model

```typescript
interface AppProfile {
  executable: string;       // Stable key: "code.exe" (lowercase, immutable)
  name: string;             // Human-readable: "Visual Studio Code"
  pid?: number;             // Last known PID (may be stale after restart)
  framework: FrameworkType; // Detected: "electron"
  confidence: number;       // 0.0-1.0 confidence score
  preferredMethod?: string; // Learned: "cdp" (from successful connection)
  connectionInfo?: object;  // Framework-specific: { debugPort: 9222 }
  path?: string;            // Full path: "C:\\...\\Code.exe"
  windowTitle?: string;     // Last seen: "project - Visual Studio Code"
  lastSeen: number;         // Unix timestamp of last detection
  tags?: string[];          // User-defined: ["dev-tools", "editor"]
}
```

### Lookup Performance

| Operation | Method | Time |
|-----------|--------|------|
| Lookup by PID | `byPid(pid)` → pidIndex Map → apps Map | O(1) |
| Lookup by name | `byName(name)` → iterate + substring match | O(n) but n ≈ 79 |
| Lookup by executable | `byExecutable(exe)` → apps Map | O(1) |
| Lookup by framework | `byFramework(type)` → iterate + filter | O(n) |
| Get all | `all()` → Map.values() | O(1) |

### Persistence Flow

```
Mutation (register/update/remove)
         │
         ▼
    Set dirty = true
         │
         ▼
    autoSave enabled?
    ┌────┴────┐
    │   Yes   │──▶ save() → JSON.stringify → writeFileSync
    └────┬────┘
    │    No   │──▶ Deferred (caller must save() manually)
    └─────────┘
```

For bulk operations (`registerAll`), auto-save is temporarily disabled to avoid 79 file writes. One save at the end.

---

## The Cascade Pattern

The cascade is how UAB picks the best control method for each app. It's not just "try until something works" — it's an ordered priority list optimized for speed and fidelity.

### Priority Order

```
Priority 1: Chrome Extension Bridge
   │   WebSocket to installed extension. No browser relaunch.
   │   Coverage: Any Chromium browser (Chrome, Edge, Brave)
   │   fail
   ▼
Priority 2: CDP Browser Plugin
   │   Direct CDP WebSocket. Requires --remote-debugging-port.
   │   Coverage: Chromium browsers launched with debug flag
   │   fail
   ▼
Priority 3: Framework-Specific Plugin
   │   Electron CDP, Office COM, etc.
   │   Coverage: Apps matching the plugin's framework
   │   fail
   ▼
Priority 4: Windows UI Automation
   │   Accessibility API fallback. Works with ANY windowed Windows app.
   │   Coverage: Everything with a window
   │   fail
   ▼
Priority 5: Vision (Screenshot + AI)
   │   Last resort for element discovery. Screenshot → Claude Vision API → coordinate input.
   │   Coverage: Anything visible on screen
   │   (requires ANTHROPIC_API_KEY)
   │   fail
   ▼
Priority 6: OS Raw Input Injection
      SendInput() on Windows, CGEventPost() on macOS, xdotool on Linux.
      Injects mouse drag, scroll, and gesture events directly into the OS input stream.
      Coverage: ANY application — the app cannot distinguish injected input from human input.
      Used for: continuous spatial gestures (sculpting, painting, drawing, drag-and-drop)
      NOT used for: commands, menu navigation, text input (keyboard is faster/cheaper)
```

### The Concerto Principle

The cascade is NOT "pick one method per app." It's a concerto — for each micro-operation within a task, the agent picks the most efficient method. Efficiency means four things: **speed**, **outcome quality**, **control precision**, and **cost**.

A typical Blender sculpting session uses 5 methods in a single workflow:
- **Keyboard** (P5): Ctrl+Tab to switch mode, Ctrl+4 to subdivide, F to resize brush
- **Scroll** (P6): zoom in/out on the viewport
- **Drag** (P6): sculpt brush strokes across the mesh surface
- **Screenshot** (Vision): verify the sculpt result after each stroke
- **Middle-drag** (P6): orbit the camera to check from different angles

The agent switches methods per micro-operation, not per app. That's the concerto.

### Why This Order?

| Method | Speed | Fidelity | Coverage | Cost | Disruption |
|--------|-------|----------|----------|------|------------|
| Chrome Extension | Fastest | Perfect | Chromium only | Free | None (already installed) |
| CDP (browser) | Fast | High | Chromium only | Free | Requires debug flag |
| Framework Hook | Fast | High | Framework-specific | Free | Sometimes requires app relaunch |
| Win-UIA | Moderate | Good | Universal | Free | None |
| Vision | Slow | Variable | Universal | API call | None |
| **OS Input Injection** | **Fast** | **Perfect** | **Universal** | **Free** | **None** |

### Automatic Fallback

```
Agent: uab.act(pid, 'btn-1', 'click')
                │
                ▼
        RoutedConnection
                │
        ┌───────┴───────┐
        │  Electron CDP  │ ──▶ WebSocket error!
        └───────┬───────┘
                │ automatic fallback
        ┌───────┴───────┐
        │   Win-UIA     │ ──▶ UIA pattern error!
        └───────┬───────┘
                │ automatic fallback
        ┌───────┴───────┐
        │    Vision     │ ──▶ Screenshot → AI → Click at (x,y) ✓
        └───────────────┘
                │
                ▼
        Agent gets ActionResult (success)
        (never knew about the fallbacks)
```

The `RoutedConnection` class wraps any `PluginConnection` and catches failures, transparently falling back to the next available method.

---

## Framework Detection Engine

### Architecture

```
FrameworkDetector
├── detectAll()      ─── Full system scan (batched)
├── detectElectron() ─── Electron-only scan (faster)
├── detectByPid()    ─── Single PID check
├── findByName()     ─── Name-based search (uses detectAll)
└── cache            ─── Map<pid, DetectedApp>
```

### Batching Strategy

The key performance insight: PowerShell process startup is expensive (~200ms). Module scanning per-process would be (150 processes × 200ms = 30 seconds). UAB batches:

```
Instead of:                          UAB does:
┌────────────────────┐              ┌────────────────────┐
│ PS: Get-Process -1 │ ×150         │ PS: Get-CimInstance│ ×1
│ PS: Get-Process -2 │              │    (all processes)  │
│ PS: Get-Process -3 │              ├────────────────────┤
│ ...                │              │ PS: Get-Process    │ ×3
│ PS: Get-Process -150│              │    -Id @(1,2,...50) │  (batches of 50)
├────────────────────┤              ├────────────────────┤
│ ~30 seconds        │              │ PS: EnumWindows    │ ×1
└────────────────────┘              │    (all titles)    │
                                    ├────────────────────┤
                                    │ ~2-5 seconds       │
                                    └────────────────────┘
```

### Window Title Scanning

UAB compiles a small C# class inline in PowerShell to call Win32 APIs:

```csharp
// Compiled at runtime via Add-Type
public class WinEnum {
  [DllImport("user32.dll")] static extern bool EnumWindows(...);
  [DllImport("user32.dll")] static extern int GetWindowText(...);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(...);

  public static Dictionary<uint, string> GetWindowTitles() {
    // One call enumerates ALL visible windows → returns PID → title map
  }
}
```

This is faster than calling `GetWindowTitle()` per PID because it only iterates visible windows (typically 20-30) instead of all processes (150+).

---

## Control Router

The router maps detected apps to control methods and manages the connection lifecycle.

### Route Structure

```typescript
interface ControlRoute {
  app: DetectedApp;           // The detected app
  method: ControlMethod;      // 'direct-api' | 'uab-hook' | 'accessibility' | 'vision'
  connection: PluginConnection; // Active connection
  fallbacks: ControlMethod[];   // Remaining fallback methods
}
```

### Connection Flow

```typescript
// Router.connect() simplified
async connect(app: DetectedApp): Promise<RoutedConnection> {
  // 1. Find best plugin for this app's framework
  const plugin = this.pluginManager.findPlugin(app);

  if (plugin) {
    try {
      // 2. Try framework-specific connection
      const conn = await plugin.connect(app);
      return new RoutedConnection(conn, this, app);
    } catch {
      // 3. Framework plugin failed — fall through to UIA
    }
  }

  // 4. Accessibility fallback: Win-UIA works for any windowed app
  const uia = this.pluginManager.getPlugin('win-uia');
  const conn = await uia.connect(app);
  return new RoutedConnection(conn, this, app);

  // 5. Vision fallback (last resort): Screenshot → AI → Coordinate input
  // Only used if both framework plugins AND UIA fail
  // Requires ANTHROPIC_API_KEY to be configured
}
```

---

## Plugin Architecture

### Plugin Interface

Every framework plugin implements two interfaces:

```typescript
interface FrameworkPlugin {
  readonly framework: FrameworkType;  // What framework this handles
  readonly name: string;              // Human-readable name
  canHandle(app: DetectedApp): boolean;       // Can this plugin control this app?
  connect(app: DetectedApp): Promise<PluginConnection>;  // Establish connection
}

interface PluginConnection {
  enumerate(): Promise<UIElement[]>;          // Get UI tree
  query(selector: ElementSelector): Promise<UIElement[]>;  // Search elements
  act(elementId, action, params?): Promise<ActionResult>;  // Perform action
  state(): Promise<AppState>;                // Get app state
  disconnect(): Promise<void>;               // Clean up
  connected: boolean;                        // Connection status
}
```

### Registration Order

Plugins are registered in the `UABConnector.start()` method in strict priority order:

```
1. ChromeExtPlugin   — Chrome extension bridge (WebSocket, no relaunch)
2. BrowserPlugin     — Browser CDP (needs --remote-debugging-port)
3. ElectronPlugin    — Electron CDP (framework hook)
4. OfficePlugin      — COM + UIA hybrid (Word, Excel, PPT, Outlook)
5. QtPlugin          — Qt via UIA bridge
6. GtkPlugin         — GTK via UIA bridge
7. JavaPlugin        — Java via JAB→UIA bridge
8. FlutterPlugin     — Flutter via UIA bridge
9. WinUIAPlugin      — Accessibility fallback (ALWAYS returns canHandle=true)
10. VisionPlugin     — Vision fallback (screenshot + Claude Vision API + coordinate input)
                        Last resort — expensive but truly universal.
                        Only available when ANTHROPIC_API_KEY is configured.
```

### Plugin Details

#### Electron Plugin (CDP)
```
Input: DetectedApp { framework: 'electron', connectionInfo: { debugPort: 9222 } }
  │
  ├── Find CDP endpoint: HTTP GET http://localhost:{port}/json/version
  ├── Connect: WebSocket to devtoolsFrontendUrl
  ├── Enumerate: Runtime.evaluate → document.querySelectorAll('*') → map to UIElement[]
  ├── Query: CSS selector matching via CDP
  ├── Act: Input.dispatchMouseEvent / Runtime.evaluate
  └── ~500 LOC of CDP protocol handling
```

#### Office Plugin (COM + UIA Hybrid)
```
Input: DetectedApp { framework: 'office', name: 'EXCEL' }
  │
  ├── Connect: PowerShell creates COM object + UIA automation element
  ├── Enumerate: UIA for UI tree (buttons, menus, ribbons)
  ├── Query: UIA PropertyCondition for UI elements
  ├── Act:
  │   ├── UI actions (click, type) → UIA InvokePattern / ValuePattern
  │   ├── readCell / writeCell → COM Excel.Application.ActiveSheet.Range
  │   ├── readRange / writeRange → COM bulk read/write
  │   ├── getSheets / readFormula → COM workbook properties
  │   ├── readDocument → UIA TextPattern (Word)
  │   ├── readSlides → COM PowerPoint.Application.Presentations
  │   └── composeEmail / sendEmail → COM Outlook.Application.CreateItem
  └── Hybrid approach: best of both COM (data) and UIA (interaction)
```

#### Win-UIA Plugin (Universal Fallback)
```
Input: DetectedApp { any framework or 'unknown' }
  │
  ├── canHandle() → ALWAYS true (this is the catch-all)
  ├── Connect: PowerShell interactive session with Add-Type UIA
  ├── Enumerate: AutomationElement.FindAll(TreeScope.Subtree)
  │   → PowerShell maps UIA ControlType to UAB ElementType
  │   → Builds recursive tree with children
  ├── Query: PropertyCondition / AndCondition / OrCondition matching
  ├── Act:
  │   ├── InvokePattern → click / invoke
  │   ├── ValuePattern → type / clear / read value
  │   ├── TogglePattern → check / uncheck / toggle
  │   ├── ExpandCollapsePattern → expand / collapse
  │   ├── SelectionItemPattern → select
  │   ├── ScrollItemPattern → scroll into view
  │   ├── WindowPattern → minimize / maximize / restore / close
  │   ├── TransformPattern → move / resize
  │   ├── TextPattern → read document text
  │   ├── GridPattern → read table cells
  │   ├── SendKeys → keypress / hotkey
  │   └── Screenshot → BitBlt screen capture to file
  └── ~1500 LOC — the most comprehensive plugin
```

---

## UABConnector vs UABService

UAB provides two API layers:

| Feature | UABConnector | UABService |
|---------|-------------|------------|
| **Instantiation** | `new UABConnector()` | `import { uab }` (singleton) |
| **Multiple instances** | Yes — each gets own state | No — one per process |
| **Dependencies** | Zero (no Grammy, no SQLite) | May reference host integrations |
| **Registry** | Own AppRegistry + JSON file | Shared via connector |
| **Use case** | Any agent framework, CLI, library | Single-consumer apps |
| **Primary API** | `scan()`, `find()`, `connect()` | `detect()`, `connectByName()` |

**Rule of thumb:**
- Building an agent? Use `UABConnector` — it's framework-independent
- Building a Telegram bot or single-purpose tool? Use `UABService` — simpler

---

## Data Flow: End-to-End

### Complete flow: "Click the Submit button in Excel"

```
    Agent: "Click Submit in Excel"
                          │
                          ▼
              ┌───────────────────┐
              │   UABConnector    │
              │   find("excel")   │
              └────────┬──────────┘
                       │
              ┌────────┴──────────┐
              │   App Registry    │──▶ Hit! AppProfile for "excel.exe"
              │   byName("excel") │    framework: 'office', pid: 5678
              └────────┬──────────┘
                       │
                       ▼
              ┌───────────────────┐
              │   UABConnector    │
              │   connect(5678)   │
              └────────┬──────────┘
                       │
              ┌────────┴────────────┐
              │   Control Router    │
              │   connect(app)      │
              └────────┬────────────┘
                       │
              ┌────────┴────────┐
              │ PluginManager   │
              │ findPlugin()    │──▶ OfficePlugin.canHandle() → true
              └────────┬────────┘
                       │
                       ▼
              ┌───────────────────┐
              │  OfficePlugin     │
              │  connect()        │──▶ COM: GetObject("Excel.Application")
              │                   │──▶ UIA: AutomationElement.FromHandle()
              └────────┬──────────┘
                       │
              ┌────────┴────────────┐
              │  Registry Learning  │──▶ Update preferredMethod: 'com+uia'
              └────────┬────────────┘
                       │
                       ▼
              ┌───────────────────┐
              │   UABConnector    │
              │   query(5678,     │
              │     {type:'button',│
              │      label:'Submit'})
              └────────┬──────────┘
                       │
              ┌────────┴────────┐
              │   Cache Check   │──▶ Miss → fetch from connection
              └────────┬────────┘
                       │
                       ▼
              ┌───────────────────┐
              │  OfficePlugin     │
              │  query()          │──▶ UIA PropertyCondition match
              └────────┬──────────┘
                       │
              ┌────────┴────────┐
              │   Cache Store   │──▶ Store result (3s TTL)
              └────────┬────────┘
                       │
                       ▼
              UIElement { id: 'btn-submit', type: 'button', label: 'Submit' }
                       │
                       ▼
              ┌───────────────────┐
              │   UABConnector    │
              │   act(5678,       │
              │     'btn-submit', │
              │     'click')      │
              └────────┬──────────┘
                       │
              ┌────────┴────────────┐
              │  Permission Check   │──▶ Risk: safe → Allowed
              │  record() to audit  │──▶ Audit: { pid, action, element, risk }
              └────────┬────────────┘
                       │
              ┌────────┴────────┐
              │   withRetry()   │──▶ Attempt 1 (maxRetries: 1)
              └────────┬────────┘
                       │
                       ▼
              ┌───────────────────┐
              │  OfficePlugin     │
              │  act('click')     │──▶ UIA InvokePattern.Invoke()
              └────────┬──────────┘
                       │
              ┌────────┴────────────┐
              │  Cache Invalidate   │──▶ 'click' is mutating → clear PID cache
              └────────┬────────────┘
                       │
                       ▼
              ActionResult { success: true }
```

---

## Production Hardening

### Smart Three-Tier Cache

```
┌──────────────────────────────────────────┐
│              Element Cache               │
│                                          │
│  Tree Cache    │  5s TTL per PID         │
│  Query Cache   │  3s TTL, 50 max/PID    │
│  State Cache   │  2s TTL per PID         │
│                                          │
│  Invalidation Triggers:                  │
│  click, type, keypress, navigate,        │
│  setCookie, toggle, expand, hotkey...    │
│                                          │
│  Safe (no invalidation):                 │
│  focus, hover, scroll, screenshot,       │
│  getCookies, getLocalStorage...          │
└──────────────────────────────────────────┘
```

### Permission & Audit

```
Action Received
      │
      ▼
┌─────────────────┐
│ Rate Limit Check │──▶ 100 actions / 60s per PID (sliding window)
└────────┬────────┘
         │ pass
         ▼
┌─────────────────┐
│ Risk Assessment  │──▶ safe / moderate / destructive
└────────┬────────┘
         │
    destructive + blockDestructive?
         │
    ┌────┴────┐
    │   Yes   │──▶ Check confirmDestructive(pid) → block or allow
    └────┬────┘
         │
         ▼
┌─────────────────┐
│  Audit Record    │──▶ { timestamp, pid, app, action, element, risk, allowed }
└─────────────────┘
```

### Retry with Exponential Backoff

```
Operation
  │
  ▼
Attempt 1 ──▶ Success? → Return
  │ fail
  │ Retryable? (ECONNRESET, timeout, EPIPE, socket hang up)
  │    No → Throw immediately
  │    Yes ↓
  ▼
Wait: baseDelay × 2^attempt + jitter (0-30%)
  │
Attempt 2 ──▶ Success? → Return
  │ fail
  ▼
Throw (max retries exhausted)
```

### Connection Health Monitoring

```
Every 30 seconds:
  ┌────────────────────────────────┐
  │  ConnectionManager             │
  │  runHealthChecks()             │
  └──────────┬─────────────────────┘
             │
  For each tracked connection:
    connection.state() ──▶ 5s timeout
             │
        ┌────┴────┐
        │ Success │──▶ Reset failures, update lastHealthy
        └─────────┘
        ┌────┴────┐
        │ Failure │──▶ Increment failures
        └────┬────┘
             │
        failures >= 3? → Attempt reconnect (exponential backoff)
             │
        reconnect failed 3x? → Mark stale → remove after 5 min
```

---

## Session Bridge

UAB works even when running in Session 0 (SSH, Windows Services, Task Scheduler).

### The Problem

```
Session 0 (SSH/Service)          Session 1 (Desktop)
┌──────────────────┐            ┌──────────────────┐
│  UAB Service     │     ✗      │  Excel, VS Code  │
│  PowerShell      │────────    │  Window handles   │
│  UIA calls fail  │            │  UI elements      │
└──────────────────┘            └──────────────────┘
```

### The Solution

```
Session 0                        Session 1
┌──────────────────┐            ┌──────────────────┐
│  UAB Service     │            │                  │
│                  │  schtasks   │  Scheduled Task  │
│  Create task ────┼──/create──▶│  runs with /IT   │
│  with /IT flag   │  /IT       │  flag             │
│                  │            │                  │
│  Read result ◀───┼── temp ────│  Write to temp   │
│  from temp file  │   file     │  file            │
└──────────────────┘            └──────────────────┘
```

`ps-exec.ts` detects Session 0 automatically and routes through the Task Scheduler bridge. The rest of UAB never knows the difference.

---

## Desktop + Server Dual-Mode

UAB v1.0.0 introduces automatic environment detection. ONE codebase works in desktop, server, and container contexts without configuration changes.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Agent Frontends                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────┐   │
│  │  Telegram Bot    │  │  HTTP Server     │  │   Bash CLI  │   │
│  │ (desktop agent)  │  │  (server.ts)     │  │  (any agent)│   │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬──────┘   │
│           │                     │                    │          │
├───────────┼─────────────────────┼────────────────────┼──────────┤
│           ▼                     ▼                    ▼          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            Environment Detection (environment.ts)        │   │
│  │                                                          │   │
│  │  Desktop (Session 1+)  │  Server (Session 0)  │ Container│   │
│  │  • Persistent conns    │  • Stateless          │ • Minimal│   │
│  │  • Extension bridge    │  • Session bridge     │ • Limited│   │
│  │  • 100/min rate limit  │  • 60/min rate limit  │ • 30/min │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                    │
│                            ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              UABConnector (auto-tuned per environment)    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                    │
│                            ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Session Bridge (ps-exec.ts) — Session 0→1 when needed   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                    │
│                            ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Plugin Manager → Framework APIs → Desktop Applications   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### How It Works

1. **On startup**, `environment.ts` detects the runtime:
   - Checks Windows session ID (0 = server, 1+ = desktop)
   - Checks for container indicators (Docker, WSL, cgroup)
   - Verifies if desktop is reachable (direct or via bridge)

2. **UABConnector auto-tunes** based on the detected environment:
   - Desktop: persistent connections, extension bridge enabled, full rate limit
   - Server: stateless connections, no extension bridge, lower rate limit
   - Container: minimal config, aggressive caching

3. **The HTTP server** (`server.ts`) wraps UABConnector:
   - Accepts REST calls from remote agents
   - Auto-connects on demand (agents don't need to manage connections)
   - Localhost-only by default, optional API key auth
   - CORS headers for browser-based agents

4. **Session Bridge** kicks in automatically when in Session 0:
   - Uses Windows Task Scheduler with `/IT` flag
   - Executes PowerShell in the user's interactive session
   - Returns results via temp file I/O
   - Transparent to the rest of UAB

### When to Use Each Mode

| Scenario | Frontend | Mode |
|----------|----------|------|
| Local development | CLI / Library | Desktop (auto) |
| Telegram bot on same machine | Telegram bot | Desktop (auto) |
| Remote agent via SSH | HTTP Server | Server (auto) |
| CI/CD pipeline | CLI | Server (auto) |
| Docker container | HTTP Server | Container (auto) |

---

## Co-work Bridge Architecture

```
Co-work (Linux VM) → Chrome extension → localhost:3100 → UABServer → Desktop Apps
```

Co-work runs in an isolated Linux VM and cannot reach the host's localhost directly. The Chrome extension acts as a relay:

1. UAB installs a SKILL.md into Co-work's plugin directory at `%APPDATA%/Claude/local-agent-mode-sessions/*/cowork_plugins/`
2. Co-work reads the skill and knows to call UAB via Chrome's localhost access
3. The Chrome extension service worker has `onMessage` and `onMessageExternal` handlers that proxy requests to `localhost:3100`
4. UABServer processes the request and returns the result through the same path

The same skill is also written to Claude Code CLI's plugin directory at `~/.claude/plugins/`.

---

## Installer Architecture

The installer (GUI or CLI) performs these steps:
1. Detects host gateway IP (WSL/Hyper-V/vmnet adapter)
2. Generates a persistent API key
3. Creates a system service (Task Scheduler on Windows, launchd on macOS) bound to 0.0.0.0:3100
4. Packs and registers the Chrome extension (.crx + registry keys)
5. Writes SKILL.md to ALL agent locations (CLI + Co-work)
6. Sets ELECTRON_ENABLE_REMOTE_DEBUGGING=1 for CDP access to Electron apps
7. Registers the plugin in Claude Code settings

---

## Input Injection

UAB uses Win32 API calls via PowerShell for input injection:
- `EnumWindows` + `FindByPid` to locate the correct window handle
- `ForceForeground` with thread attachment for reliable window activation
- `SendKeys` for keyboard input (both single keys and bulk text)
- `mouse_event` for click/hover at absolute coordinates
- `PrintWindow` with DPI awareness for hi-res screenshot capture

This approach works with ALL window types including Electron, UWP, and Win32 apps.

---

### Recursive Application Bridge (Flow Library)

The flow library is UAB's procedural memory system. It transforms trial-and-error app interaction into deterministic execution.

**Storage**: `data/flow-library/{appname}.json` — one file per application.

**Flow format**:
```json
{
  "app_name": "Grok",
  "app_framework": "Electron",
  "input_method": "double_tab_activate_then_clipboard_paste",
  "navigation_plan": [
    {"step": 1, "action": "focus"},
    {"step": 2, "action": "keypress", "key": "Tab"},
    ...
  ],
  "known_issues": ["..."],
  "version": "1.2"
}
```

**Endpoints**:
- `GET /flow/list` — all available flows (no auth)
- `GET /flow/{appname}` — pre-built sequence (no auth)
- `POST /flow` — save new/updated flow (auth required)

**Default generation**: When no app-specific flow exists, UAB generates a default based on the detected framework type (Electron, Win32, Office, Qt). Defaults are stored in `_defaults.json`.

**Recursive improvement**: Each interaction either confirms the existing flow or generates a correction. Failed sequences trigger analysis and version bumps. The library grows with use and the error rate approaches zero over time.

**Cross-agent knowledge sharing**: Any agent connected to UAB inherits the entire flow library. One agent's discovery benefits all agents on all machines where the library is deployed.

**Patent relevance**: The recursive application bridge — where the system builds procedural memory from interaction and shares it deterministically across agents — represents a novel capability in the agent-to-application control space. This transforms UAB from an automation tool into an agent operating system with learned procedural knowledge.

---

### X-ray Vision (Deep Query + Invoke)

Standard UI enumeration only traverses a few levels of the accessibility tree. Many interactive elements — especially inside Electron web content — are deeper in the tree and invisible to shallow traversal.

`/deep-query` uses UIA `FindAll` with `TrueCondition` to search the ENTIRE descendant tree. This reveals every element the application exposes through accessibility, regardless of depth. For ChatGPT, this surfaces 123 elements including sidebar conversations, model selector, input field, and action buttons. For Grok, this surfaces Copy/Regenerate/Read Aloud buttons that are invisible to standard enumerate.

`/invoke` uses UIA `InvokePattern` to programmatically activate any element — the equivalent of a mouse click but without requiring window focus or screen coordinates. Combined with FindAll name search and occurrence selection (first/last), this enables precise, named-element interaction.

This is "programmatic visual equivalence through accessibility tree introspection" — the agent perceives applications at the same semantic level as a human user, through the application's own self-description of its interface.

---

## Anti-Screenshot SDK Architecture

The Anti-Screenshot SDK inverts the traditional computer-use approach. Instead of screenshot → vision → coordinates → click, it uses:

1. **UIA Tree** (instant) — element IDs, types, states, structure
2. **Bounding Rects** (instant) — spatial positions, sizes → spatial map
3. **Text Reading** (fast) — TextPattern/ValuePattern content extraction
4. **Vision** (slow) — screenshot + Claude Vision (ONLY when needed)

The spatial map organizes elements into rows and columns using Y-coordinate clustering, enabling agents to understand UI layout from structured data without processing images.

The MCP server exposes 15 tools over stdio, making UAB a native tool for any MCP-compatible agent including Claude Desktop.

Atomic chains solve the menu timing problem by executing all steps (click menu → arrow down → Enter) in a single PowerShell session, preventing focus loss between steps.

---

## Key Design Decisions

See [docs/design-decisions.md](docs/design-decisions.md) for the full rationale behind:
- Why DLL scanning instead of window class matching
- Why dual-indexed Maps instead of SQLite
- Why JSON profiles instead of a database
- Why PowerShell instead of native Node.js bindings
- Why the plugin cascade instead of capability negotiation
- Why the connector pattern for framework independence
- Why ONE codebase for desktop and server instead of separate builds
