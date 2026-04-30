/**
 * Flutter Framework Plugin
 *
 * Flutter apps on Windows use the Flutter Embedder which creates
 * a custom rendering surface. Flutter 3.x+ includes accessibility
 * support that bridges to Windows UIA via SemanticsNode → UIA mapping.
 *
 * Detection signals:
 *   - flutter_windows.dll / flutter_engine.dll loaded
 *   - FlutterDesktopView window class
 *
 * For enhanced control, Flutter apps can be connected via the
 * Dart VM Service Protocol (--observatory-port) for deep inspection,
 * but the UIA bridge provides good coverage for standard interactions.
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

export class FlutterPlugin implements FrameworkPlugin {
  readonly framework = 'flutter' as const;
  readonly name = 'Flutter (UIA Bridge)';
  readonly controlMethod = 'flutter-uia' as const;
  private uiaPlugin = new WinUIAPlugin();

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'flutter';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const connection = await this.uiaPlugin.connect(app);
    return new FlutterConnection(app, connection);
  }
}

class FlutterConnection implements PluginConnection {
  readonly app: DetectedApp;
  private uiaConn: PluginConnection;

  constructor(app: DetectedApp, uiaConn: PluginConnection) {
    this.app = app;
    this.uiaConn = uiaConn;
  }

  get connected(): boolean { return this.uiaConn.connected; }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.uiaConn.enumerate();
    return elements.map(el => this.tagFlutter(el));
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.uiaConn.query(selector);
    return elements.map(el => this.tagFlutter(el));
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

  private tagFlutter(el: UIElement): UIElement {
    return {
      ...el,
      meta: { ...el.meta, pluginSource: 'flutter' },
      children: el.children.map(c => this.tagFlutter(c)),
    };
  }
}

export default FlutterPlugin;
