/**
 * Java Swing / JavaFX Framework Plugin
 *
 * Java apps expose accessibility via the Java Access Bridge (JAB).
 * On Windows, JAB bridges to MSAA/UIA, so we route through Win UIA.
 *
 * Detection signals:
 *   - jvm.dll loaded
 *   - java.exe / javaw.exe process name
 *   - -jar flag in command line
 *   - WindowsForms class name containing "SunAwtFrame" (Swing) or "Glass" (JavaFX)
 *
 * Note: Java Access Bridge must be enabled on the system:
 *   Control Panel → Ease of Access → Java Access Bridge
 *   Or: jabswitch.exe -enable
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

export class JavaPlugin implements FrameworkPlugin {
  readonly framework = 'java-swing' as const;
  readonly name = 'Java (UIA via JAB)';
  readonly controlMethod = 'java-jab-uia' as const;
  private uiaPlugin = new WinUIAPlugin();

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'java-swing' || app.framework === 'javafx';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const connection = await this.uiaPlugin.connect(app);
    return new JavaConnection(app, connection);
  }
}

class JavaConnection implements PluginConnection {
  readonly app: DetectedApp;
  private uiaConn: PluginConnection;

  constructor(app: DetectedApp, uiaConn: PluginConnection) {
    this.app = app;
    this.uiaConn = uiaConn;
  }

  get connected(): boolean { return this.uiaConn.connected; }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.uiaConn.enumerate();
    return elements.map(el => this.tagJava(el));
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.uiaConn.query(selector);
    return elements.map(el => this.tagJava(el));
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

  private tagJava(el: UIElement): UIElement {
    return {
      ...el,
      meta: { ...el.meta, pluginSource: 'java', javaFramework: this.app.framework },
      children: el.children.map(c => this.tagJava(c)),
    };
  }
}

export default JavaPlugin;
