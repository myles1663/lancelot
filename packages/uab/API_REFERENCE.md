# API Reference

Complete reference for every public interface in Universal App Bridge.

> The primary API is `UABConnector` — framework-independent, instantiable, zero dependencies. Use it in any agent framework.

---

## Table of Contents

- [UABConnector](#uabconnector) — Primary API (framework-independent)
- [AppRegistry](#appregistry) — In-memory knowledge base with JSON persistence
- [Types](#types) — Core type definitions
- [ElementCache](#elementcache) — Smart three-tier caching
- [PermissionManager](#permissionmanager) — Safety, rate limiting, audit
- [ConnectionManager](#connectionmanager) — Health monitoring & reconnect
- [ChainExecutor](#chainexecutor) — Multi-step workflows
- [Retry Utilities](#retry-utilities) — Error recovery
- [FrameworkDetector](#frameworkdetector) — Process scanning & identification
- [ControlRouter](#controlrouter) — Method cascading & fallback
- [PluginManager](#pluginmanager) — Plugin registry
- [CLI Commands](#cli-commands) — Command-line interface
- [UABServer](#uabserver) — HTTP server for remote access
- [Environment Detection](#environment-detection) — Runtime auto-detection

---

## UABConnector

**Import:** `import { UABConnector } from 'universal-app-bridge'`

The primary API for controlling desktop apps. Framework-independent, instantiable (not singleton), zero dependencies on any agent runtime.

### Constructor

```typescript
new UABConnector(options?: ConnectorOptions)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `profileDir` | `string` | `'data/uab-profiles'` | Directory for JSON profile persistence |
| `persistent` | `boolean` | `false` | Enable connection health monitoring |
| `extensionBridge` | `boolean` | `false` | Enable Chrome extension WebSocket bridge |
| `loadProfiles` | `boolean` | `true` | Load existing profiles on start |
| `rateLimit` | `number` | `100` | Max actions per minute per PID |

### Lifecycle

#### `start(): Promise<void>`

Initialize the connector. Loads profiles, registers plugins, optionally starts extension bridge and connection manager.

**Must be called before any other method.**

#### `stop(): Promise<void>`

Disconnect all connections, stop health monitoring, release all resources.

#### `running: boolean`

Whether the connector is currently active.

---

### Smart Discovery

These methods implement the Smart Function Discovery pipeline.

#### `scan(electronOnly?: boolean): Promise<AppProfile[]>`

**Phase 1+2+3:** Scan the entire system, identify frameworks, and register everything in the registry.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `electronOnly` | `boolean` | `false` | Only scan for Electron apps (faster) |

**Returns:** Array of `AppProfile` objects — every detected app with framework, confidence, and metadata. All results are automatically registered in the `AppRegistry`.

```typescript
const apps = await uab.scan();
// → 79 apps found, frameworks identified, profiles persisted to registry.json
```

**Performance:** 2-5 seconds for full system scan (batched PowerShell calls).

#### `apps(): AppProfile[]`

List all known apps from the registry. **No scan — instant.** Returns whatever is currently in the registry (from `scan()` or `load()`).

```typescript
const known = uab.apps();
// → Instant (O(1) — reads from in-memory Map)
```

#### `find(query: string): Promise<AppProfile[]>`

**Smart lookup:** Checks registry first (instant), falls back to live detection if not found.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `string` | App name to search (case-insensitive, substring match) |

```typescript
const excel = await uab.find('excel');
// Registry hit: instant (< 1ms)
// Registry miss: live detect → register → return (~2s)
```

**The intelligence:** First call to `find("excel")` may need live detection. After that, it's always instant because the registry remembers.

#### `inspectPid(pid: number): Promise<AppProfile | null>`

Check a specific PID. Registry first, live detection fallback.

| Parameter | Type | Description |
|-----------|------|-------------|
| `pid` | `number` | Process ID to inspect |

---

### Connection

#### `connect(pid: number): Promise<ConnectionInfo>`
#### `connect(name: string): Promise<ConnectionInfo>`

Connect to an app by PID or name. Auto-detects if not in registry. Selects best control method via plugin cascade.

```typescript
// By name (searches registry, then live-detects)
const conn = await uab.connect('notepad');

// By PID (checks registry, auto-detects if not found)
const conn = await uab.connect(1234);
```

**Returns:**

```typescript
interface ConnectionInfo {
  pid: number;        // Process ID
  name: string;       // App name
  framework: string;  // Detected framework
  method: string;     // Control method used ('cdp', 'com+uia', 'accessibility')
  elementCount: number; // Total UI elements found
}
```

**What happens internally:**
1. Look up app in registry (or live-detect)
2. Plugin cascade selects best method
3. `withRetry()` wraps the connection attempt
4. Connection manager tracks health (if persistent mode)
5. Registry updated with preferred method (**learning**)
6. Element tree enumerated for count

#### `disconnect(pid: number): Promise<void>`

Disconnect from a specific app.

#### `disconnectAll(): Promise<void>`

Disconnect from all connected apps.

#### `isConnected(pid: number): boolean`

Check if currently connected to a PID.

---

### Core Interaction

#### `enumerate(pid: number, maxDepth?: number): Promise<UIElement[]>`

Get the UI element tree for a connected app. **Cached for 5 seconds.**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pid` | `number` | — | Process ID of connected app |
| `maxDepth` | `number` | `3` | Maximum tree depth |

```typescript
const tree = await uab.enumerate(pid);
// First call: fetches from plugin (~100-500ms)
// Repeat within 5s: instant cache hit
```

#### `query(pid: number, selector: ElementSelector): Promise<UIElement[]>`

Search for specific UI elements. **Cached for 3 seconds**, auto-invalidated after mutating actions.

| Parameter | Type | Description |
|-----------|------|-------------|
| `pid` | `number` | Process ID of connected app |
| `selector` | `ElementSelector` | Search criteria |

**ElementSelector:**

| Field | Type | Description |
|-------|------|-------------|
| `type` | `ElementType` | Element type (e.g., `'button'`, `'textfield'`) |
| `label` | `string` | Label text (case-insensitive substring) |
| `labelExact` | `string` | Exact label match |
| `labelRegex` | `string` | Regex pattern for label |
| `properties` | `Record<string, unknown>` | Property value filters |
| `visible` | `boolean` | Visibility filter |
| `enabled` | `boolean` | Enabled state filter |
| `maxDepth` | `number` | Max search depth |
| `limit` | `number` | Max results to return |

```typescript
// By type
const buttons = await uab.query(pid, { type: 'button' });

// By label (fuzzy)
const submit = await uab.query(pid, { label: 'Submit' });

// Combined with constraints
const visible = await uab.query(pid, { type: 'textfield', visible: true, limit: 5 });
```

#### `act(pid: number, elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult>`

Perform an action on a UI element. **Permission-checked, retried on transient failure, cache-invalidating.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `pid` | `number` | Process ID |
| `elementId` | `string` | Target element ID (from enumerate/query) |
| `action` | `ActionType` | Action to perform |
| `params` | `ActionParams` | Optional action parameters |

**What happens internally:**
1. Permission check (rate limit + risk assessment)
2. Audit record created
3. `withRetry()` wraps the action
4. Cache invalidated if action is mutating

```typescript
await uab.act(pid, 'btn_1', 'click');
await uab.act(pid, 'input_3', 'type', { text: 'Hello' });
await uab.act(pid, 'select_5', 'select', { value: 'Option A' });
await uab.act(pid, 'elem_2', 'scroll', { direction: 'down', amount: 3 });
```

**ActionResult:**

```typescript
{
  success: boolean;
  result?: unknown;       // Return data (cell values, document text, etc.)
  stateChanges?: UIElement[];  // Elements that changed
  error?: string;         // Error message if failed
}
```

#### `state(pid: number): Promise<AppState>`

Get current application state. **Cached for 2 seconds.**

```typescript
const state = await uab.state(pid);
// { window: { title, size, position, focused }, activeElement, modals, menus }
```

---

### Keyboard & Window

#### `keypress(pid: number, key: string): Promise<ActionResult>`

Send a single keypress.

```typescript
await uab.keypress(pid, 'Enter');
await uab.keypress(pid, 'Tab');
await uab.keypress(pid, 'Escape');
```

#### `hotkey(pid: number, keys: string | string[]): Promise<ActionResult>`

Send a key combination.

```typescript
await uab.hotkey(pid, 'ctrl+s');
await uab.hotkey(pid, ['ctrl', 'shift', 's']);
```

#### `window(pid: number, action: string, params?): Promise<ActionResult>`

Window management.

```typescript
await uab.window(pid, 'maximize');
await uab.window(pid, 'minimize');
await uab.window(pid, 'restore');
await uab.window(pid, 'close');
await uab.window(pid, 'move', { x: 100, y: 100 });
await uab.window(pid, 'resize', { width: 800, height: 600 });
```

#### `screenshot(pid: number, outputPath?: string): Promise<ActionResult>`

Capture a screenshot of the app window.

```typescript
await uab.screenshot(pid);  // Default: data/screenshots/uab-{pid}-{timestamp}.png
await uab.screenshot(pid, 'my-screenshot.png');
```

---

### Diagnostics

#### `cacheStats(): CacheStats & { hitRate: number }`

Cache hit/miss statistics.

#### `auditLog(limit?: number): AuditEntry[]`

Recent audit log of all actions. Default limit: 50.

#### `healthSummary(): HealthEntry[]`

Connection health summary (persistent mode only).

---

### Helper Methods

#### `countElements(elements: UIElement[]): number`

Count total elements recursively (including nested children).

#### `flattenTree(elements: UIElement[], maxDepth?, depth?): FlatElement[]`

Flatten a nested UI tree into a flat list with depth info for display.

---

## AppRegistry

**Import:** `import { AppRegistry } from 'universal-app-bridge'`

In-memory knowledge base with JSON persistence. The "brain" of UAB.

### Constructor

```typescript
new AppRegistry(options?: RegistryOptions)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `profileDir` | `string` | `'data/uab-profiles'` | Directory for JSON persistence |
| `autoSave` | `boolean` | `true` | Auto-save after every mutation |

### Persistence

#### `load(): void`

Load profiles from `registry.json`. Safe to call if file doesn't exist.

#### `save(): void`

Persist current registry to JSON file.

### Registration

#### `register(app: DetectedApp): AppProfile`

Register a detected app. Returns the profile (new or updated).

#### `registerAll(apps: DetectedApp[]): AppProfile[]`

Bulk register with single save at the end. Uses deferred auto-save.

#### `update(executable: string, patch: Partial<AppProfile>): boolean`

Update specific fields of an existing profile.

```typescript
registry.update('code.exe', {
  preferredMethod: 'cdp',
  pid: 12345,
  lastSeen: Date.now()
});
```

#### `remove(executable: string): boolean`

Remove an app profile from the registry.

### Lookup

| Method | Returns | Time | Description |
|--------|---------|------|-------------|
| `byPid(pid)` | `AppProfile \| undefined` | O(1) | Lookup via PID index |
| `byName(name)` | `AppProfile[]` | O(n) | Case-insensitive substring match |
| `byExecutable(exe)` | `AppProfile \| undefined` | O(1) | Exact executable key |
| `byFramework(type)` | `AppProfile[]` | O(n) | Filter by framework |
| `all()` | `AppProfile[]` | O(1) | Get all profiles |
| `count()` | `number` | O(1) | Number of registered apps |
| `has(exe)` | `boolean` | O(1) | Check if app exists |

### Conversion

#### `toDetectedApp(profile: AppProfile): DetectedApp`

Convert a registry profile back to `DetectedApp` format for use with router/plugin APIs.

---

## Types

### AppProfile

```typescript
interface AppProfile {
  executable: string;       // Stable key: "code.exe" (lowercase)
  name: string;             // "Visual Studio Code"
  pid?: number;             // Last known PID (may be stale)
  framework: FrameworkType; // "electron"
  confidence: number;       // 0.0-1.0
  preferredMethod?: ControlMethod;  // Learned from connection
  connectionInfo?: Record<string, unknown>;  // Framework-specific
  path?: string;            // Full executable path
  windowTitle?: string;     // Last window title
  lastSeen: number;         // Unix timestamp
  tags?: string[];          // User-defined tags
}
```

### UIElement

```typescript
interface UIElement {
  id: string;                          // Unique element identifier
  type: ElementType;                   // Normalized type
  label: string;                       // Display text / accessible name
  properties: Record<string, unknown>; // Framework-specific properties
  bounds: Bounds;                      // { x, y, width, height }
  children: UIElement[];               // Nested children
  actions: ActionType[];               // Available actions
  visible: boolean;                    // Visibility state
  enabled: boolean;                    // Interactive state
  meta?: Record<string, unknown>;      // Plugin metadata
}
```

### DetectedApp

```typescript
interface DetectedApp {
  pid: number;
  name: string;
  path: string;
  framework: FrameworkType;
  confidence: number;
  connectionInfo?: Record<string, unknown>;
  windowTitle?: string;
}
```

### FrameworkType

```typescript
type FrameworkType =
  | 'electron' | 'browser' | 'qt5' | 'qt6'
  | 'gtk3' | 'gtk4' | 'macos-native' | 'wpf'
  | 'winui' | 'dotnet' | 'flutter'
  | 'java-swing' | 'javafx' | 'office' | 'unknown';
```

### ControlMethod

```typescript
type ControlMethod = 'direct-api' | 'uab-hook' | 'accessibility' | 'vision';
```

### ElementType (32 types)

**Containers:** `window`, `dialog`, `container`, `toolbar`, `statusbar`, `tabpanel`

**Input:** `textfield`, `textarea`, `checkbox`, `radio`, `select`, `slider`

**Lists:** `list`, `listitem`, `table`, `tablerow`, `tablecell`, `tree`, `treeitem`

**Navigation:** `menu`, `menuitem`, `tab`, `link`

**Display:** `label`, `heading`, `image`, `separator`, `progressbar`, `scrollbar`, `tooltip`

**Interactive:** `button`

**Special:** `unknown`

### ActionType (61 types)

**Click:** `click`, `doubleclick`, `rightclick`, `focus`, `hover`, `contextmenu`

**Text:** `type`, `clear`, `select`

**Toggle:** `check`, `uncheck`, `toggle`, `expand`, `collapse`, `invoke`

**Scroll:** `scroll`

**Keyboard:** `keypress`, `hotkey`

**Window:** `minimize`, `maximize`, `restore`, `close`, `move`, `resize`, `screenshot`

**Office:** `readDocument`, `readCell`, `writeCell`, `readRange`, `writeRange`, `getSheets`, `readFormula`, `readSlides`, `readSlideText`, `readEmails`, `composeEmail`, `sendEmail`

**Browser:** `navigate`, `goBack`, `goForward`, `reload`, `getTabs`, `switchTab`, `closeTab`, `newTab`, `getCookies`, `setCookie`, `deleteCookie`, `clearCookies`, `getLocalStorage`, `setLocalStorage`, `deleteLocalStorage`, `clearLocalStorage`, `getSessionStorage`, `setSessionStorage`, `deleteSessionStorage`, `clearSessionStorage`, `executeScript`

### ActionParams

```typescript
interface ActionParams {
  text?: string;             // type action
  value?: string;            // select, writeCell
  direction?: string;        // scroll direction
  amount?: number;           // scroll amount
  key?: string;              // keypress virtual key
  keys?: string[];           // hotkey combo
  x?: number; y?: number;    // move position
  width?: number; height?: number;  // resize dimensions
  outputPath?: string;       // screenshot path
  row?: number; col?: number;  // Excel cell
  sheet?: string;            // Excel sheet name
  cellRange?: string;        // Excel range (A1:B5)
  formula?: string;          // Excel formula
  values?: string[][];       // writeRange 2D array
  to?: string; subject?: string; body?: string;  // Outlook email
  url?: string; domain?: string;  // Browser navigation/cookies
  cookieName?: string; cookieValue?: string;  // Cookie CRUD
  storageKey?: string; storageValue?: string;  // Web storage
  tabId?: string;            // Tab management
  script?: string;           // JavaScript execution
}
```

### AppState

```typescript
interface AppState {
  window: {
    title: string;
    size: { width: number; height: number };
    position: { x: number; y: number };
    focused: boolean;
  };
  activeElement?: UIElement;
  modals: UIElement[];
  menus: UIElement[];
  clipboard?: string;
}
```

---

## ElementCache

**Import:** `import { ElementCache } from 'universal-app-bridge'`

Three-tier cache with TTL and action-triggered invalidation.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `getTree(pid)` | `UIElement[] \| null` | Get cached tree (5s TTL) |
| `setTree(pid, tree)` | `void` | Store tree |
| `getQuery(pid, selector)` | `UIElement[] \| null` | Get cached query (3s TTL) |
| `setQuery(pid, selector, results)` | `void` | Store query |
| `getState(pid)` | `unknown \| null` | Get cached state (2s TTL) |
| `setState(pid, state)` | `void` | Store state |
| `invalidate(pid)` | `void` | Clear all cache for PID |
| `invalidateIfNeeded(pid, action)` | `void` | Clear if action is mutating |
| `shouldInvalidate(action)` | `boolean` | Check if action type is mutating |
| `clear()` | `void` | Clear entire cache |
| `remove(pid)` | `void` | Remove specific PID |
| `getStats()` | `CacheStats` | Hit/miss statistics |
| `getHitRate()` | `number` | Hit rate percentage |

---

## PermissionManager

**Import:** `import { PermissionManager } from 'universal-app-bridge'`

### Constructor

```typescript
new PermissionManager(options?: PermissionOptions)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `blockDestructive` | `boolean` | `false` | Block destructive actions |
| `rateLimit` | `number` | `100` | Max actions per window |
| `rateLimitWindow` | `number` | `60000` | Window in ms |
| `maxAuditEntries` | `number` | `1000` | Max audit log size |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `check(pid, action, app?)` | `PermissionCheck` | Check if action is allowed |
| `record(pid, action, elementId, app, allowed, reason?)` | `void` | Record to audit log |
| `confirmDestructive(pid)` | `void` | Pre-approve destructive actions |
| `getRiskLevel(action)` | `RiskLevel` | Get risk classification |
| `getAuditLog(limit?)` | `AuditEntry[]` | Get recent entries |
| `getRateLimitStatus(pid)` | `RateLimitStatus` | Check rate limit |

### Risk Levels

| Level | Actions | Behavior |
|-------|---------|----------|
| `safe` | click, scroll, focus, hover, screenshot, window mgmt | Always allowed |
| `moderate` | type, select, check, toggle, keypress, hotkey | Allowed, logged |
| `destructive` | close | Configurable blocking |

---

## ConnectionManager

**Import:** `import { ConnectionManager } from 'universal-app-bridge'`

### Constructor

```typescript
new ConnectionManager(router, options?)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `healthCheckInterval` | `number` | `30000` | Check interval in ms |
| `maxHealthFailures` | `number` | `3` | Failures before reconnect |
| `maxReconnectAttempts` | `number` | `3` | Max reconnect attempts |
| `staleTimeout` | `number` | `300000` | Remove after 5 min unhealthy |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `startMonitoring()` | `void` | Begin health checks |
| `stopMonitoring()` | `void` | Stop health checks |
| `track(pid, app, connection)` | `void` | Register connection |
| `untrack(pid, reason?)` | `void` | Remove connection |
| `getAll()` | `ConnectionEntry[]` | Get all entries |
| `getHealthSummary()` | `HealthEntry[]` | Health summary |
| `shutdown()` | `Promise<void>` | Stop everything |

---

## ChainExecutor

**Import:** `import { ChainExecutor } from 'universal-app-bridge'`

### `execute(chain: ChainDefinition): Promise<ChainResult>`

Execute a multi-step action workflow.

### ChainDefinition

```typescript
{
  name: string;          // Chain name
  pid: number;           // Target process
  steps: ChainStep[];    // Steps to execute
  stopOnError?: boolean; // Default: true
  stepDelay?: number;    // Delay between steps (ms, default: 200)
}
```

### Step Types

| Type | Fields | Description |
|------|--------|-------------|
| `action` | `selector`, `action`, `params?` | Find element and perform action |
| `wait` | `selector`, `timeoutMs?`, `pollMs?`, `waitForAbsence?` | Wait for element |
| `conditional` | `selector`, `ifPresent`, `ifAbsent?` | Branch on element presence |
| `delay` | `ms` | Fixed delay |
| `keypress` | `key` | Send keypress |
| `hotkey` | `keys` | Send hotkey |
| `typeText` | `selector`, `text`, `clearFirst?` | Type into element |

### Templates

#### `buildFormChain(pid, name, fields, submitSelector?)`

Build a form-filling chain with optional submit.

#### `buildMenuChain(pid, name, menuPath)`

Build a menu navigation chain (e.g., `['File', 'Save As...']`).

---

## Retry Utilities

**Import:** `import { withRetry, withTimeout, isRetryable } from 'universal-app-bridge'`

### `withRetry<T>(operation, options?): Promise<T>`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `maxRetries` | `number` | `2` | Max retry attempts |
| `baseDelay` | `number` | `500` | Base delay in ms |
| `maxDelay` | `number` | `5000` | Max delay in ms |
| `jitter` | `boolean` | `true` | Add 0-30% random jitter |
| `timeout` | `number` | `30000` | Per-attempt timeout |
| `label` | `string` | `'operation'` | Label for logging |

**Retryable patterns:** `timeout`, `EPIPE`, `ECONNRESET`, `ECONNREFUSED`, `socket hang up`, `powershell exited`, `process not found`, `not responding`

### `withTimeout<T>(operation, timeoutMs, label?): Promise<T>`

Execute with timeout.

### `isRetryable(error: Error): boolean`

Check if error matches retryable patterns.

---

## FrameworkDetector

**Import:** `import { FrameworkDetector } from 'universal-app-bridge'`

| Method | Returns | Description |
|--------|---------|-------------|
| `detectAll()` | `Promise<DetectedApp[]>` | Full system scan (batched) |
| `detectElectron()` | `Promise<DetectedApp[]>` | Electron apps only |
| `detectByPid(pid)` | `Promise<DetectedApp \| null>` | Check specific PID |
| `findByName(name)` | `Promise<DetectedApp[]>` | Search by name |
| `clearCache()` | `void` | Clear detection cache |

---

## ControlRouter

**Import:** `import { ControlRouter } from 'universal-app-bridge'`

| Method | Returns | Description |
|--------|---------|-------------|
| `connect(app)` | `Promise<RoutedConnection>` | Connect with cascade |
| `getRoute(pid)` | `ControlRoute \| undefined` | Get current route |
| `disconnect(pid)` | `Promise<void>` | Disconnect |
| `disconnectAll()` | `Promise<void>` | Disconnect all |

---

## PluginManager

**Import:** `import { PluginManager } from 'universal-app-bridge'`

| Method | Returns | Description |
|--------|---------|-------------|
| `register(plugin)` | `void` | Register plugin (order matters!) |
| `findPlugin(app)` | `FrameworkPlugin \| null` | Find matching plugin |
| `connect(app)` | `Promise<PluginConnection>` | Connect via plugin |
| `getConnection(pid)` | `PluginConnection \| undefined` | Get active connection |
| `disconnect(pid)` | `Promise<void>` | Disconnect |
| `disconnectAll()` | `Promise<void>` | Disconnect all |

---

## CLI Commands

All commands output JSON. Usage: `node dist/uab/cli.js <command> [args]`

### Smart Discovery Commands

| Command | Args | Description |
|---------|------|-------------|
| `scan` | `[--electron]` | Scan system, identify frameworks, register all apps |
| `apps` | — | List known apps from registry (instant, no scan) |
| `find` | `<name>` | Smart lookup: registry first, live detection fallback |
| `profiles` | — | Show full registry with all metadata |

### Connection & Interaction

| Command | Args | Description |
|---------|------|-------------|
| `connect` | `<name\|pid>` | Connect with automatic method selection |
| `enumerate` | `<pid> [--depth N]` | Get flattened UI tree |
| `query` | `<pid> [--type T] [--label L] [--limit N]` | Search elements |
| `act` | `<pid> <elementId> <action> [--text T] [--value V]` | Perform action |
| `state` | `<pid>` | Get app state |

### Keyboard & Window

| Command | Args | Description |
|---------|------|-------------|
| `keypress` | `<pid> <key>` | Send keypress |
| `hotkey` | `<pid> <key1+key2+...>` | Send hotkey |
| `window` | `<pid> min\|max\|restore\|close\|move\|resize` | Window control |
| `screenshot` | `<pid> [--output path]` | Capture screenshot |

### Browser

| Command | Args | Description |
|---------|------|-------------|
| `navigate` | `<pid> <url>` | Navigate to URL |
| `tabs` | `<pid>` | List tabs |
| `switchtab` | `<pid> <tabId>` | Switch tab |
| `newtab` | `<pid> [url]` | Open new tab |
| `closetab` | `<pid> [tabId]` | Close tab |
| `cookies` | `<pid> [--name N] [--domain D]` | Get cookies |
| `setcookie` | `<pid> --name N --value V [opts]` | Set cookie |
| `deletecookie` | `<pid> --name N [--domain D]` | Delete cookie |
| `clearcookies` | `<pid> [--domain D]` | Clear cookies |
| `storage` | `<pid> [--type local\|session] [--action ...]` | Web storage |
| `exec` | `<pid> "<javascript>"` | Execute JavaScript |

### Workflows & Extension

| Command | Args | Description |
|---------|------|-------------|
| `chain` | `<json>` or `--json '{...}'` | Execute action chain |
| `ext-status` | — | Check extension bridge status |
| `ext-install` | — | Extension install guide |
| `help` | — | Show all commands |

### Server & Environment

| Command | Args | Description |
|---------|------|-------------|
| `serve` | `[--port 3100] [--host 127.0.0.1] [--api-key KEY]` | Start HTTP server |
| `env` | — | Show detected environment and defaults |

---

## UABServer

**Import:** `import { UABServer } from 'universal-app-bridge/server'`

HTTP server that wraps UABConnector for remote access. Zero dependencies beyond Node's built-in `http` module.

### Constructor

```typescript
new UABServer(options?: ServerOptions)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `port` | `number` | `3100` | Port to listen on |
| `host` | `string` | `'0.0.0.0'` | Bind address |
| `apiKey` | `string` | — | Required. `X-API-Key` header on all POST requests |
| `connector` | `ConnectorOptions` | Auto-detected | Override connector settings |
| `maxBodySize` | `number` | `1048576` | Max request body size in bytes (1MB) |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `start()` | `Promise<void>` | Start the HTTP server |
| `stop()` | `Promise<void>` | Stop and clean up |
| `running` | `boolean` | Whether server is listening |
| `address` | `string` | Full server URL |

### Authentication

All POST endpoints require the `X-API-Key` header. The API key is generated during installation and stored at:
- Windows: `%LOCALAPPDATA%\UAB Bridge\api-key`
- macOS: `~/Library/Application Support/UAB Bridge/api-key`

GET /health is exempt from authentication.

### Endpoints

#### Discovery

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/scan` | `{ electronOnly?: boolean }` | Scan for apps |
| POST | `/apps` | `{ framework?: string }` | List known apps |
| POST | `/find` | `{ query: string }` | Search by name |

#### Connection & Interaction

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/connect` | `{ target: string \| number }` | Connect to app |
| POST | `/disconnect` | `{ pid: number }` | Disconnect |
| POST | `/enumerate` | `{ pid: number, maxDepth?: number }` | Get UI tree |
| POST | `/query` | `{ pid: number, selector?: ElementSelector }` | Search elements |
| POST | `/act` | `{ pid: number, elementId?: string, action: string, params?: object }` | Perform action |
| POST | `/state` | `{ pid: number }` | Get app state |

#### Application Launch & Focus

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/open` | `{ "target": "notepad" }` | Launch an application |
| POST | `/focus` | `{ "pid": 1234 }` or `{ "name": "chatgpt" }` | Bring an app window to the foreground |
| POST | `/describe` | `{ "pid": 1234 }` or `{ "name": "chatgpt" }` | Screenshot + Vision AI description (requires ANTHROPIC_API_KEY) |

##### POST /open

Launch an application.

```json
{ "target": "notepad" }
```

Returns: `{ "success": true, "message": "Launched notepad" }`

##### POST /focus

Bring an application window to the foreground.

```json
{ "pid": 1234 }
// or
{ "name": "chatgpt" }
```

Returns: `{ "success": true, "pid": 1234, "title": "ChatGPT" }`

##### POST /describe

Screenshot an application and get a text description via Vision AI (requires ANTHROPIC_API_KEY).

```json
{ "pid": 1234 }
// or
{ "name": "chatgpt" }
```

Returns: `{ "pid": 1234, "screenshot": "path/to/file.png", "description": "..." }`

#### Keyboard, Window & Screenshot

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/keypress` | `{ pid: number, key: string }` | Send keypress |
| POST | `/hotkey` | `{ pid: number, keys: string \| string[] }` | Send hotkey |
| POST | `/window` | `{ pid: number, action: string, params?: object }` | Window control |
| POST | `/screenshot` | `{ pid: number, outputPath?: string }` | Capture screenshot |

#### P6 — OS Raw Input Injection

Direct OS-level mouse input injection. Used for continuous spatial gestures (sculpting, painting, drawing, drag-and-drop). The application cannot distinguish injected input from human input.

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/drag` | `{ pid: number, path: [{x,y},...], button?: string, stepDelay?: number }` | Drag along coordinate path |
| POST | `/scroll` | `{ pid: number, x: number, y: number, amount: number }` | Scroll at coordinates |

**`/drag` parameters:**
- `pid` — Target process ID
- `path` — Array of `{x, y}` waypoints (minimum 2 points). Screen coordinates.
- `button` — `"left"` (default), `"middle"`, or `"right"`
- `stepDelay` — Milliseconds between waypoints (default: 10). Use 25-40ms for natural brush strokes.

**`/scroll` parameters:**
- `pid` — Target process ID
- `x`, `y` — Screen coordinates where the scroll occurs
- `amount` — Scroll notches. Positive = up, negative = down. Each unit = 120 wheel delta.

**Example — Sculpt brush stroke in Blender:**
```bash
curl -X POST http://localhost:3100/drag -d '{
  "pid": 67364,
  "button": "left",
  "path": [{"x":1200,"y":400},{"x":1250,"y":390},{"x":1300,"y":385},{"x":1350,"y":390},{"x":1400,"y":400}],
  "stepDelay": 30
}'
```

**Example — Orbit camera with middle-mouse drag:**
```bash
curl -X POST http://localhost:3100/drag -d '{
  "pid": 67364,
  "button": "middle",
  "path": [{"x":1200,"y":500},{"x":1200,"y":400},{"x":1200,"y":300}],
  "stepDelay": 25
}'
```

#### Diagnostics

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Health check + environment info |
| GET | `/info` | — | API info + available endpoints |
| POST | `/cache-stats` | — | Cache hit/miss stats |
| POST | `/audit-log` | `{ limit?: number }` | Recent audit entries |
| POST | `/health-summary` | — | Connection health summary |
| POST | `/environment` | — | Runtime environment details |

---

## Flow Library

The flow library provides pre-built interaction sequences for known applications. These endpoints allow agents to retrieve and contribute learned app control sequences.

### `GET /flow/list`

List all available flows. **No authentication required.**

**Request:**
```bash
curl -s http://localhost:3100/flow/list
```

**Response:**
```json
{
  "success": true,
  "flows": [
    { "app_name": "ChatGPT", "app_framework": "Electron", "version": "1.0" },
    { "app_name": "Grok", "app_framework": "Electron", "version": "1.2" },
    { "app_name": "Notepad", "app_framework": "Win32", "version": "1.0" },
    { "app_name": "Excel", "app_framework": "Office", "version": "1.1" },
    { "app_name": "Slack", "app_framework": "Electron", "version": "1.0" },
    { "app_name": "Discord", "app_framework": "Electron", "version": "1.0" }
  ]
}
```

### `GET /flow/{appname}`

Get the pre-built interaction sequence for a specific app. **No authentication required.**

**Request:**
```bash
curl -s http://localhost:3100/flow/grok
```

**Response:**
```json
{
  "success": true,
  "flow": {
    "app_name": "Grok",
    "app_framework": "Electron",
    "input_method": "double_tab_activate_then_clipboard_paste",
    "navigation_plan": [
      { "step": 1, "action": "focus" },
      { "step": 2, "action": "keypress", "key": "Tab" },
      { "step": 3, "action": "keypress", "key": "Tab" },
      { "step": 4, "action": "activate_input" },
      { "step": 5, "action": "clipboard_paste", "text": "{user_input}" },
      { "step": 6, "action": "keypress", "key": "Enter" }
    ],
    "known_issues": ["Input field requires Tab activation before paste"],
    "version": "1.2"
  }
}
```

If no app-specific flow exists, UAB returns a framework-based default:

```json
{
  "success": true,
  "flow": {
    "app_name": "unknown-electron-app",
    "app_framework": "Electron",
    "input_method": "default_electron",
    "navigation_plan": [
      { "step": 1, "action": "focus" },
      { "step": 2, "action": "keypress", "key": "Tab" },
      { "step": 3, "action": "type", "text": "{user_input}" },
      { "step": 4, "action": "keypress", "key": "Enter" }
    ],
    "known_issues": [],
    "version": "0.1",
    "is_default": true
  }
}
```

### `POST /flow`

Save a new or updated flow after discovering a working interaction sequence. **Authentication required** (`X-API-Key` header).

**Request:**
```bash
curl -s -X POST http://localhost:3100/flow \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "MyApp",
    "app_framework": "Electron",
    "input_method": "single_tab_then_type",
    "navigation_plan": [
      {"step": 1, "action": "focus"},
      {"step": 2, "action": "keypress", "key": "Tab"},
      {"step": 3, "action": "type", "text": "{user_input}"},
      {"step": 4, "action": "keypress", "key": "Enter"}
    ],
    "known_issues": [],
    "version": "1.0"
  }'
```

**Response:**
```json
{
  "success": true,
  "message": "Flow saved for MyApp",
  "path": "data/flow-library/myapp.json"
}
```

Flow files are stored in `data/flow-library/`. Framework defaults are in `data/flow-library/_defaults.json`.

---

## Anti-Screenshot SDK

### POST /focused

Get the currently focused element with its tree path.

**Request:**
```json
{ "pid": 1234 }
```

**Response:**
```json
{
  "success": true,
  "focused": {
    "name": "Message input",
    "type": "Edit",
    "path": "Window > Pane > Group > Edit",
    "bounds": { "x": 100, "y": 500, "w": 600, "h": 40 }
  }
}
```

### POST /find-by-path

Find elements by tree path or parent context. Solves the "5 elements named Close" problem.

**Request:**
```json
{ "pid": 1234, "path": "Window > Menu > MenuItem[File]" }
```

### POST /watch

Watch for state changes (focus, element tree).

**Request:**
```json
{ "pid": 1234, "duration": 5000 }
```

### POST /atomic

Execute a multi-step action chain atomically in a single PowerShell session (no focus loss between steps).

**Request:**
```json
{
  "pid": 1234,
  "steps": [
    { "action": "invoke", "name": "File" },
    { "action": "invoke", "name": "Save As..." }
  ]
}
```

### POST /smart-invoke

6-method element activation cascade: InvokePattern, SetFocus, ValuePattern, ExpandCollapsePattern, coordinate click, parent invoke.

**Request:**
```json
{ "pid": 1234, "name": "Submit", "occurrence": "first" }
```

### POST /spatial-map

Get spatial layout of the application organized by rows and columns.

**Request:**
```json
{ "pid": 1234 }
```

**Response:**
```json
{
  "success": true,
  "rows": [
    { "y": 0, "elements": ["File", "Edit", "View", "Help"] },
    { "y": 30, "elements": ["toolbar_btn_1", "toolbar_btn_2"] }
  ]
}
```

---

## Deep Query & Invoke

### POST /deep-query
Scan entire UI tree for all named/actionable elements.
Request: `{"pid": 1234}` or `{"pid": 1234, "name": "Copy"}` or `{"pid": 1234, "type": "button"}`
Response: `{"pid": 1234, "count": 123, "elements": [{"name": "Copy", "type": "Button", "actions": "InvokePattern", "x": 100, "y": 200, "w": 33, "h": 32}, ...]}`

### POST /invoke
Find an element by name and invoke it directly.
Request: `{"pid": 1234, "name": "Copy", "occurrence": "last"}`
Response: `{"success": true, "name": "Copy", "totalMatches": 6, "clipboardText": "...", "clipboardLength": 1167}`
- occurrence: "first", "last", or numeric index

---

## Environment Detection

**Import:** `import { detectEnvironment, getDefaults, env } from 'universal-app-bridge/environment'`

### `detectEnvironment(): EnvironmentInfo`

Returns detected runtime context (cached after first call).

```typescript
interface EnvironmentInfo {
  mode: 'desktop' | 'server' | 'container';
  hasDesktop: boolean;       // Whether a desktop session is reachable
  sessionId: number;         // Windows session ID (0 = non-interactive)
  isContainer: boolean;      // Docker, WSL, etc.
  needsBridge: boolean;      // Whether Session 0→1 bridge is needed
  platform: string;
  arch: string;
  nodeVersion: string;
}
```

### `getDefaults(mode?): EnvironmentDefaults`

Returns environment-appropriate defaults for UABConnector.

```typescript
interface EnvironmentDefaults {
  persistent: boolean;         // Desktop: true, Server/Container: false
  extensionBridge: boolean;    // Desktop: true, Server/Container: false
  rateLimit: number;           // Desktop: 100, Server: 60, Container: 30
  cacheTTLMultiplier: number;  // Desktop: 1, Server: 2, Container: 3
}
```

### `env` (Proxy)

Convenience proxy that auto-calls `detectEnvironment()`:

```typescript
import { env } from 'universal-app-bridge/environment';

if (env.mode === 'desktop') { /* interactive mode */ }
if (env.hasDesktop) { /* can reach desktop session */ }
if (env.needsBridge) { /* using Task Scheduler bridge */ }
```

### `resetEnvironment(): void`

Clear cached detection (for testing).
