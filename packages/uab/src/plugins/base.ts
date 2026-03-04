/**
 * Base Plugin & Plugin Manager
 *
 * Manages framework plugin registration and routing.
 * Supports multiple plugins and selects the best one via canHandle().
 */

import type { DetectedApp, FrameworkPlugin, FrameworkType, PluginConnection } from '../types.js';

export class PluginManager {
  private plugins: FrameworkPlugin[] = [];
  private connections: Map<number, PluginConnection> = new Map();

  /**
   * Register a framework plugin. Plugins are tried in registration order,
   * so register specific plugins before generic fallbacks.
   */
  register(plugin: FrameworkPlugin): void {
    this.plugins.push(plugin);
  }

  /**
   * Get all registered framework types (unique).
   */
  getRegisteredFrameworks(): FrameworkType[] {
    const types = new Set(this.plugins.map(p => p.framework));
    return Array.from(types);
  }

  /**
   * Check if any plugin can handle the given framework.
   */
  hasPlugin(framework: FrameworkType): boolean {
    return this.plugins.some(p => p.framework === framework || p.canHandle({ framework } as DetectedApp));
  }

  /**
   * Find the best plugin for an app by trying each in order.
   */
  findPlugin(app: DetectedApp): FrameworkPlugin | null {
    // First try exact framework match
    for (const plugin of this.plugins) {
      if (plugin.framework === app.framework && plugin.canHandle(app)) {
        return plugin;
      }
    }
    // Then try any plugin that declares it can handle this app
    for (const plugin of this.plugins) {
      if (plugin.canHandle(app)) {
        return plugin;
      }
    }
    return null;
  }

  /**
   * Connect to an app using the best available plugin.
   */
  async connect(app: DetectedApp): Promise<PluginConnection> {
    const existing = this.connections.get(app.pid);
    if (existing?.connected) return existing;

    const plugin = this.findPlugin(app);
    if (!plugin) {
      throw new Error(`No plugin can handle app: ${app.name} (${app.framework})`);
    }

    const connection = await plugin.connect(app);
    this.connections.set(app.pid, connection);
    return connection;
  }

  getConnection(pid: number): PluginConnection | undefined {
    const conn = this.connections.get(pid);
    return conn?.connected ? conn : undefined;
  }

  async disconnect(pid: number): Promise<void> {
    const conn = this.connections.get(pid);
    if (conn) {
      await conn.disconnect();
      this.connections.delete(pid);
    }
  }

  async disconnectAll(): Promise<void> {
    for (const [, conn] of this.connections) {
      try { await conn.disconnect(); } catch { /* best effort */ }
    }
    this.connections.clear();
  }

  getActiveConnections(): Array<{ pid: number; app: DetectedApp; connected: boolean }> {
    return Array.from(this.connections.entries()).map(([pid, conn]) => ({
      pid,
      app: conn.app,
      connected: conn.connected,
    }));
  }
}
