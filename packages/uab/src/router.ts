/**
 * Control Router
 *
 * Selects the best available control method for each app:
 *   Priority 1: Direct API / MCP Server (if available)
 *   Priority 2: UAB Framework Hook (this project)
 *   Priority 3: Accessibility API (OS-native)
 *   Priority 4: Vision + Input Injection (universal fallback)
 */

import type {
  DetectedApp, PluginConnection, ControlMethod, ControlRoute,
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
  AppState, UABEventType, UABEventCallback, Subscription,
} from './types.js';
import { PluginManager } from './plugins/base.js';
import { WinUIAPlugin } from './plugins/win-uia/index.js';

export class ControlRouter {
  private pluginManager: PluginManager;
  private routes: Map<number, ControlRoute> = new Map();

  constructor(pluginManager: PluginManager) {
    this.pluginManager = pluginManager;
  }

  async connect(app: DetectedApp): Promise<RoutedConnection> {
    const methods = this.getAvailableMethods(app);
    let lastError: Error | null = null;

    for (const method of methods) {
      try {
        const connection = await this.tryMethod(app, method);
        if (connection) {
          const route: ControlRoute = {
            app, method, connection,
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

  getRoute(pid: number): ControlRoute | undefined {
    return this.routes.get(pid);
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
            app: route.app, method, connection,
            fallbacks: route.fallbacks.filter(m => m !== method),
          };
          this.routes.set(pid, newRoute);
          return new RoutedConnection(newRoute, this);
        }
      } catch { /* continue */ }
    }

    this.routes.delete(pid);
    return null;
  }

  private getAvailableMethods(app: DetectedApp): ControlMethod[] {
    const methods: ControlMethod[] = [];
    if (this.pluginManager.hasPlugin(app.framework)) {
      methods.push('uab-hook');
    }
    methods.push('accessibility');
    return methods;
  }

  private uiaFallback = new WinUIAPlugin();

  private async tryMethod(app: DetectedApp, method: ControlMethod): Promise<PluginConnection | null> {
    switch (method) {
      case 'uab-hook':
        return this.pluginManager.connect(app);
      case 'accessibility':
        // Use Windows UI Automation as the accessibility fallback
        if (this.uiaFallback.canHandle(app)) {
          return this.uiaFallback.connect(app);
        }
        throw new Error('Accessibility API fallback not available for this app');
      case 'vision':
        throw new Error('Vision fallback not yet implemented');
      case 'direct-api':
        throw new Error('Direct API method not yet implemented');
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
      // If the action failed and there are fallbacks, try the next method
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
