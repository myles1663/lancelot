/**
 * UAB Service — Singleton service managing the Universal App Bridge lifecycle.
 *
 * Framework-agnostic: import this module from ClaudeClaw, Lancelot,
 * or any other AI agent runtime to get desktop app control.
 *
 * Phase 4 enhancements:
 *   - Connection Manager with health monitoring & auto-reconnect
 *   - Smart Element Cache with TTL & invalidation
 *   - Permission/Safety model for destructive actions
 *   - Retry with exponential backoff on transient errors
 *   - Action Chain executor for multi-step workflows
 *
 * Usage:
 *   import { uab } from './uab/service.js';
 *   await uab.start();                          // Initialize UAB
 *   const apps = await uab.detect();            // Scan for apps
 *   await uab.connect(apps[0]);                 // Connect to an app
 *   const buttons = await uab.query(pid, { type: 'button' });
 *   await uab.act(pid, buttons[0].id, 'click');
 *   await uab.stop();                           // Cleanup
 */

import { FrameworkDetector } from './detector.js';
import { PluginManager } from './plugins/base.js';
import { ElectronPlugin } from './plugins/electron/index.js';
import { WinUIAPlugin } from './plugins/win-uia/index.js';
import { QtPlugin } from './plugins/qt/index.js';
import { GtkPlugin } from './plugins/gtk/index.js';
import { JavaPlugin } from './plugins/java/index.js';
import { FlutterPlugin } from './plugins/flutter/index.js';
import { OfficePlugin } from './plugins/office/index.js';
import { ControlRouter, RoutedConnection } from './router.js';
import { ConnectionManager } from './connection-manager.js';
import { ElementCache } from './cache.js';
import { PermissionManager } from './permissions.js';
import { ChainExecutor, type ChainDefinition, type ChainResult } from './chains.js';
import { withRetry } from './retry.js';
import type {
  DetectedApp, UIElement, ElementSelector,
  ActionType, ActionParams, ActionResult, AppState,
} from './types.js';
import { createLogger } from './logger.js';

const log = createLogger('uab');

export class UABService {
  private detector: FrameworkDetector;
  private pluginManager: PluginManager;
  private router: ControlRouter;
  private _running = false;

  // Phase 4: Production hardening modules
  private connectionMgr: ConnectionManager;
  private cache: ElementCache;
  readonly permissions: PermissionManager;
  private chainExecutor: ChainExecutor;

  constructor() {
    this.detector = new FrameworkDetector();
    this.pluginManager = new PluginManager();
    this.router = new ControlRouter(this.pluginManager);

    // Phase 4 modules
    this.connectionMgr = new ConnectionManager(this.router);
    this.cache = new ElementCache();
    this.permissions = new PermissionManager();
    this.chainExecutor = new ChainExecutor(this);
  }

  get running(): boolean { return this._running; }

  /**
   * Initialize UAB — register all available plugins & start monitoring.
   */
  async start(): Promise<void> {
    if (this._running) return;

    // Ensure screenshots directory exists
    const fs = await import('fs');
    fs.mkdirSync('data/screenshots', { recursive: true });

    // Register framework plugins (priority order: specific -> generic)
    this.pluginManager.register(new ElectronPlugin());    // CDP -- best for Electron
    this.pluginManager.register(new OfficePlugin());      // Office (Word/Excel/PPT) + document content
    this.pluginManager.register(new QtPlugin());          // Qt via UIA
    this.pluginManager.register(new GtkPlugin());         // GTK via UIA
    this.pluginManager.register(new JavaPlugin());        // Java via JAB->UIA
    this.pluginManager.register(new FlutterPlugin());     // Flutter via UIA
    this.pluginManager.register(new WinUIAPlugin());      // Universal Windows fallback

    // Start connection health monitoring
    this.connectionMgr.startMonitoring();

    this._running = true;
    log.info('UAB service started', {
      frameworks: this.pluginManager.getRegisteredFrameworks(),
    });
  }

  /**
   * Stop UAB — disconnect all apps, stop monitoring, clean up.
   */
  async stop(): Promise<void> {
    if (!this._running) return;
    await this.connectionMgr.shutdown();
    await this.router.disconnectAll();
    this.cache.clear();
    this.permissions.clear();
    this._running = false;
    log.info('UAB service stopped');
  }

  // ─── Discovery ──────────────────────────────────────────────────

  /** Scan all running processes for controllable apps */
  async detect(): Promise<DetectedApp[]> {
    return this.detector.detectAll();
  }

  /** Quick-scan for Electron apps only */
  async detectElectron(): Promise<DetectedApp[]> {
    return this.detector.detectElectron();
  }

  /** Deep-inspect a specific PID */
  async detectByPid(pid: number): Promise<DetectedApp | null> {
    return this.detector.detectByPid(pid);
  }

  /** Find apps by name (fuzzy) */
  async findByName(name: string): Promise<DetectedApp[]> {
    return this.detector.findByName(name);
  }

  // ─── Connection ─────────────────────────────────────────────────

  /** Connect to an app — auto-selects the best control method */
  async connect(app: DetectedApp): Promise<{ method: string; pid: number }> {
    const conn = await withRetry(
      () => this.router.connect(app),
      { maxRetries: 1, label: `connect-${app.name}` },
    );
    this.connectionMgr.track(app.pid, app, conn);
    log.info('Connected to app', {
      name: app.name,
      pid: app.pid,
      framework: app.framework,
      method: conn.method,
    });
    return { method: conn.method, pid: app.pid };
  }

  /** Disconnect from an app */
  async disconnect(pid: number): Promise<void> {
    this.connectionMgr.untrack(pid, 'manual');
    this.cache.remove(pid);
    await this.router.disconnect(pid);
    log.info('Disconnected from app', { pid });
  }

  /** Disconnect all apps */
  async disconnectAll(): Promise<void> {
    for (const entry of this.connectionMgr.getAll()) {
      this.cache.remove(entry.pid);
      this.connectionMgr.untrack(entry.pid, 'disconnect-all');
    }
    await this.router.disconnectAll();
  }

  /** Check if connected to a PID */
  isConnected(pid: number): boolean {
    const route = this.router.getRoute(pid);
    return !!route && route.connection.connected;
  }

  /** Get all active connections */
  getConnections(): Array<{ pid: number; name: string; framework: string; method: string }> {
    return this.pluginManager.getActiveConnections()
      .filter(c => c.connected)
      .map(c => ({
        pid: c.pid,
        name: c.app.name,
        framework: c.app.framework,
        method: this.router.getRoute(c.pid)?.method || 'unknown',
      }));
  }

  // ─── Unified API (with cache + permissions + retry) ─────────────

  /** Get the full UI element tree for a connected app */
  async enumerate(pid: number): Promise<UIElement[]> {
    // Check cache first
    const cached = this.cache.getTree(pid);
    if (cached) return cached;

    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);

    const tree = await withRetry(
      () => route.connection.enumerate(),
      { maxRetries: 1, label: `enumerate-${pid}` },
    );

    this.cache.setTree(pid, tree);
    return tree;
  }

  /** Search for UI elements matching a selector */
  async query(pid: number, selector: ElementSelector): Promise<UIElement[]> {
    // Check cache first
    const cached = this.cache.getQuery(pid, selector);
    if (cached) return cached;

    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);

    const results = await withRetry(
      () => route.connection.query(selector),
      { maxRetries: 1, label: `query-${pid}` },
    );

    this.cache.setQuery(pid, selector, results);
    return results;
  }

  /** Perform an action on a UI element (with permission check + cache invalidation) */
  async act(pid: number, elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);

    // Permission check
    const check = this.permissions.check(pid, action, route.app);
    this.permissions.record(pid, action, elementId, route.app, check.allowed, check.reason);
    if (!check.allowed) {
      return { success: false, error: check.reason };
    }

    const result = await withRetry(
      () => route.connection.act(elementId, action, params),
      { maxRetries: 1, label: `act-${pid}-${action}` },
    );

    // Invalidate cache after mutating actions
    this.cache.invalidateIfNeeded(pid, action);

    log.debug('Action performed', { pid, elementId, action, success: result.success });
    return result;
  }

  /** Get current app state */
  async state(pid: number): Promise<AppState> {
    // Check cache first
    const cached = this.cache.getState(pid);
    if (cached) return cached as AppState;

    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);

    const appState = await withRetry(
      () => route.connection.state(),
      { maxRetries: 1, label: `state-${pid}` },
    );

    this.cache.setState(pid, appState);
    return appState;
  }

  // ─── Phase 3: Keyboard Input ────────────────────────────────────

  /** Send a single keypress to a connected app */
  async keypress(pid: number, key: string): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    const result = await route.connection.act('', 'keypress', { key });
    this.cache.invalidateIfNeeded(pid, 'keypress');
    return result;
  }

  /** Send a hotkey combination to a connected app (e.g., ['ctrl', 's']) */
  async hotkey(pid: number, keys: string[]): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    const result = await route.connection.act('', 'hotkey', { keys });
    this.cache.invalidateIfNeeded(pid, 'hotkey');
    return result;
  }

  // ─── Phase 3: Window Management ──────────────────────────────────

  /** Minimize a window */
  async minimize(pid: number): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'minimize');
  }

  /** Maximize a window */
  async maximize(pid: number): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'maximize');
  }

  /** Restore a window from min/max */
  async restore(pid: number): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'restore');
  }

  /** Close a window gracefully */
  async closeWindow(pid: number): Promise<ActionResult> {
    return this.act(pid, '', 'close'); // Goes through permission check
  }

  /** Move a window to (x, y) */
  async moveWindow(pid: number, x: number, y: number): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'move', { x, y });
  }

  /** Resize a window to (width, height) */
  async resizeWindow(pid: number, width: number, height: number): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'resize', { width, height });
  }

  // ─── Phase 3: Screenshot ──────────────────────────────────────

  /** Capture a screenshot of a connected app's window */
  async screenshot(pid: number, outputPath?: string): Promise<ActionResult> {
    const route = this.router.getRoute(pid);
    if (!route) throw new Error(`Not connected to PID ${pid}`);
    return route.connection.act('', 'screenshot', { outputPath });
  }

  // ─── Phase 4: Action Chains ───────────────────────────────────

  /** Execute a multi-step action chain */
  async executeChain(chain: ChainDefinition): Promise<ChainResult> {
    return this.chainExecutor.execute(chain);
  }

  // ─── Phase 4: Health & Diagnostics ─────────────────────────────

  /** Get connection health summary */
  getHealthSummary(): Array<{
    pid: number; name: string; healthy: boolean;
    uptimeMs: number; failures: number; method: string;
  }> {
    return this.connectionMgr.getHealthSummary();
  }

  /** Get cache statistics */
  getCacheStats() {
    return {
      ...this.cache.getStats(),
      hitRate: this.cache.getHitRate(),
    };
  }

  /** Get recent audit log */
  getAuditLog(limit = 50) {
    return this.permissions.getAuditLog(limit);
  }

  /** Trigger a manual health check on all connections */
  async checkHealth(): Promise<void> {
    await this.connectionMgr.runHealthChecks();
  }

  // ─── Convenience ────────────────────────────────────────────────

  /** Connect by name — finds the app and connects in one step */
  async connectByName(name: string): Promise<{ method: string; pid: number; app: DetectedApp }> {
    const matches = await this.findByName(name);
    if (matches.length === 0) throw new Error(`No app found matching "${name}"`);
    if (matches.length > 1) {
      const list = matches.map(m => `  PID ${m.pid}: ${m.name}`).join('\n');
      throw new Error(`Multiple apps match "${name}":\n${list}\nSpecify a PID instead.`);
    }
    const app = matches[0];
    const result = await this.connect(app);
    return { ...result, app };
  }

  /** Count all UI elements recursively */
  countElements(elements: UIElement[]): number {
    let count = elements.length;
    for (const el of elements) count += this.countElements(el.children);
    return count;
  }

  /** Flatten UI tree to a simple list (for display) */
  flattenTree(elements: UIElement[], maxDepth = 3, depth = 0): Array<{ depth: number; element: UIElement }> {
    const flat: Array<{ depth: number; element: UIElement }> = [];
    if (depth > maxDepth) return flat;
    for (const el of elements) {
      flat.push({ depth, element: el });
      flat.push(...this.flattenTree(el.children, maxDepth, depth + 1));
    }
    return flat;
  }
}

/** Singleton UAB service instance */
export const uab = new UABService();
