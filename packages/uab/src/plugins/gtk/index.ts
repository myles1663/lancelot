/**
 * GTK3/GTK4 Framework Plugin
 *
 * On Windows, GTK apps can expose accessibility via:
 *   - ATK → MSAA/UIA bridge (GTK3 with at-spi2-atk)
 *   - GTK4's built-in accessibility (maps to platform A11y APIs)
 *   - Fallback: Windows UI Automation (partial coverage)
 *
 * Detection signals:
 *   - libgtk-3-0.dll / libgtk-4-1.dll loaded
 *   - GdkWindow class names
 *   - GIMP, Inkscape, GNOME apps on Windows use GTK
 *
 * On Linux: Would use AT-SPI2 D-Bus interface for direct access.
 * On Windows: Leverages the Win UIA plugin through ATK→MSAA bridge.
 */

import {
  FrameworkPlugin,
  PluginConnection,
  DetectedApp,
  UIElement,
  ElementSelector,
  ActionType,
  ActionParams,
  ActionResult,
  AppState,
  UABEventType,
  UABEventCallback,
  Subscription,
} from '../../types.js';
import { WinUIAPlugin } from '../win-uia/index.js';

// ─── GTK Plugin ─────────────────────────────────────────────

export class GtkPlugin implements FrameworkPlugin {
  readonly framework = 'gtk4' as const;
  readonly name = 'GTK (UIA Bridge)';
  private uiaPlugin = new WinUIAPlugin();

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'gtk3' || app.framework === 'gtk4';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    // GTK on Windows exposes accessibility via ATK → MSAA/UIA bridge
    const connection = await this.uiaPlugin.connect(app);
    return new GtkConnection(app, connection);
  }
}

/**
 * GTK connection wraps UIA with GTK-specific metadata.
 */
class GtkConnection implements PluginConnection {
  readonly app: DetectedApp;
  private uiaConn: PluginConnection;

  constructor(app: DetectedApp, uiaConn: PluginConnection) {
    this.app = app;
    this.uiaConn = uiaConn;
  }

  get connected(): boolean { return this.uiaConn.connected; }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.uiaConn.enumerate();
    return elements.map(el => this.tagGtk(el));
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.uiaConn.query(selector);
    return elements.map(el => this.tagGtk(el));
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    return this.uiaConn.act(elementId, action, params);
  }

  async state(): Promise<AppState> {
    return this.uiaConn.state();
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    return this.uiaConn.subscribe(event, callback);
  }

  async disconnect(): Promise<void> {
    return this.uiaConn.disconnect();
  }

  private tagGtk(el: UIElement): UIElement {
    return {
      ...el,
      meta: { ...el.meta, pluginSource: 'gtk', gtkVersion: this.app.framework === 'gtk4' ? 4 : 3 },
      children: el.children.map(c => this.tagGtk(c)),
    };
  }
}

export default GtkPlugin;
