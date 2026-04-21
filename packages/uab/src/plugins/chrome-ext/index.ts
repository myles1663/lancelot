/**
 * Chrome Extension Bridge Plugin
 *
 * Connects to Chrome/Edge/Brave via a locally-installed extension
 * that communicates over WebSocket. NO browser relaunch required.
 *
 * Falls through to the CDP-based BrowserPlugin if the extension
 * is not connected, providing a graceful degradation path.
 *
 * Priority: ChromeExtPlugin > BrowserPlugin (CDP)
 */

import type {
  FrameworkPlugin, PluginConnection, DetectedApp,
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
  AppState, UABEventType, UABEventCallback, Subscription,
} from '../../types.js';
import { ExtensionWSServer } from './ws-server.js';
import { createLogger } from '../../logger.js';

const log = createLogger('chrome-ext-plugin');

const BROWSER_PROCESSES = new Set([
  'chrome.exe', 'msedge.exe', 'brave.exe',
  'chromium.exe', 'vivaldi.exe', 'opera.exe',
]);

export class ChromeExtPlugin implements FrameworkPlugin {
  readonly framework = 'browser' as const;
  readonly name = 'Chrome Extension Bridge';
  readonly controlMethod = 'chrome-extension' as const;

  private wsServer: ExtensionWSServer;
  private connections: Map<number, ChromeExtConnection> = new Map();

  constructor(wsServer: ExtensionWSServer) {
    this.wsServer = wsServer;
  }

  canHandle(app: DetectedApp): boolean {
    // Handle browsers ONLY when the extension is connected
    if (!this.wsServer.connected) return false;
    const procName = app.path?.split(/[\\/]/).pop()?.toLowerCase() || '';
    return BROWSER_PROCESSES.has(procName) || app.framework === 'browser';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const existing = this.connections.get(app.pid);
    if (existing?.connected) return existing;

    const conn = new ChromeExtConnection(app, this.wsServer);
    this.connections.set(app.pid, conn);
    log.info('Connected via extension bridge', { pid: app.pid, name: app.name });
    return conn;
  }
}

// ─── Connection Implementation ───────────────────────────────

class ChromeExtConnection implements PluginConnection {
  readonly app: DetectedApp;
  private _connected = true;
  private ws: ExtensionWSServer;
  private subscriptions: Map<string, { event: UABEventType; callback: UABEventCallback }> = new Map();

  constructor(app: DetectedApp, ws: ExtensionWSServer) {
    this.app = app;
    this.ws = ws;
  }

  get connected(): boolean {
    return this._connected && this.ws.connected;
  }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.ws.send('dom.enumerate', {
      maxDepth: 5,
    }) as UIElement[];
    return elements || [];
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.ws.send('dom.query', {
      selector: {
        type: selector.type,
        label: selector.label,
        labelExact: selector.labelExact,
        labelRegex: selector.labelRegex,
        visible: selector.visible,
      },
      limit: selector.limit || 50,
    }) as UIElement[];
    return elements || [];
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    // Route to the appropriate extension command based on action type
    switch (action) {
      // ─── Tab Management ──────────────────────────────
      case 'getTabs': {
        const tabs = await this.ws.send('tabs.list');
        return { success: true, result: tabs };
      }
      case 'newTab': {
        const tab = await this.ws.send('tabs.create', { url: params?.url });
        return { success: true, result: tab };
      }
      case 'closeTab': {
        await this.ws.send('tabs.close', { tabId: params?.tabId });
        return { success: true };
      }
      case 'switchTab': {
        await this.ws.send('tabs.activate', { tabId: params?.tabId });
        return { success: true };
      }

      // ─── Navigation ──────────────────────────────────
      case 'navigate': {
        await this.ws.send('nav.goto', { url: params?.url });
        return { success: true };
      }
      case 'goBack': {
        await this.ws.send('nav.back', {});
        return { success: true };
      }
      case 'goForward': {
        await this.ws.send('nav.forward', {});
        return { success: true };
      }
      case 'reload': {
        await this.ws.send('nav.reload', {});
        return { success: true };
      }

      // ─── Cookies ─────────────────────────────────────
      case 'getCookies': {
        const cookies = await this.ws.send('cookies.getAll', {
          domain: params?.domain,
          url: params?.url,
          name: params?.cookieName,
        });
        return { success: true, result: cookies };
      }
      case 'setCookie': {
        const result = await this.ws.send('cookies.set', {
          url: params?.url,
          name: params?.cookieName,
          value: params?.cookieValue,
          domain: params?.domain,
          path: params?.path,
          secure: params?.secure,
          httpOnly: params?.httpOnly,
          sameSite: params?.sameSite,
          expirationDate: params?.expires,
        }) as ActionResult;
        return result;
      }
      case 'deleteCookie': {
        const result = await this.ws.send('cookies.remove', {
          url: params?.url,
          name: params?.cookieName,
        }) as ActionResult;
        return result;
      }
      case 'clearCookies': {
        const result = await this.ws.send('cookies.clear', {
          domain: params?.domain,
        }) as ActionResult;
        return result;
      }

      // ─── Storage ─────────────────────────────────────
      case 'getLocalStorage': {
        const data = await this.ws.send('storage.get', {
          storageType: 'local',
          key: params?.storageKey,
        });
        return { success: true, result: data };
      }
      case 'setLocalStorage': {
        await this.ws.send('storage.set', {
          storageType: 'local',
          key: params?.storageKey,
          value: params?.storageValue,
        });
        return { success: true };
      }
      case 'deleteLocalStorage': {
        await this.ws.send('storage.remove', {
          storageType: 'local',
          key: params?.storageKey,
        });
        return { success: true };
      }
      case 'clearLocalStorage': {
        await this.ws.send('storage.clear', { storageType: 'local' });
        return { success: true };
      }
      case 'getSessionStorage': {
        const data = await this.ws.send('storage.get', {
          storageType: 'session',
          key: params?.storageKey,
        });
        return { success: true, result: data };
      }
      case 'setSessionStorage': {
        await this.ws.send('storage.set', {
          storageType: 'session',
          key: params?.storageKey,
          value: params?.storageValue,
        });
        return { success: true };
      }
      case 'deleteSessionStorage': {
        await this.ws.send('storage.remove', {
          storageType: 'session',
          key: params?.storageKey,
        });
        return { success: true };
      }
      case 'clearSessionStorage': {
        await this.ws.send('storage.clear', { storageType: 'session' });
        return { success: true };
      }

      // ─── JavaScript Execution ────────────────────────
      case 'executeScript': {
        const result = await this.ws.send('exec.run', {
          script: params?.script,
        });
        return { success: true, result };
      }

      // ─── Screenshot ──────────────────────────────────
      case 'screenshot': {
        const capture = await this.ws.send('capture.screenshot', {
          format: 'png',
        }) as { data: string; format: string };

        // Save to file if outputPath provided
        if (params?.outputPath && capture?.data) {
          const fs = await import('fs');
          const { dirname } = await import('path');
          fs.mkdirSync(dirname(params.outputPath), { recursive: true });
          fs.writeFileSync(params.outputPath, Buffer.from(capture.data, 'base64'));
          return { success: true, result: { path: params.outputPath } };
        }
        return { success: true, result: capture };
      }

      // ─── DOM Actions ─────────────────────────────────
      case 'click':
      case 'doubleclick':
      case 'rightclick': {
        const result = await this.ws.send(`dom.${action}`, {
          elementId,
        }) as ActionResult;
        return result;
      }
      case 'type': {
        const result = await this.ws.send('dom.type', {
          elementId,
          text: params?.text,
        }) as ActionResult;
        return result;
      }
      case 'clear': {
        const result = await this.ws.send('dom.clear', {
          elementId,
        }) as ActionResult;
        return result;
      }
      case 'select': {
        const result = await this.ws.send('dom.select', {
          elementId,
          value: params?.value,
        }) as ActionResult;
        return result;
      }
      case 'focus': {
        const result = await this.ws.send('dom.focus', {
          elementId,
        }) as ActionResult;
        return result;
      }
      case 'hover': {
        const result = await this.ws.send('dom.hover', {
          elementId,
        }) as ActionResult;
        return result;
      }
      case 'scroll': {
        const result = await this.ws.send('dom.scroll', {
          elementId,
          direction: params?.direction,
          amount: params?.amount,
        }) as ActionResult;
        return result;
      }
      case 'check':
      case 'uncheck':
      case 'toggle': {
        const result = await this.ws.send(`dom.${action}`, {
          elementId,
        }) as ActionResult;
        return result;
      }

      // ─── Keyboard ────────────────────────────────────
      case 'keypress': {
        // Use DOM-level keyboard event injection
        const result = await this.ws.send('exec.run', {
          script: `document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: '${params?.key}', bubbles: true })); document.activeElement.dispatchEvent(new KeyboardEvent('keyup', { key: '${params?.key}', bubbles: true }));`,
        });
        return { success: true, result };
      }
      case 'hotkey': {
        const keys = params?.keys || [];
        const mods = { ctrlKey: false, shiftKey: false, altKey: false, metaKey: false };
        let mainKey = '';
        for (const k of keys) {
          const lower = k.toLowerCase();
          if (lower === 'ctrl' || lower === 'control') mods.ctrlKey = true;
          else if (lower === 'shift') mods.shiftKey = true;
          else if (lower === 'alt') mods.altKey = true;
          else if (lower === 'meta' || lower === 'win') mods.metaKey = true;
          else mainKey = k;
        }
        const result = await this.ws.send('exec.run', {
          script: `document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: '${mainKey}', ${Object.entries(mods).filter(([, v]) => v).map(([k]) => `${k}: true`).join(', ')}, bubbles: true }))`,
        });
        return { success: true, result };
      }

      // ─── Window Management (pass-through, these are OS-level) ──
      case 'minimize':
      case 'maximize':
      case 'restore':
      case 'close':
      case 'move':
      case 'resize': {
        // Window management is OS-level, not browser-extension level.
        // Fall through — the router can try WinUIA for these.
        return { success: false, error: `Window action "${action}" not available via extension. Use OS-level control.` };
      }

      default:
        return { success: false, error: `Unsupported action: ${action}` };
    }
  }

  async state(): Promise<AppState> {
    // Get active tab info for the app state
    const tabs = await this.ws.send('tabs.list') as Array<{
      id: string; title: string; url: string; active: boolean;
    }>;
    const activeTab = tabs?.find(t => t.active);

    return {
      window: {
        title: activeTab?.title || 'Browser',
        size: { width: 0, height: 0 },
        position: { x: 0, y: 0 },
        focused: true,
      },
      activeElement: undefined,
      modals: [],
      menus: [],
    };
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    const id = `sub-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
    this.subscriptions.set(id, { event, callback });
    return {
      id,
      event,
      unsubscribe: () => { this.subscriptions.delete(id); },
    };
  }

  async disconnect(): Promise<void> {
    this._connected = false;
    this.subscriptions.clear();
    log.info('Extension connection released', { pid: this.app.pid });
  }
}
