/**
 * UABConnector - framework-independent desktop app control.
 *
 * Public API surfaces:
 *   - Library import for TypeScript callers
 *   - CLI commands with JSON output
 *   - HTTP service mode through UABServer
 *
 * Design constraints:
 *   - No dependency on a specific agent framework
 *   - Instantiable rather than singleton-bound
 *   - In-memory registry for lookups, JSON profiles for persistence
 *   - Returns plain JSON-serializable objects
 *
 * @example
 * ```ts
 * // Library usage
 * import { UABConnector } from './uab/connector.js';
 *
 * const uab = new UABConnector();
 * await uab.start();
 * const apps = await uab.scan();
 * await uab.connect(apps[0].pid);
 * const buttons = await uab.query(apps[0].pid, { type: 'button' });
 * await uab.act(apps[0].pid, buttons[0].id, 'click');
 * await uab.stop();
 * ```
 *
 * @example
 * ```bash
 * # CLI usage (any agent framework)
 * node dist/uab/cli.js scan
 * node dist/uab/cli.js apps
 * node dist/uab/cli.js connect notepad
 * node dist/uab/cli.js query 1234 --type button
 * node dist/uab/cli.js act 1234 btn_1 click
 * ```
 */

import { detectEnvironment, getDefaults } from './environment.js';
import { FrameworkDetector } from './detector.js';
import { PluginManager } from './plugins/base.js';
import { ElectronPlugin } from './plugins/electron/index.js';
import { BrowserPlugin } from './plugins/browser/index.js';
import { ChromeExtPlugin } from './plugins/chrome-ext/index.js';
import { ExtensionWSServer } from './plugins/chrome-ext/ws-server.js';
import { DirectApiPlugin } from './plugins/direct-api/index.js';
import { WinUIAPlugin } from './plugins/win-uia/index.js';
import { QtPlugin } from './plugins/qt/index.js';
import { GtkPlugin } from './plugins/gtk/index.js';
import { JavaPlugin } from './plugins/java/index.js';
import { FlutterPlugin } from './plugins/flutter/index.js';
import { OfficePlugin } from './plugins/office/index.js';
import { ControlRouter } from './router.js';
import { ElementCache } from './cache.js';
import { PermissionManager } from './permissions.js';
import { withRetry } from './retry.js';
import { ConnectionManager } from './connection-manager.js';
import { AppRegistry } from './registry.js';
import type { AppProfile } from './registry.js';
import type {
  DetectedApp, UIElement, ElementSelector,
  ActionType, ActionParams, ActionResult, AppState,
  FocusedElementInfo, PathSelector, AtomicChainDef, AtomicChainResult,
  SmartResolveResult, StateChangeEvent, StateChangeCallback,
  FrameworkHookDescriptor,
  ConcertoMethod,
  ControlRoute,
  OperationPlanContext,
} from './types.js';
import type { FrameworkSignature } from './detector.js';
import { CompositeEngine } from './composite.js';
import type { CompositeResult, CompositeOptions } from './composite.js';
import type { SpatialMap, SpatialElement } from './spatial.js';
import { getConcertoMethodInventory, planOperation } from './concerto.js';

// ─── Options ────────────────────────────────────────────────────

export interface ConnectorOptions {
  /** Directory for JSON profile persistence. Default: "data/uab-profiles" */
  profileDir?: string;
  /** Enable persistent connections with health monitoring. Default: auto-detected from environment */
  persistent?: boolean;
  /** Enable Chrome extension WebSocket bridge. Default: auto-detected from environment */
  extensionBridge?: boolean;
  /** Load existing profiles on start. Default: true */
  loadProfiles?: boolean;
  /** Max actions per minute per PID (rate limiting). Default: auto-detected from environment */
  rateLimit?: number;
  /** Force a specific runtime mode instead of auto-detecting. */
  mode?: 'desktop' | 'server' | 'container';
}

// ─── Connection Info ────────────────────────────────────────────

export interface ConnectionInfo {
  pid: number;
  name: string;
  framework: string;
  method: string;
  elementCount: number;
}

// ─── Connector ──────────────────────────────────────────────────

export class UABConnector {
  readonly registry: AppRegistry;

  private detector: FrameworkDetector;
  private pluginManager: PluginManager;
  private router: ControlRouter;
  private cache: ElementCache;
  private permissions: PermissionManager;

  // Optional persistent-mode modules
  private connectionMgr: ConnectionManager | null = null;
  private extensionServer: ExtensionWSServer | null = null;

  private opts: Required<Omit<ConnectorOptions, 'mode'>> & { mode: string };
  private started = false;

  constructor(options?: ConnectorOptions) {
    // Auto-detect environment defaults if not explicitly set
    const envDefaults = getDefaults(options?.mode ?? detectEnvironment().mode);

    this.opts = {
      profileDir: options?.profileDir || 'data/uab-profiles',
      persistent: options?.persistent ?? envDefaults.persistent,
      extensionBridge: options?.extensionBridge ?? envDefaults.extensionBridge,
      loadProfiles: options?.loadProfiles ?? true,
      rateLimit: options?.rateLimit ?? envDefaults.rateLimit,
      mode: options?.mode ?? detectEnvironment().mode,
    };

    this.registry = new AppRegistry({ profileDir: this.opts.profileDir });
    this.detector = new FrameworkDetector();
    this.pluginManager = new PluginManager();
    this.router = new ControlRouter(this.pluginManager);
    this.cache = new ElementCache();
    this.permissions = new PermissionManager({ rateLimit: this.opts.rateLimit });
  }

  // ─── Lifecycle ──────────────────────────────────────────────────

  /** Initialize the connector. Call before any other method. */
  async start(): Promise<void> {
    if (this.started) return;

    // Load saved profiles
    if (this.opts.loadProfiles) {
      this.registry.load();
    }

    // Extension bridge (optional)
    if (this.opts.extensionBridge) {
      this.extensionServer = new ExtensionWSServer();
      try {
        await this.extensionServer.start();
      } catch {
        this.extensionServer = null;
      }
    }

    // Register plugins (priority order: specific → generic)
    this.pluginManager.register(new DirectApiPlugin());
    if (this.extensionServer) {
      this.pluginManager.register(new ChromeExtPlugin(this.extensionServer));
    }
    this.pluginManager.register(new BrowserPlugin());
    this.pluginManager.register(new ElectronPlugin());
    this.pluginManager.register(new OfficePlugin());
    this.pluginManager.register(new QtPlugin());
    this.pluginManager.register(new GtkPlugin());
    this.pluginManager.register(new JavaPlugin());
    this.pluginManager.register(new FlutterPlugin());
    this.pluginManager.register(new WinUIAPlugin());

    // Persistent mode: connection manager with health monitoring
    if (this.opts.persistent) {
      this.connectionMgr = new ConnectionManager(this.router);
      this.connectionMgr.startMonitoring();
    }

    this.started = true;
  }

  /** Stop the connector and release all resources. */
  async stop(): Promise<void> {
    if (!this.started) return;

    if (this.connectionMgr) {
      await this.connectionMgr.shutdown();
      this.connectionMgr = null;
    }
    if (this.extensionServer) {
      await this.extensionServer.stop();
      this.extensionServer = null;
    }

    await this.router.disconnectAll();
    this.cache.clear();
    this.permissions.clear();
    this.started = false;
  }

  /** Is the connector running? */
  get running(): boolean { return this.started; }

  // ─── Discovery ──────────────────────────────────────────────────

  /**
   * Scan for all controllable apps and register them.
   * Returns fresh profiles with live PIDs.
   */
  async scan(electronOnly = false): Promise<AppProfile[]> {
    this.ensureStarted();
    const detected = electronOnly
      ? await this.detector.detectElectron()
      : await this.detector.detectAll();

    return this.registry.registerAll(detected);
  }

  /**
   * List known apps from registry (direct lookup, no scan).
   * Call scan() first to populate, or load() to restore saved profiles.
   */
  apps(): AppProfile[] {
    return this.registry.all();
  }

  /**
   * List the framework hooks that are currently registered in this connector.
   * This is the source of truth for what the standalone runtime can actually use.
   */
  hookInventory(): FrameworkHookDescriptor[] {
    return this.pluginManager.getHookInventory();
  }

  signatureInventory(): FrameworkSignature[] {
    return this.detector.getSignatureInventory();
  }

  concertoInventory() {
    return getConcertoMethodInventory();
  }

  /**
   * Search registry by name (fuzzy, case-insensitive).
   * If no results in registry, falls back to live detection.
   */
  async find(query: string): Promise<AppProfile[]> {
    this.ensureStarted();

    // Try registry first
    const cached = this.registry.byName(query);
    if (cached.length > 0) return cached;

    // Fall back to live detection
    const detected = await this.detector.findByName(query);
    if (detected.length === 0) return [];

    return detected.map(app => this.registry.register(app));
  }

  /** Inspect a specific PID. */
  async inspectPid(pid: number): Promise<AppProfile | null> {
    this.ensureStarted();

    // Check registry first
    const cached = this.registry.byPid(pid);
    if (cached) return cached;

    // Live detect
    const app = await this.detector.detectByPid(pid);
    if (!app) return null;

    return this.registry.register(app);
  }

  // ─── Connection ─────────────────────────────────────────────────

  /** Connect to an app by PID. Auto-detects if not in registry. */
  async connect(pid: number): Promise<ConnectionInfo>;
  /** Connect to an app by name. Searches registry, then live-detects. */
  async connect(name: string): Promise<ConnectionInfo>;
  async connect(target: number | string): Promise<ConnectionInfo> {
    this.ensureStarted();

    let app: DetectedApp;

    if (typeof target === 'number') {
      // PID-based connection
      const profile = this.registry.byPid(target);
      if (profile) {
        app = this.registry.toDetectedApp(profile);
      } else {
        const detected = await this.detector.detectByPid(target);
        if (!detected) throw new Error(`No detectable app at PID ${target}`);
        this.registry.register(detected);
        app = detected;
      }
    } else {
      // Name-based connection
      const profiles = await this.find(target);
      if (profiles.length === 0) throw new Error(`No app found matching "${target}"`);

      // For multi-process apps (Electron, etc.), prefer the process with a window title.
      // This avoids connecting to broker/crashpad/GPU subprocesses that have no UI.
      let best = profiles[0];
      if (profiles.length > 1) {
        const withWindow = profiles.filter(p => p.windowTitle && p.windowTitle.length > 0);
        if (withWindow.length > 0) {
          best = withWindow[0];
        }
      }
      app = this.registry.toDetectedApp(best);
    }

    // Ensure we have a valid PID
    if (!app.pid) throw new Error(`App "${app.name}" has no known PID. Run scan() first.`);

    const conn = await withRetry(
      () => this.router.connect(app),
      { maxRetries: 1, label: `connect-${app.name}` },
    );

    // Track in connection manager if persistent
    if (this.connectionMgr) {
      this.connectionMgr.track(app.pid, app, conn);
    }

    // Update registry with connection method
    this.registry.update(this.extractExe(app), {
      preferredMethod: (conn as { method?: string }).method as any,
    });

    // Get element count
    const elements = await conn.enumerate();
    const count = this.countElements(elements);

    return {
      pid: app.pid,
      name: app.name,
      framework: app.framework,
      method: (conn as { method?: string }).method || 'unknown',
      elementCount: count,
    };
  }

  /** Disconnect from an app. */
  async disconnect(pid: number): Promise<void> {
    if (this.connectionMgr) {
      this.connectionMgr.untrack(pid, 'manual');
    }
    this.cache.remove(pid);
    await this.router.disconnect(pid);
  }

  /** Disconnect from all apps. */
  async disconnectAll(): Promise<void> {
    if (this.connectionMgr) {
      for (const entry of this.connectionMgr.getAll()) {
        this.cache.remove(entry.pid);
        this.connectionMgr.untrack(entry.pid, 'disconnect-all');
      }
    }
    await this.router.disconnectAll();
  }

  /** Check if connected to a PID. */
  isConnected(pid: number): boolean {
    const route = this.router.getRoute(pid);
    return !!route && route.connection.connected;
  }

  // ─── Interaction ────────────────────────────────────────────────

  /** Get the UI element tree for a connected app. */
  async enumerate(pid: number, maxDepth = 3): Promise<UIElement[]> {
    this.ensureConnected(pid);

    const cached = this.cache.getTree(pid);
    if (cached) return cached;

    const connection = this.router.getConnection(pid)!;
    const tree = await withRetry(
      () => connection.enumerate(),
      { maxRetries: 1, label: `enumerate-${pid}` },
    );

    this.cache.setTree(pid, tree);
    return tree;
  }

  /** Search for UI elements matching a selector. */
  async query(pid: number, selector: ElementSelector): Promise<UIElement[]> {
    this.ensureConnected(pid);

    const cached = this.cache.getQuery(pid, selector);
    if (cached) return cached;

    const connection = this.router.getConnection(pid)!;
    const results = await withRetry(
      () => connection.query(selector),
      { maxRetries: 1, label: `query-${pid}` },
    );

    this.cache.setQuery(pid, selector, results);
    return results;
  }

  /** Perform an action on a UI element (with permission check + cache invalidation). */
  async act(pid: number, elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    this.ensureConnected(pid);

    const route = this.router.getRoute(pid)!;
    const connection = this.router.getConnection(pid)!;

    // Permission check
    const check = this.permissions.check(pid, action, route.app);
    this.permissions.record(pid, action, elementId, route.app, check.allowed, check.reason);
    if (!check.allowed) {
      return { success: false, error: check.reason };
    }

    const result = await withRetry(
      () => connection.act(elementId, action, params),
      { maxRetries: 1, label: `act-${pid}-${action}` },
    );

    this.cache.invalidateIfNeeded(pid, action);
    return result;
  }

  /** Get current app state. */
  async state(pid: number): Promise<AppState> {
    this.ensureConnected(pid);

    const cached = this.cache.getState(pid);
    if (cached) return cached as AppState;

    const connection = this.router.getConnection(pid)!;
    const appState = await withRetry(
      () => connection.state(),
      { maxRetries: 1, label: `state-${pid}` },
    );

    this.cache.setState(pid, appState);
    return appState;
  }

  // ─── Keyboard & Window ──────────────────────────────────────────

  /** Send a single keypress. */
  async keypress(pid: number, key: string): Promise<ActionResult> {
    this.ensureConnected(pid);
    const plan = this.planForPid(pid, 'keypress');
    if (plan.primaryMethod !== 'keyboard-native') {
      throw new Error(`Unexpected concerto plan for keypress: ${plan.primaryMethod}`);
    }
    const { sendKeypress } = await import('./plugins/vision/input.js');
    const result = sendKeypress(pid, key);
    this.cache.invalidateIfNeeded(pid, 'keypress');
    return result;
  }

  /** Send a hotkey combination (e.g., "ctrl+s" or ['ctrl', 's']). */
  async hotkey(pid: number, keys: string | string[]): Promise<ActionResult> {
    this.ensureConnected(pid);
    const plan = this.planForPid(pid, 'hotkey');
    if (plan.primaryMethod !== 'keyboard-native') {
      throw new Error(`Unexpected concerto plan for hotkey: ${plan.primaryMethod}`);
    }
    const keyArray = typeof keys === 'string' ? keys.split('+').map(k => k.trim()) : keys;
    const { sendHotkey } = await import('./plugins/vision/input.js');
    const result = sendHotkey(pid, keyArray);
    this.cache.invalidateIfNeeded(pid, 'hotkey');
    return result;
  }

  /** P6 raw input injection - drag along a waypoint path. */
  async drag(pid: number, path: Array<{ x: number; y: number }>, stepDelay = 10, button: 'left' | 'middle' | 'right' = 'left'): Promise<ActionResult> {
    this.ensureConnected(pid);
    const plan = this.planForPid(pid, 'drag');
    if (plan.primaryMethod !== 'os-input-injection') {
      throw new Error(`Unexpected concerto plan for drag: ${plan.primaryMethod}`);
    }
    const { dragPath } = await import('./plugins/vision/input.js');
    return dragPath(pid, path, stepDelay, button);
  }

  /** P6 raw input injection - scroll at absolute coordinates. */
  async scroll(pid: number, x: number, y: number, amount: number): Promise<ActionResult> {
    this.ensureConnected(pid);
    const plan = this.planForPid(pid, 'scroll');
    if (plan.primaryMethod !== 'os-input-injection') {
      throw new Error(`Unexpected concerto plan for scroll: ${plan.primaryMethod}`);
    }
    const { scrollAt } = await import('./plugins/vision/input.js');
    return scrollAt(pid, x, y, amount);
  }

  /** Window management (minimize, maximize, restore, close, move, resize). */
  async window(pid: number, action: string, params?: { x?: number; y?: number; width?: number; height?: number }): Promise<ActionResult> {
    this.ensureConnected(pid);

    const actionMap: Record<string, ActionType> = {
      min: 'minimize', max: 'maximize', restore: 'restore', close: 'close',
      move: 'move', resize: 'resize',
      minimize: 'minimize', maximize: 'maximize',
    };

    const mapped = actionMap[action.toLowerCase()] || action as ActionType;
    return this.act(pid, '', mapped, params);
  }

  /** Capture a screenshot of the app window. Returns path + base64 data. */
  async screenshot(pid: number, outputPath?: string): Promise<ActionResult> {
    this.ensureConnected(pid);
    const connection = this.router.getConnection(pid)!;
    void this.planForPid(pid, 'screenshot');
    const path = outputPath || `data/screenshots/uab-${pid}-${Date.now()}.png`;

    // Ensure directory exists
    const { mkdirSync, readFileSync, existsSync } = await import('fs');
    const { dirname } = await import('path');
    mkdirSync(dirname(path), { recursive: true });

    const result = await connection.act('', 'screenshot', { outputPath: path });

    // Always include base64 in the response so remote clients (Co-work) can read it
    if (result.success && !(result as any).base64 && !(result as any).data) {
      const filePath = (result as any).path || path;
      if (existsSync(filePath)) {
        (result as any).data = readFileSync(filePath).toString('base64');
      }
    }

    return result;
  }

  planOperation(pid: number, action: ActionType | 'describe', context?: OperationPlanContext) {
    this.ensureConnected(pid);
    const route = this.router.getRoute(pid)!;
    return planOperation(route.method, action, route.fallbacks, this.buildOperationPlanContext(pid, route, context));
  }

  private planForPid(pid: number, action: ActionType | 'describe', context?: OperationPlanContext) {
    this.ensureConnected(pid);
    const route = this.router.getRoute(pid)!;
    return planOperation(route.method, action, route.fallbacks, this.buildOperationPlanContext(pid, route, context));
  }

  private buildOperationPlanContext(
    pid: number,
    route: ControlRoute,
    context?: OperationPlanContext,
  ): OperationPlanContext {
    const envInfo = detectEnvironment();
    const runtimeMethods = new Set<ConcertoMethod>([route.method, ...route.fallbacks]);
    const visionAvailable = Boolean(process.env.ANTHROPIC_API_KEY);
    if (visionAvailable) {
      runtimeMethods.add('vision');
      runtimeMethods.add('vision-analysis');
    }
    if (envInfo.hasDesktop) {
      runtimeMethods.add('keyboard-native');
      runtimeMethods.add('os-input-injection');
    }

    const directApiAvailable =
      route.method === 'direct-api' ||
      route.fallbacks.includes('direct-api') ||
      Boolean((route.app.connectionInfo as Record<string, unknown> | undefined)?.directApi);
    if (directApiAvailable) {
      runtimeMethods.add('direct-api');
    }

    const healthEntry = this.connectionMgr?.get(pid);
    const baseRuntime = {
      mode: envInfo.mode,
      hasDesktop: envInfo.hasDesktop,
      needsBridge: envInfo.needsBridge,
      platform: envInfo.platform,
      framework: route.app.framework,
      frameworkConfidence: route.app.confidence,
      activeMethod: route.method,
      preferredMethodOrder: [route.method, ...route.fallbacks],
      availableMethods: Array.from(runtimeMethods),
      directApiAvailable,
      visionAvailable,
      connectionHealthy: healthEntry ? healthEntry.healthFailures === 0 : true,
      healthFailures: healthEntry?.healthFailures || 0,
      reconnectAttempts: healthEntry?.reconnectAttempts || 0,
    };
    const runtimeOverride = context?.runtime || {};

    return {
      ...context,
      runtime: {
        ...baseRuntime,
        ...runtimeOverride,
        preferredMethodOrder: runtimeOverride.preferredMethodOrder || baseRuntime.preferredMethodOrder,
        availableMethods: runtimeOverride.availableMethods || baseRuntime.availableMethods,
      },
    };
  }

  // ─── Diagnostics ────────────────────────────────────────────────

  /** Get cache hit statistics. */
  cacheStats() {
    return {
      ...this.cache.getStats(),
      hitRate: this.cache.getHitRate(),
    };
  }

  /** Get recent audit log of actions performed. */
  auditLog(limit = 50) {
    return this.permissions.getAuditLog(limit);
  }

  /** Get health summary (persistent mode only). */
  healthSummary() {
    if (!this.connectionMgr) return [];
    return this.connectionMgr.getHealthSummary();
  }

  /** Get active connections in a daemon-friendly summary shape. */
  getConnections(): ConnectionInfo[] {
    return this.connectionMgr
      ? this.connectionMgr.getAll().map((entry) => ({
          pid: entry.pid,
          name: entry.app.name,
          framework: entry.app.framework,
          method: entry.connection.method,
          elementCount: 0,
        }))
      : [];
  }

  /** Force a health-check sweep in persistent mode. */
  async runHealthChecks(): Promise<void> {
    if (!this.connectionMgr) return;
    await this.connectionMgr.runHealthChecks();
  }

  // ─── Helpers ────────────────────────────────────────────────────

  private ensureStarted(): void {
    if (!this.started) throw new Error('Connector not started. Call start() first.');
  }

  private ensureConnected(pid: number): void {
    this.ensureStarted();
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}. Call connect() first.`);
  }

  private extractExe(app: DetectedApp): string {
    const parts = app.path.replace(/\\/g, '/').split('/');
    return (parts[parts.length - 1] || app.name).toLowerCase();
  }

  // ─── Composite / Spatial Map ────────────────────────────────────

  /** Get the composite engine for advanced spatial queries. */
  get composite(): CompositeEngine {
    if (!this._composite) {
      this._composite = new CompositeEngine(this);
    }
    return this._composite;
  }
  private _composite: CompositeEngine | null = null;

  /**
   * Build a spatial map of the app - bounding rects organized into rows/columns.
   * This is FASTER than screenshots and gives AI structured positional data.
   */
  async spatialMap(pid: number, options?: CompositeOptions): Promise<CompositeResult> {
    this.ensureConnected(pid);
    return this.composite.query(pid, options);
  }

  /**
   * Get a text-based map of the app layout for AI consumption.
   * Replaces screenshots in most use cases.
   */
  async textMap(pid: number, format?: 'detailed' | 'compact' | 'json'): Promise<string> {
    this.ensureConnected(pid);
    const result = await this.composite.textMap(pid, { format });
    return result.text;
  }

  /**
   * Find elements by natural language description using spatial map + text reading.
   * Faster than vision-based element finding.
   */
  async findByDescription(pid: number, description: string): Promise<SpatialElement[]> {
    this.ensureConnected(pid);
    return this.composite.findElement(pid, description);
  }

  // ─── Feature 1: Real-time Focus Tracking ────────────────────────

  /**
   * Get the currently focused element in a window via UIA FocusedElement.
   * No connection required; works with any visible window.
   */
  async focused(pid: number): Promise<FocusedElementInfo> {
    this.ensureStarted();
    const { runPSRawInteractive } = await import('./ps-exec.js');
    const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
if (-not $focused) {
  @{ error = 'No focused element' } | ConvertTo-Json -Compress
  exit
}

# Check if it belongs to our PID
$focusedPid = $focused.Current.ProcessId
if ($focusedPid -ne ${pid}) {
  # Walk up to find if any ancestor belongs to our PID
  $tw = [System.Windows.Automation.TreeWalker]::RawViewWalker
  $check = $focused
  $found = $false
  while ($check) {
    if ($check.Current.ProcessId -eq ${pid}) { $found = $true; break }
    try { $check = $tw.GetParent($check) } catch { break }
  }
  if (-not $found) {
    @{ error = "Focused element belongs to PID $focusedPid, not ${pid}" } | ConvertTo-Json -Compress
    exit
  }
}

# Build tree path from window root to focused element
$path = @()
$tw = [System.Windows.Automation.TreeWalker]::RawViewWalker
$pathEl = $focused
while ($pathEl) {
  $pName = $pathEl.Current.Name -replace '[^\\x20-\\x7E]', ''
  if ($pName) { $path = ,@($pName) + $path }
  if ($pathEl.Current.ProcessId -ne ${pid}) { break }
  try { $pathEl = $tw.GetParent($pathEl) } catch { break }
}

$patterns = @()
try {
  foreach ($p in $focused.GetSupportedPatterns()) {
    $patterns += ($p.ProgrammaticName -replace 'PatternIdentifiers\\.Pattern', '' -replace 'Identifiers\\.Pattern', '')
  }
} catch {}

$rect = $focused.Current.BoundingRectangle
@{
  pid = ${pid}
  name = ($focused.Current.Name -replace '[^\\x20-\\x7E]', '')
  type = ($focused.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '')
  automationId = ($focused.Current.AutomationId -replace '[^\\x20-\\x7E]', '')
  className = ($focused.Current.ClassName -replace '[^\\x20-\\x7E]', '')
  x = if ($rect.X -gt -99999 -and $rect.X -lt 99999) { [int]$rect.X } else { 0 }
  y = if ($rect.Y -gt -99999 -and $rect.Y -lt 99999) { [int]$rect.Y } else { 0 }
  w = if ($rect.Width -gt 0 -and $rect.Width -lt 99999) { [int]$rect.Width } else { 0 }
  h = if ($rect.Height -gt 0 -and $rect.Height -lt 99999) { [int]$rect.Height } else { 0 }
  patterns = ($patterns -join ',')
  path = $path
} | ConvertTo-Json -Compress -Depth 3
`;
    try {
      const raw = runPSRawInteractive(script, 5000);
      const data = JSON.parse(raw);
      if (data.error) throw new Error(data.error);
      return {
        pid,
        name: data.name || '',
        type: data.type || 'Unknown',
        automationId: data.automationId || '',
        bounds: { x: data.x || 0, y: data.y || 0, width: data.w || 0, height: data.h || 0 },
        center: { x: (data.x || 0) + (data.w || 0) / 2, y: (data.y || 0) + (data.h || 0) / 2 },
        patterns: data.patterns ? data.patterns.split(',').filter(Boolean) : [],
        className: data.className || '',
        path: Array.isArray(data.path) ? data.path : [],
        timestamp: Date.now(),
      };
    } catch (err) {
      throw new Error(`Focus tracking failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // ─── Feature 2: Element Addressing by Path ─────────────────────

  /**
   * Find elements by tree path or parent context.
   * Solves the "5 elements named Close" problem.
   *
   * @example
   * // By tree path: Menu Bar → File → Save As...
   * await connector.findByPath(pid, { path: ["File", "Save As..."] })
   *
   * // By parent context: "Close" inside "Settings dialog"
   * await connector.findByPath(pid, { name: "Close", parent: "Settings" })
   */
  async findByPath(pid: number, selector: PathSelector): Promise<Array<{ name: string; type: string; automationId: string; bounds: { x: number; y: number; w: number; h: number }; patterns: string }>> {
    this.ensureStarted();
    const { runPSRawInteractive } = await import('./ps-exec.js');

    const pathJson = selector.path ? JSON.stringify(selector.path).replace(/'/g, "''") : '[]';
    const name = (selector.name || '').replace(/'/g, "''");
    const parent = (selector.parent || '').replace(/'/g, "''");
    const typeFilter = (selector.type || '').replace(/'/g, "''");
    const occurrence = selector.occurrence ?? 'first';

    const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if (-not $win) { Write-Output '[]'; exit }

$pathArr = '${pathJson}' | ConvertFrom-Json
$nameFilter = '${name}'
$parentFilter = '${parent}'
$typeFilter = '${typeFilter}'
$tw = [System.Windows.Automation.TreeWalker]::RawViewWalker

function Get-ElementPath($el, $remaining) {
  if ($remaining.Count -eq 0) { return @($el) }
  $target = $remaining[0]
  $rest = @()
  if ($remaining.Count -gt 1) { $rest = $remaining[1..($remaining.Count-1)] }

  $child = $tw.GetFirstChild($el)
  $results = @()
  while ($child) {
    $childName = $child.Current.Name -replace '[^\\x20-\\x7E]', ''
    if ($childName -eq $target -or $childName -like "*$target*") {
      $results += Get-ElementPath $child $rest
    }
    $child = $tw.GetNextSibling($child)
  }
  return $results
}

function Get-ByParent($el, $parentName, $childName) {
  $allCond = [System.Windows.Automation.Condition]::TrueCondition
  $allElements = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $allCond)

  $results = @()
  foreach ($e in $allElements) {
    $eName = $e.Current.Name -replace '[^\\x20-\\x7E]', ''
    if ($eName -and $eName -like "*$childName*") {
      # Check if any ancestor matches parent name
      $p = $tw.GetParent($e)
      $depth = 0
      while ($p -and $depth -lt 10) {
        $pName = $p.Current.Name -replace '[^\\x20-\\x7E]', ''
        if ($pName -like "*$parentName*") {
          $results += $e
          break
        }
        $p = $tw.GetParent($p)
        $depth++
      }
    }
  }
  return $results
}

$matches = @()
if ($pathArr.Count -gt 0) {
  $matches = Get-ElementPath $win $pathArr
} elseif ($parentFilter) {
  $matches = Get-ByParent $win $parentFilter $nameFilter
} else {
  # Simple name search - exact first, then partial match
  $nameCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, '$nameFilter'
  )
  $found = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $nameCond)
  foreach ($f in $found) { $matches += $f }

  # If no exact match, try partial/contains match
  if ($matches.Count -eq 0 -and $nameFilter) {
    $allCond = [System.Windows.Automation.Condition]::TrueCondition
    $allElements = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $allCond)
    foreach ($el in $allElements) {
      $eName = $el.Current.Name -replace '[^\\x20-\\x7E]', ''
      if ($eName -like "*$nameFilter*") { $matches += $el }
    }
  }
}

# Apply type filter
if ($typeFilter -and $matches.Count -gt 0) {
  $matches = $matches | Where-Object {
    ($_.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '') -like "*$typeFilter*"
  }
}

$results = @()
foreach ($m in $matches) {
  $rect = $m.Current.BoundingRectangle
  $patterns = @()
  try {
    foreach ($p in $m.GetSupportedPatterns()) {
      $patterns += ($p.ProgrammaticName -replace 'PatternIdentifiers\\.Pattern', '' -replace 'Identifiers\\.Pattern', '')
    }
  } catch {}

  $results += @{
    name = ($m.Current.Name -replace '[^\\x20-\\x7E]', '')
    type = ($m.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '')
    automationId = ($m.Current.AutomationId -replace '[^\\x20-\\x7E]', '')
    x = if ($rect.X -gt -99999 -and $rect.X -lt 99999) { [int]$rect.X } else { 0 }
    y = if ($rect.Y -gt -99999 -and $rect.Y -lt 99999) { [int]$rect.Y } else { 0 }
    w = if ($rect.Width -gt 0 -and $rect.Width -lt 99999) { [int]$rect.Width } else { 0 }
    h = if ($rect.Height -gt 0 -and $rect.Height -lt 99999) { [int]$rect.Height } else { 0 }
    patterns = ($patterns -join ',')
  }
}

if ($results.Count -eq 0) { Write-Output '[]' }
else { $results | ConvertTo-Json -Compress -Depth 2 }
`;
    try {
      const raw = runPSRawInteractive(script, 15000);
      let elements = JSON.parse(raw);
      if (!Array.isArray(elements)) elements = [elements];

      // Apply occurrence filter
      if (occurrence === 'first' && elements.length > 1) {
        elements = [elements[0]];
      } else if (occurrence === 'last' && elements.length > 1) {
        elements = [elements[elements.length - 1]];
      } else if (typeof occurrence === 'number' && elements.length > occurrence) {
        elements = [elements[occurrence]];
      }

      return elements;
    } catch (err) {
      throw new Error(`Path search failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // ─── Feature 3: State-change Listener ──────────────────────────

  /**
   * Watch for UIA state changes on a window. Polls efficiently at configurable interval.
   * Returns a snapshot of changes since last check.
   *
   * For real-time push notifications, use the HTTP server's SSE endpoint.
   */
  async watchChanges(pid: number, durationMs: number = 3000, pollMs: number = 200): Promise<StateChangeEvent[]> {
    this.ensureStarted();
    const { runPSRawInteractive } = await import('./ps-exec.js');

    const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$events = [System.Collections.ArrayList]::new()
$pid = ${pid}

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $pid
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if (-not $win) { Write-Output '[]'; exit }

# Snapshot initial state
$initialFocus = [System.Windows.Automation.AutomationElement]::FocusedElement
$initialFocusName = if ($initialFocus) { $initialFocus.Current.Name } else { '' }
$initialTitle = $win.Current.Name

# Register UIA event handlers
$handler = {
  param($sender, $e)
  $script:events.Add(@{
    type = 'structureChanged'
    timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    name = if ($sender) { try { $sender.Current.Name -replace '[^\\x20-\\x7E]', '' } catch { '' } } else { '' }
  }) | Out-Null
}

try {
  [System.Windows.Automation.Automation]::AddStructureChangedEventHandler(
    $win,
    [System.Windows.Automation.TreeScope]::Subtree,
    $handler
  )
} catch {}

# Poll for focus changes and window changes
$endTime = [DateTimeOffset]::UtcNow.AddMilliseconds(${durationMs})
$lastFocusName = $initialFocusName

while ([DateTimeOffset]::UtcNow -lt $endTime) {
  Start-Sleep -Milliseconds ${pollMs}

  # Check focus change
  try {
    $currentFocus = [System.Windows.Automation.AutomationElement]::FocusedElement
    $currentFocusName = if ($currentFocus) { $currentFocus.Current.Name -replace '[^\\x20-\\x7E]', '' } else { '' }
    if ($currentFocusName -ne $lastFocusName) {
      $rect = $currentFocus.Current.BoundingRectangle
      $events.Add(@{
        type = 'focus'
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        name = $currentFocusName
        elType = ($currentFocus.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '')
        automationId = ($currentFocus.Current.AutomationId -replace '[^\\x20-\\x7E]', '')
        x = if ($rect.X -gt -99999) { [int]$rect.X } else { 0 }
        y = if ($rect.Y -gt -99999) { [int]$rect.Y } else { 0 }
        w = if ($rect.Width -gt 0) { [int]$rect.Width } else { 0 }
        h = if ($rect.Height -gt 0) { [int]$rect.Height } else { 0 }
      }) | Out-Null
      $lastFocusName = $currentFocusName
    }
  } catch {}

  # Check window title change
  try {
    $currentTitle = $win.Current.Name
    if ($currentTitle -ne $initialTitle) {
      $events.Add(@{
        type = 'propertyChanged'
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        name = ($currentTitle -replace '[^\\x20-\\x7E]', '')
        property = 'windowTitle'
        oldValue = ($initialTitle -replace '[^\\x20-\\x7E]', '')
      }) | Out-Null
      $initialTitle = $currentTitle
    }
  } catch {}
}

# Cleanup handlers
try {
  [System.Windows.Automation.Automation]::RemoveAllEventHandlers()
} catch {}

if ($events.Count -eq 0) { Write-Output '[]' }
else { $events | ConvertTo-Json -Compress -Depth 3 }
`;
    try {
      const raw = runPSRawInteractive(script, durationMs + 5000);
      let events = JSON.parse(raw);
      if (!Array.isArray(events)) events = [events];

      return events.map((e: any) => ({
        type: e.type || 'propertyChanged',
        timestamp: e.timestamp || Date.now(),
        pid,
        element: e.name ? {
          name: e.name,
          type: e.elType || '',
          automationId: e.automationId || '',
          bounds: { x: e.x || 0, y: e.y || 0, width: e.w || 0, height: e.h || 0 },
        } : undefined,
        details: e.property ? { property: e.property, oldValue: e.oldValue } : undefined,
      }));
    } catch (err) {
      throw new Error(`Watch failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // ─── Feature 4: Atomic Action Chains ───────────────────────────

  /**
   * Execute a sequence of actions in a SINGLE PowerShell session.
   * No focus-stealing between steps. All actions fire atomically.
   *
   * @example
   * await connector.atomicChain({
   *   pid: 38184,
   *   steps: [
   *     { action: 'hotkey', keys: ['alt', 'm'] },
   *     { action: 'wait', ms: 300 },
   *     { action: 'keypress', key: 'Down' },
   *     { action: 'keypress', key: 'Enter' },
   *   ]
   * });
   */
  async atomicChain(chain: AtomicChainDef): Promise<AtomicChainResult> {
    this.ensureStarted();
    const { runPSRawInteractive } = await import('./ps-exec.js');

    // Build PowerShell script for all steps in one session
    const stepScripts: string[] = [];
    for (const step of chain.steps) {
      switch (step.action) {
        case 'wait':
          stepScripts.push(`Start-Sleep -Milliseconds ${step.ms || 200}`);
          break;
        case 'keypress':
          stepScripts.push(`[System.Windows.Forms.SendKeys]::SendWait('${this.psKeyMap(step.key || '')}')`);
          break;
        case 'hotkey': {
          const keys = step.keys || [];
          const modKeys: string[] = [];
          const mainKeys: string[] = [];
          for (const k of keys) {
            const lower = k.toLowerCase();
            if (['ctrl', 'control'].includes(lower)) modKeys.push('^');
            else if (['alt'].includes(lower)) modKeys.push('%');
            else if (['shift'].includes(lower)) modKeys.push('+');
            else mainKeys.push(this.psKeyMap(k));
          }
          const combo = modKeys.join('') + '(' + mainKeys.join('') + ')';
          stepScripts.push(`[System.Windows.Forms.SendKeys]::SendWait('${combo}')`);
          break;
        }
        case 'click': {
          const cx = step.x || 0;
          const cy = step.y || 0;
          stepScripts.push(`
[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(${cx}, ${cy})
$sig = @'
[DllImport("user32.dll")] public static extern void mouse_event(int dwFlags, int dx, int dy, int dwData, int dwExtraInfo);
'@
$mouse = Add-Type -MemberDefinition $sig -Name 'MouseInput' -Namespace 'Win32' -PassThru
$mouse::mouse_event(0x0002, 0, 0, 0, 0)
$mouse::mouse_event(0x0004, 0, 0, 0, 0)`);
          break;
        }
        case 'type': {
          const escaped = (step.text || '').replace(/'/g, "''").replace(/[+^%~(){}[\]]/g, '{$&}');
          stepScripts.push(`[System.Windows.Forms.SendKeys]::SendWait('${escaped}')`);
          break;
        }
      }
    }

    const script = `
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Focus the target window first
$sig2 = @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
'@
Add-Type -MemberDefinition $sig2 -Name 'WinAPI' -Namespace 'Win32Focus' -ErrorAction SilentlyContinue

$proc = Get-Process -Id ${chain.pid} -ErrorAction SilentlyContinue
if ($proc -and $proc.MainWindowHandle) {
  if ([Win32Focus.WinAPI]::IsIconic($proc.MainWindowHandle)) {
    [Win32Focus.WinAPI]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
  }
  [Win32Focus.WinAPI]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 100
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$completed = 0
$totalSteps = ${chain.steps.length}
$error_msg = ''

try {
${stepScripts.map((s, i) => `  # Step ${i + 1}\n  ${s}\n  $completed = ${i + 1}`).join('\n')}
} catch {
  $error_msg = $_.Exception.Message -replace '[^\\x20-\\x7E]', ''
}

$sw.Stop()
@{
  success = ($completed -eq $totalSteps -and $error_msg -eq '')
  stepsCompleted = $completed
  totalSteps = $totalSteps
  durationMs = $sw.ElapsedMilliseconds
  error = $error_msg
} | ConvertTo-Json -Compress
`;

    try {
      const raw = runPSRawInteractive(script, 30000);
      return JSON.parse(raw);
    } catch (err) {
      return {
        success: false,
        stepsCompleted: 0,
        totalSteps: chain.steps.length,
        durationMs: 0,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  /** Map key names to PowerShell SendKeys format */
  private psKeyMap(key: string): string {
    const map: Record<string, string> = {
      'enter': '{ENTER}', 'return': '{ENTER}', 'tab': '{TAB}',
      'escape': '{ESC}', 'esc': '{ESC}', 'backspace': '{BS}', 'delete': '{DEL}',
      'up': '{UP}', 'down': '{DOWN}', 'left': '{LEFT}', 'right': '{RIGHT}',
      'home': '{HOME}', 'end': '{END}', 'pageup': '{PGUP}', 'pagedown': '{PGDN}',
      'f1': '{F1}', 'f2': '{F2}', 'f3': '{F3}', 'f4': '{F4}',
      'f5': '{F5}', 'f6': '{F6}', 'f7': '{F7}', 'f8': '{F8}',
      'f9': '{F9}', 'f10': '{F10}', 'f11': '{F11}', 'f12': '{F12}',
      'space': ' ', 'insert': '{INSERT}',
    };
    return map[key.toLowerCase()] || key;
  }

  // ─── Feature 5: Smart Element Resolution ───────────────────────

  /**
   * Find an element by name and invoke it using the BEST available method.
   * Tries in order:
   *   1. InvokePattern (standard UIA invoke)
   *   2. SetFocus → Enter key
   *   3. Find nearest invokable parent/sibling
   *   4. Calculate bounding rect center → click at coordinates
   *   5. ExpandCollapsePattern
   *   6. TogglePattern
   *
   * Last-resort activation path for visible controls after structured methods fail.
   */
  async smartInvoke(pid: number, name: string, options?: { parent?: string; type?: string; occurrence?: 'first' | 'last' | number }): Promise<SmartResolveResult> {
    this.ensureStarted();
    const { runPSRawInteractive } = await import('./ps-exec.js');

    const escapedName = name.replace(/'/g, "''");
    const escapedParent = (options?.parent || '').replace(/'/g, "''");
    const typeFilter = (options?.type || '').replace(/'/g, "''");
    const occurrence = options?.occurrence ?? 'last';

    const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$sig = @'
[DllImport("user32.dll")] public static extern void mouse_event(int dwFlags, int dx, int dy, int dwData, int dwExtraInfo);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
'@
Add-Type -MemberDefinition $sig -Name 'SmartInput' -Namespace 'Win32Smart' -ErrorAction SilentlyContinue

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if (-not $win) {
  @{ success = $false; error = 'Window not found'; method = '' } | ConvertTo-Json -Compress
  exit
}

# Find matching elements
$nameCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::NameProperty, '${escapedName}'
)
$allMatches = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $nameCond)

# Also try partial match if no exact match
if ($allMatches.Count -eq 0) {
  $allCond = [System.Windows.Automation.Condition]::TrueCondition
  $allElements = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $allCond)
  $partialMatches = @()
  foreach ($el in $allElements) {
    $eName = $el.Current.Name -replace '[^\\x20-\\x7E]', ''
    if ($eName -like "*${escapedName}*") { $partialMatches += $el }
  }
  if ($partialMatches.Count -gt 0) { $allMatches = $partialMatches }
}

if ($allMatches.Count -eq 0) {
  @{ success = $false; error = "No element named '${escapedName}' found"; method = '' } | ConvertTo-Json -Compress
  exit
}

# Apply parent filter
$matches = @()
if ('${escapedParent}') {
  $tw = [System.Windows.Automation.TreeWalker]::RawViewWalker
  foreach ($m in $allMatches) {
    $p = $tw.GetParent($m)
    $depth = 0
    while ($p -and $depth -lt 10) {
      $pName = $p.Current.Name -replace '[^\\x20-\\x7E]', ''
      if ($pName -like "*${escapedParent}*") { $matches += $m; break }
      $p = $tw.GetParent($p)
      $depth++
    }
  }
} else {
  foreach ($m in $allMatches) { $matches += $m }
}

# Apply type filter
if ('${typeFilter}' -and $matches.Count -gt 0) {
  $matches = @($matches | Where-Object {
    ($_.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '') -like "*${typeFilter}*"
  })
}

if ($matches.Count -eq 0) {
  @{ success = $false; error = "No matching element after filters"; method = '' } | ConvertTo-Json -Compress
  exit
}

# Select occurrence
$target = $null
$occ = '${occurrence}'
if ($occ -eq 'last') {
  $maxY = -999999
  foreach ($m in $matches) {
    $y = $m.Current.BoundingRectangle.Y
    if ($y -gt $maxY) { $maxY = $y; $target = $m }
  }
} elseif ($occ -eq 'first') {
  $minY = 999999
  foreach ($m in $matches) {
    $y = $m.Current.BoundingRectangle.Y
    if ($y -lt $minY) { $minY = $y; $target = $m }
  }
} else {
  $idx = [int]$occ
  if ($idx -lt $matches.Count) { $target = $matches[$idx] }
  else { $target = $matches[0] }
}

$rect = $target.Current.BoundingRectangle
$elInfo = @{
  name = ($target.Current.Name -replace '[^\\x20-\\x7E]', '')
  type = ($target.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '')
  x = if ($rect.X -gt -99999) { [int]$rect.X } else { 0 }
  y = if ($rect.Y -gt -99999) { [int]$rect.Y } else { 0 }
  w = if ($rect.Width -gt 0) { [int]$rect.Width } else { 0 }
  h = if ($rect.Height -gt 0) { [int]$rect.Height } else { 0 }
}

# METHOD 1: Try InvokePattern
try {
  $invokePattern = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
  $invokePattern.Invoke()
  @{ success = $true; method = 'invoke'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
  exit
} catch {}

# METHOD 2: Try SetFocus + Enter
try {
  $target.SetFocus()
  Start-Sleep -Milliseconds 100
  [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
  @{ success = $true; method = 'focus-enter'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
  exit
} catch {}

# METHOD 3: Try nearest invokable parent
try {
  $tw = [System.Windows.Automation.TreeWalker]::RawViewWalker
  $parentEl = $tw.GetParent($target)
  $depth = 0
  while ($parentEl -and $depth -lt 5) {
    try {
      $pInvoke = $parentEl.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
      $pInvoke.Invoke()
      $elInfo.method_parent = ($parentEl.Current.Name -replace '[^\\x20-\\x7E]', '')
      @{ success = $true; method = 'parent-invoke'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
      exit
    } catch {}
    $parentEl = $tw.GetParent($parentEl)
    $depth++
  }
} catch {}

# METHOD 4: Click at bounding rect center (the fallback that always works)
try {
  if ($rect.Width -gt 0 -and $rect.Height -gt 0 -and $rect.X -gt -99999) {
    $centerX = [int]($rect.X + $rect.Width / 2)
    $centerY = [int]($rect.Y + $rect.Height / 2)

    # Bring window to front
    $proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle) {
      [Win32Smart.SmartInput]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
    }
    Start-Sleep -Milliseconds 50

    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($centerX, $centerY)
    Start-Sleep -Milliseconds 30
    [Win32Smart.SmartInput]::mouse_event(0x0002, 0, 0, 0, 0)
    [Win32Smart.SmartInput]::mouse_event(0x0004, 0, 0, 0, 0)

    $elInfo.clickedAt = @{ x = $centerX; y = $centerY }
    @{ success = $true; method = 'click-coordinates'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
    exit
  }
} catch {}

# METHOD 5: Try ExpandCollapsePattern
try {
  $expandPattern = $target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  $expandPattern.Expand()
  @{ success = $true; method = 'expand'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
  exit
} catch {}

# METHOD 6: Try TogglePattern
try {
  $togglePattern = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
  $togglePattern.Toggle()
  @{ success = $true; method = 'toggle'; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
  exit
} catch {}

@{ success = $false; error = 'All invoke methods failed'; method = ''; element = $elInfo } | ConvertTo-Json -Compress -Depth 3
`;
    try {
      const raw = runPSRawInteractive(script, 20000);
      const data = JSON.parse(raw);
      return {
        success: data.success,
        method: data.method || 'unknown',
        element: {
          name: data.element?.name || name,
          type: data.element?.type || '',
          bounds: {
            x: data.element?.x || 0,
            y: data.element?.y || 0,
            width: data.element?.w || 0,
            height: data.element?.h || 0,
          },
        },
        error: data.error,
      };
    } catch (err) {
      return {
        success: false,
        method: 'invoke',
        element: { name, type: '', bounds: { x: 0, y: 0, width: 0, height: 0 } },
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ─── Helpers ────────────────────────────────────────────────────

  /** Count elements recursively. */
  countElements(elements: UIElement[]): number {
    let count = elements.length;
    for (const el of elements) count += this.countElements(el.children);
    return count;
  }

  /** Flatten UI tree for display. */
  flattenTree(elements: UIElement[], maxDepth = 3, depth = 0): Array<{ depth: number; id: string; type: string; label: string; actions: string[]; childCount: number }> {
    const flat: Array<{ depth: number; id: string; type: string; label: string; actions: string[]; childCount: number }> = [];
    if (depth > maxDepth) return flat;
    for (const el of elements) {
      flat.push({
        depth,
        id: el.id,
        type: el.type,
        label: el.label,
        actions: el.actions as string[],
        childCount: el.children.length,
      });
      flat.push(...this.flattenTree(el.children, maxDepth, depth + 1));
    }
    return flat;
  }
}
