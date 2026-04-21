/**
 * Control Router
 *
 * Selects the best available control method for each app:
 *   Priority 1: Direct API / MCP Server (if available)
 *   Priority 2: Framework-specific UAB hook
 *   Priority 3: WinUIA accessibility fallback
 *   Priority 4: Vision + input injection fallback
 */

import type {
  DetectedApp, PluginConnection, ControlMethod, ControlRoute,
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
  AppState, UABEventType, UABEventCallback, Subscription,
} from './types.js';
import { PluginManager } from './plugins/base.js';
import { WinUIAPlugin } from './plugins/win-uia/index.js';
import { VisionPlugin } from './plugins/vision/index.js';
import { createLogger } from './logger.js';

const log = createLogger('router');

export class ControlRouter {
  private pluginManager: PluginManager;
  private routes: Map<number, ControlRoute> = new Map();
  private uiaFallback = new WinUIAPlugin();
  private visionFallback = new VisionPlugin();

  constructor(pluginManager: PluginManager) {
    this.pluginManager = pluginManager;
  }

  async connect(app: DetectedApp): Promise<RoutedConnection> {
    const methods = this.describeAvailableMethods(app);
    let lastError: Error | null = null;

    for (const method of methods) {
      try {
        const connection = await this.tryMethod(app, method);
        if (connection) {
          const route: ControlRoute = {
            app,
            method,
            connection,
            fallbacks: methods.filter(m => m !== method),
          };
          this.routes.set(app.pid, route);
          return new RoutedConnection(route, this);
        }
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
      }
    }

    throw new Error(
      `Cannot connect to ${app.name} (PID: ${app.pid}). ` +
      `Tried methods: ${methods.join(', ')}. ` +
      `Last error: ${lastError?.message || 'unknown'}`
    );
  }

  describeAvailableMethods(app: DetectedApp): ControlMethod[] {
    const methods: ControlMethod[] = [];

    for (const plugin of this.pluginManager.getCandidatePlugins(app)) {
      if (!methods.includes(plugin.controlMethod)) {
        methods.push(plugin.controlMethod);
      }
    }

    if (!methods.includes(this.uiaFallback.controlMethod) && this.uiaFallback.canHandle(app)) {
      methods.push(this.uiaFallback.controlMethod);
    }

    if (!methods.includes(this.visionFallback.controlMethod) && this.visionFallback.canHandle(app)) {
      methods.push(this.visionFallback.controlMethod);
    }

    return methods;
  }

  getRoute(pid: number): ControlRoute | undefined {
    return this.routes.get(pid);
  }

  getConnection(pid: number): RoutedConnection | undefined {
    const route = this.routes.get(pid);
    if (!route) {
      return undefined;
    }
    return new RoutedConnection(route, this);
  }

  async disconnect(pid: number): Promise<void> {
    const route = this.routes.get(pid);
    if (route) {
      await route.connection.disconnect();
      this.routes.delete(pid);
    }
  }

  async disconnectAll(): Promise<void> {
    for (const [pid] of this.routes) {
      await this.disconnect(pid);
    }
  }

  async fallback(pid: number): Promise<RoutedConnection | null> {
    const route = this.routes.get(pid);
    if (!route || route.fallbacks.length === 0) return null;

    try { await route.connection.disconnect(); } catch { /* best effort */ }

    for (const method of route.fallbacks) {
      try {
        const connection = await this.tryMethod(route.app, method);
        if (connection) {
          const newRoute: ControlRoute = {
            app: route.app,
            method,
            connection,
            fallbacks: route.fallbacks.filter(m => m !== method),
          };
          this.routes.set(pid, newRoute);
          return new RoutedConnection(newRoute, this);
        }
      } catch (err) {
        log.debug('UAB route fallback failed', {
          pid,
          app: route.app.name,
          method,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }

    this.routes.delete(pid);
    return null;
  }

  private async tryMethod(app: DetectedApp, method: ControlMethod): Promise<PluginConnection | null> {
    switch (method) {
      case 'direct-api':
      case 'chrome-extension':
      case 'browser-cdp':
      case 'electron-cdp':
      case 'office-com+uia':
      case 'qt-uia':
      case 'gtk-uia':
      case 'java-jab-uia':
      case 'flutter-uia': {
        const plugin = this.pluginManager.findPluginByMethod(app, method);
        if (!plugin) {
          throw new Error(`Framework hook ${method} is not available for ${app.name}`);
        }
        return plugin.connect(app);
      }
      case 'win-uia':
        if (this.uiaFallback.canHandle(app)) {
          return this.uiaFallback.connect(app);
        }
        throw new Error('WinUIA fallback not available for this app');
      case 'vision':
        if (this.visionFallback.canHandle(app)) {
          return this.visionFallback.connect(app);
        }
        throw new Error('Vision fallback requires ANTHROPIC_API_KEY');
      default:
        throw new Error(`Unknown control method: ${method}`);
    }
  }
}

export class RoutedConnection implements PluginConnection {
  private route: ControlRoute;
  private router: ControlRouter;

  constructor(route: ControlRoute, router: ControlRouter) {
    this.route = route;
    this.router = router;
  }

  get app(): DetectedApp { return this.route.app; }
  get connected(): boolean { return this.route.connection.connected; }
  get method(): ControlMethod { return this.route.method; }

  async enumerate(): Promise<UIElement[]> {
    return this.withFallback(() => this.route.connection.enumerate());
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    return this.withFallback(() => this.route.connection.query(selector));
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    return this.withActionFallback(elementId, action, params);
  }

  async state(): Promise<AppState> {
    return this.withFallback(() => this.route.connection.state());
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    return this.route.connection.subscribe(event, callback);
  }

  async disconnect(): Promise<void> {
    return this.router.disconnect(this.route.app.pid);
  }

  private async withActionFallback(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    try {
      const result = await this.route.connection.act(elementId, action, params);
      if (!result.success && result.error && this.route.fallbacks.length > 0) {
        const fallbackConn = await this.router.fallback(this.route.app.pid);
        if (fallbackConn) {
          const newRoute = this.router.getRoute(this.route.app.pid);
          if (newRoute) this.route = newRoute;
          return this.route.connection.act(elementId, action, params);
        }
      }
      return result;
    } catch (err) {
      const fallbackConn = await this.router.fallback(this.route.app.pid);
      if (fallbackConn) {
        const newRoute = this.router.getRoute(this.route.app.pid);
        if (newRoute) this.route = newRoute;
        return this.route.connection.act(elementId, action, params);
      }
      throw err;
    }
  }

  private async withFallback<T>(op: () => Promise<T>): Promise<T> {
    try {
      return await op();
    } catch (err) {
      const fallbackConn = await this.router.fallback(this.route.app.pid);
      if (fallbackConn) {
        const newRoute = this.router.getRoute(this.route.app.pid);
        if (newRoute) this.route = newRoute;
        return op();
      }
      throw err;
    }
  }
}
