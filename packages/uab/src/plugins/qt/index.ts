/**
 * Qt5/Qt6 Framework Plugin
 *
 * On Windows, Qt apps expose their UI through the Windows Accessibility
 * framework (QAccessible → MSAA/UIA bridge). This plugin detects Qt apps
 * by their loaded DLLs and connects via the Win UIA plugin.
 *
 * For extra control beyond what UIA provides, we can also inject commands
 * via Qt's own IPC mechanisms:
 *   - Qt5: Uses QDBus on Linux, QLocalServer on Windows
 *   - Qt6: Same + additional QML debugging via QML inspector
 *
 * Detection signals:
 *   - Qt5Core.dll / Qt6Core.dll loaded
 *   - Qt-specific window class names (e.g., "Qt5QWindowIcon", "Qt6QWindowIcon")
 *   - qApp command line flags
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
import { runPSJson } from '../../ps-exec.js';
import { WinUIAPlugin } from '../win-uia/index.js';

// ─── Qt Window Class Detection ──────────────────────────────

const QT_WINDOW_CLASSES = [
  'Qt5QWindowIcon', 'Qt6QWindowIcon',
  'Qt5QWindowToolTipIcon', 'Qt6QWindowToolTipIcon',
  'Qt5QWindowPopupDropDownMenuIcon', 'Qt6QWindowPopupDropDownMenuIcon',
  'Qt5QWindowOwnDC', 'Qt6QWindowOwnDC',
];

/**
 * Check if a process has Qt-specific window classes.
 */
function hasQtWindowClass(pid: number): { isQt: boolean; version?: 5 | 6 } {
  try {
    const script = `
$windows = Get-Process -Id ${pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty MainWindowHandle
if ($windows) {
  Add-Type @'
    using System;
    using System.Runtime.InteropServices;
    using System.Text;
    public class WinAPI {
      [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
      public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);
    }
'@
  $sb = New-Object System.Text.StringBuilder(256)
  [WinAPI]::GetClassName($windows, $sb, 256) | Out-Null
  $className = $sb.ToString()
  @{ className = $className } | ConvertTo-Json -Compress
} else {
  @{ className = '' } | ConvertTo-Json -Compress
}
`;
    const parsed = runPSJson(script, 5000) as { className: string };
    const cls = parsed.className;

    if (cls.startsWith('Qt6')) return { isQt: true, version: 6 };
    if (cls.startsWith('Qt5')) return { isQt: true, version: 5 };
    return { isQt: false };
  } catch {
    return { isQt: false };
  }
}

// ─── Qt Plugin ──────────────────────────────────────────────

export class QtPlugin implements FrameworkPlugin {
  readonly framework = 'qt6' as const;
  readonly name = 'Qt (UIA Bridge)';
  private uiaPlugin = new WinUIAPlugin();

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'qt5' || app.framework === 'qt6';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    // Qt apps expose accessibility via MSAA/UIA bridge
    // Use the Win UIA plugin for actual interaction
    const connection = await this.uiaPlugin.connect(app);
    return new QtConnection(app, connection);
  }
}

/**
 * Qt connection wraps the UIA connection with Qt-specific enhancements.
 */
class QtConnection implements PluginConnection {
  readonly app: DetectedApp;
  private uiaConn: PluginConnection;

  constructor(app: DetectedApp, uiaConn: PluginConnection) {
    this.app = app;
    this.uiaConn = uiaConn;
  }

  get connected(): boolean { return this.uiaConn.connected; }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.uiaConn.enumerate();
    // Tag elements with Qt metadata
    return elements.map(el => this.tagQt(el));
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.uiaConn.query(selector);
    return elements.map(el => this.tagQt(el));
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

  private tagQt(el: UIElement): UIElement {
    return {
      ...el,
      meta: { ...el.meta, pluginSource: 'qt', qtVersion: this.app.framework === 'qt6' ? 6 : 5 },
      children: el.children.map(c => this.tagQt(c)),
    };
  }
}

export default QtPlugin;
