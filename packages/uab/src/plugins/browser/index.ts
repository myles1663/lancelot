/**
 * Browser Framework Plugin
 *
 * Connects to the user's REAL running browser (Chrome, Edge, Brave)
 * via Chrome DevTools Protocol (CDP) for full control of:
 *   - Cookies (CRUD)
 *   - localStorage / sessionStorage
 *   - Tab management (list/switch/close/new)
 *   - Navigation (goto/back/forward/reload)
 *   - DOM interaction (click, type, query — inherited from Electron plugin)
 *   - JavaScript execution
 *   - Screenshots
 *
 * IMPORTANT: The browser must be launched with --remote-debugging-port=PORT
 * or have debugging enabled. This plugin auto-discovers the debug port.
 */

import type {
  FrameworkPlugin, PluginConnection, DetectedApp,
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
  AppState, UABEventType, UABEventCallback, Subscription,
} from '../../types.js';
import { CDPConnection, type CDPTarget } from '../electron/cdp.js';
import { DOMMapper } from '../electron/mapper.js';
import { randomUUID } from 'crypto';
import { writeFileSync, mkdirSync } from 'fs';
import { dirname } from 'path';
import { execSync, spawn } from 'child_process';
import { runPSJsonInteractive, runPSRaw } from '../../ps-exec.js';
import { createLogger } from '../../logger.js';

const log = createLogger('browser-plugin');

// ─── Browser process names we detect ──────────────────────────

const BROWSER_PROCESSES = new Set([
  'chrome.exe', 'msedge.exe', 'brave.exe',
  'chromium.exe', 'vivaldi.exe', 'opera.exe',
]);

// Display names for browsers
const BROWSER_NAMES: Record<string, string> = {
  'chrome.exe': 'Google Chrome',
  'msedge.exe': 'Microsoft Edge',
  'brave.exe': 'Brave Browser',
  'chromium.exe': 'Chromium',
  'vivaldi.exe': 'Vivaldi',
  'opera.exe': 'Opera',
};

// Default user data directories per browser (Windows)
const DEFAULT_USER_DATA_DIRS: Record<string, string> = {
  'chrome.exe': '%LOCALAPPDATA%\\Google\\Chrome\\User Data',
  'msedge.exe': '%LOCALAPPDATA%\\Microsoft\\Edge\\User Data',
  'brave.exe': '%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data',
  'chromium.exe': '%LOCALAPPDATA%\\Chromium\\User Data',
  'vivaldi.exe': '%LOCALAPPDATA%\\Vivaldi\\User Data',
  'opera.exe': '%APPDATA%\\Opera Software\\Opera Stable',
};

// Per-browser default CDP ports to avoid conflicts when multiple browsers run
const BROWSER_DEBUG_PORTS: Record<string, number> = {
  'chrome.exe': 9222,
  'msedge.exe': 9223,
  'brave.exe': 9224,
  'chromium.exe': 9225,
  'vivaldi.exe': 9226,
  'opera.exe': 9227,
};

function getDefaultDebugPort(processNameOrPath: string): number {
  const name = (processNameOrPath.split(/[\\/]/).pop() || '').toLowerCase();
  return BROWSER_DEBUG_PORTS[name] || 9222;
}

/**
 * Get the user-data-dir from a running browser's command line, or fall back to the default.
 */
function getUserDataDir(processName: string, pid: number): string {
  try {
    const cmd = `wmic process where "ProcessId=${pid}" get CommandLine /format:value`;
    const output = execSync(cmd, { encoding: 'utf-8', timeout: 5000 });
    // Chrome uses --user-data-dir="path" or --user-data-dir=path
    const match = output.match(/--user-data-dir=("([^"]+)"|(\S+))/i);
    if (match) return match[2] || match[3];
  } catch { /* fall through to default */ }

  const template = DEFAULT_USER_DATA_DIRS[processName.toLowerCase()];
  if (template) {
    // Expand environment variables
    return template.replace(/%([^%]+)%/g, (_, envVar) => process.env[envVar] || '');
  }
  return '';
}

/**
 * Get the profile directory from command line (--profile-directory).
 */
function getProfileDir(pid: number): string {
  try {
    const cmd = `wmic process where "ProcessId=${pid}" get CommandLine /format:value`;
    const output = execSync(cmd, { encoding: 'utf-8', timeout: 5000 });
    const match = output.match(/--profile-directory=("([^"]+)"|(\S+))/i);
    if (match) return match[2] || match[3];
  } catch { /* fall through */ }
  return '';
}

/**
 * Gracefully close ALL instances of a browser and wait for exit.
 * Browsers use multi-process architecture, so killing by image name
 * ensures the profile directory is fully released for relaunch.
 */
async function closeBrowserGracefully(pid: number, processName: string): Promise<void> {
  log.info('Closing all browser instances for relaunch', { pid, processName });

  // Kill all instances of this browser by image name (sends WM_CLOSE)
  try {
    execSync(`taskkill /IM ${processName}`, { encoding: 'utf-8', timeout: 5000, stdio: 'ignore' });
  } catch { /* might already be closed */ }

  // Wait for all processes of this browser to exit (up to 8 seconds)
  for (let i = 0; i < 16; i++) {
    await new Promise(r => setTimeout(r, 500));
    try {
      const check = execSync(
        `tasklist /FI "IMAGENAME eq ${processName}" /NH`,
        { encoding: 'utf-8', timeout: 3000 }
      );
      if (!check.toLowerCase().includes(processName.toLowerCase())) {
        log.info('Browser closed gracefully', { processName });
        return;
      }
    } catch { /* tasklist failed, assume closed */ return; }
  }

  // Force kill if still running after 8s
  try {
    execSync(`taskkill /F /IM ${processName}`, { encoding: 'utf-8', timeout: 5000, stdio: 'ignore' });
    log.warn('Browser force-killed after graceful close timeout', { processName });
  } catch { /* already dead */ }

  // Brief pause after force kill
  await new Promise(r => setTimeout(r, 500));
}

/**
 * Relaunch a browser with --remote-debugging-port, preserving the user's profile.
 * Returns the new PID.
 */
async function relaunchWithDebugPort(
  exePath: string, processName: string, userDataDir: string,
  profileDir: string, port: number
): Promise<number> {
  const args = [`--remote-debugging-port=${port}`];

  if (userDataDir) {
    args.push(`--user-data-dir=${userDataDir}`);
  }
  if (profileDir) {
    args.push(`--profile-directory=${profileDir}`);
  }

  // Restore previous session (keeps tabs from before close)
  args.push('--restore-last-session');

  log.info('Relaunching browser with CDP', { exePath, port, userDataDir, profileDir });

  const child = spawn(exePath, args, {
    detached: true,
    stdio: 'ignore',
    windowsHide: false,
  });
  child.unref();

  const newPid = child.pid;
  if (!newPid) throw new Error('Failed to spawn browser process');

  log.info('Browser relaunched', { newPid, port });
  return newPid;
}

/**
 * Wait for CDP to become available on a port (polls /json/version).
 */
async function waitForCDP(port: number, maxWaitMs: number = 15000): Promise<boolean> {
  const start = Date.now();
  const interval = 500;

  while (Date.now() - start < maxWaitMs) {
    try {
      const targets = await CDPConnection.discoverTargets('127.0.0.1', port);
      if (targets.length > 0) return true;
    } catch { /* not ready yet */ }
    await new Promise(r => setTimeout(r, interval));
  }
  return false;
}

export class BrowserPlugin implements FrameworkPlugin {
  readonly framework = 'browser' as const;
  readonly name = 'Browser (CDP)';

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'browser';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const processName = app.path
      ? app.path.split(/[\\/]/).pop() || ''
      : '';
    const browserPort = getDefaultDebugPort(processName);
    const port = (app.connectionInfo?.debugPort as number) || browserPort;

    let actualPort = port;

    // ── Fast path: try the browser-specific CDP port first ──
    // Each browser gets its own default port (Chrome=9222, Edge=9223, etc.)
    // to avoid conflicts when multiple browsers run simultaneously.
    if (!app.connectionInfo?.debugPort) {
      let cdpReady = false;
      try {
        const targets = await CDPConnection.discoverTargets('127.0.0.1', browserPort);
        if (targets.length > 0) {
          actualPort = browserPort;
          cdpReady = true;
          log.info('CDP already available on browser-specific port', {
            name: app.name, port: browserPort, targets: targets.length,
          });
        }
      } catch { /* CDP not on this port, continue discovery */ }

      if (!cdpReady) {
        // Fallback: inspect command line for a custom port
        const discovered = CDPConnection.findDebugPort(app.pid);
        if (discovered) {
          actualPort = discovered;
        } else {
          // ── Auto-launch: browser is running without debug port ──
          // Close and relaunch with --remote-debugging-port
          log.info('No CDP debug port found — auto-relaunching browser', {
            name: app.name, pid: app.pid, port: browserPort,
          });

          if (!app.path) {
            throw new Error(
              `Cannot auto-relaunch ${app.name}: executable path unknown.\n` +
              `Manually relaunch with: "${app.name}" --remote-debugging-port=${browserPort}`
            );
          }

          // Capture profile info before closing
          const userDataDir = getUserDataDir(processName, app.pid);
          const profileDir = getProfileDir(app.pid);

          // Close the running browser
          await closeBrowserGracefully(app.pid, processName);

          // Relaunch with browser-specific debug port
          actualPort = browserPort;
          const newPid = await relaunchWithDebugPort(
            app.path, processName, userDataDir, profileDir, actualPort
          );

          // Update the app reference with the new PID
          (app as { pid: number }).pid = newPid;

          // Wait for CDP to become available
          const ready = await waitForCDP(actualPort);
          if (!ready) {
            throw new Error(
              `Browser relaunched (PID ${newPid}) but CDP not available on port ${actualPort} after 15s.\n` +
              `Check if another process is using port ${actualPort}.`
            );
          }

          log.info('Auto-relaunch successful — CDP ready', { newPid, port: actualPort });
        }
      }
    }

    // Discover all targets (tabs)
    const targets = await CDPConnection.discoverTargets('127.0.0.1', actualPort);
    const pageTargets = targets.filter(t => t.type === 'page');
    if (pageTargets.length === 0) {
      throw new Error(`No browser tabs found on CDP port ${actualPort}. Found target types: ${targets.map(t => t.type).join(', ')}`);
    }

    // Connect to the first page target by default
    const primaryTarget = pageTargets[0];
    const cdp = new CDPConnection('127.0.0.1', actualPort);
    await cdp.connect(primaryTarget.webSocketDebuggerUrl);
    await cdp.enableDOM();
    await cdp.enableRuntime();
    await cdp.enablePage();

    // Enable Network domain for cookie management
    await cdp.send('Network.enable');

    return new BrowserConnection(app, cdp, actualPort, pageTargets);
  }
}

/**
 * Check if a process name is a known browser.
 */
export function isBrowserProcess(processName: string): boolean {
  return BROWSER_PROCESSES.has(processName.toLowerCase());
}

/**
 * Get a display name for a browser process.
 */
export function getBrowserDisplayName(processName: string): string {
  return BROWSER_NAMES[processName.toLowerCase()] || processName.replace('.exe', '');
}

// ─── Browser Connection ───────────────────────────────────────

class BrowserConnection implements PluginConnection {
  readonly app: DetectedApp;
  private cdp: CDPConnection;
  private mapper: DOMMapper;
  private port: number;
  private tabs: CDPTarget[];
  private subscriptions: Map<string, { event: UABEventType; cleanup: () => void }> = new Map();
  private cachedTree: UIElement[] | null = null;
  private cacheTimestamp = 0;
  private readonly CACHE_TTL = 2000;

  constructor(app: DetectedApp, cdp: CDPConnection, port: number, tabs: CDPTarget[]) {
    this.app = app;
    this.cdp = cdp;
    this.port = port;
    this.tabs = tabs;
    this.mapper = new DOMMapper(cdp);
  }

  get connected(): boolean { return this.cdp.connected; }

  // ─── Core UAB API ──────────────────────────────────────────

  async enumerate(): Promise<UIElement[]> {
    this.ensureConnected();
    if (this.cachedTree && Date.now() - this.cacheTimestamp < this.CACHE_TTL) {
      return this.cachedTree;
    }
    const tree = await this.mapper.mapDocument();
    await this.populateBoundsRecursive(tree, 0, 2);
    this.cachedTree = tree;
    this.cacheTimestamp = Date.now();
    return tree;
  }

  private async populateBoundsRecursive(elements: UIElement[], depth: number, maxDepth: number): Promise<void> {
    if (depth > maxDepth) return;
    const interactive = ['button', 'link', 'textfield', 'textarea', 'checkbox', 'radio', 'select', 'menuitem', 'tab'];
    for (const el of elements) {
      if (interactive.includes(el.type) || depth === 0) {
        await this.mapper.populateBounds(el);
      }
      if (el.children.length > 0) {
        await this.populateBoundsRecursive(el.children, depth + 1, maxDepth);
      }
    }
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    this.ensureConnected();
    const cssSelector = this.toCSSSelector(selector);
    if (cssSelector && !selector.label && !selector.labelRegex) {
      return this.queryCDP(cssSelector, selector);
    }
    const tree = await this.enumerate();
    return this.filterTree(tree, selector, 0, selector.maxDepth);
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    this.ensureConnected();

    // Browser-specific actions (no element needed)
    switch (action) {
      // Cookie management
      case 'getCookies': return await this.doGetCookies(params);
      case 'setCookie': return await this.doSetCookie(params);
      case 'deleteCookie': return await this.doDeleteCookie(params);
      case 'clearCookies': return await this.doClearCookies(params);
      // Storage management
      case 'getLocalStorage': return await this.doGetStorage('localStorage', params);
      case 'setLocalStorage': return await this.doSetStorage('localStorage', params);
      case 'deleteLocalStorage': return await this.doDeleteStorage('localStorage', params);
      case 'clearLocalStorage': return await this.doClearStorage('localStorage');
      case 'getSessionStorage': return await this.doGetStorage('sessionStorage', params);
      case 'setSessionStorage': return await this.doSetStorage('sessionStorage', params);
      case 'deleteSessionStorage': return await this.doDeleteStorage('sessionStorage', params);
      case 'clearSessionStorage': return await this.doClearStorage('sessionStorage');
      // Navigation
      case 'navigate': return await this.doNavigate(params);
      case 'goBack': return await this.doGoBack();
      case 'goForward': return await this.doGoForward();
      case 'reload': return await this.doReload();
      // Tab management
      case 'getTabs': return await this.doGetTabs();
      case 'switchTab': return await this.doSwitchTab(params);
      case 'closeTab': return await this.doCloseTab(params);
      case 'newTab': return await this.doNewTab(params);
      // Script execution
      case 'executeScript': return await this.doExecuteScript(params);
      // Element-free standard actions
      case 'screenshot': return await this.doScreenshot(params);
      case 'keypress': return await this.doKeypress(params);
      case 'hotkey': return await this.doHotkey(params);
      case 'minimize': return await this.doWindowAction('minimize');
      case 'maximize': return await this.doWindowAction('maximize');
      case 'restore': return await this.doWindowAction('restore');
      case 'close': return await this.doWindowAction('close');
      case 'move': return await this.doWindowMove(params);
      case 'resize': return await this.doWindowResize(params);
    }

    // Element-based actions — auto-enumerate if node map is empty
    let nodeId = this.mapper.getNodeId(elementId);
    if (!nodeId) {
      await this.enumerate();
      nodeId = this.mapper.getNodeId(elementId);
    }
    if (!nodeId) return { success: false, error: `Element not found: ${elementId}` };

    try {
      switch (action) {
        case 'click': return await this.doClick(nodeId);
        case 'doubleclick': return await this.doDoubleClick(nodeId);
        case 'rightclick': return await this.doRightClick(nodeId);
        case 'type': return await this.doType(nodeId, params);
        case 'clear': return await this.doClear(nodeId);
        case 'select': return await this.doSelect(nodeId, params);
        case 'focus': return await this.doFocus(nodeId);
        case 'hover': return await this.doHover(nodeId);
        case 'scroll': return await this.doScroll(nodeId, params);
        case 'check': case 'uncheck': case 'toggle':
          return await this.doToggle(nodeId, action);
        case 'expand': case 'collapse':
          return await this.doExpandCollapse(nodeId, action);
        case 'invoke': return await this.doInvoke(nodeId, params);
        default: return { success: false, error: `Unknown action: ${action}` };
      }
    } catch (err) {
      return { success: false, error: `Action failed: ${err}` };
    } finally {
      this.cachedTree = null;
    }
  }

  async state(): Promise<AppState> {
    this.ensureConnected();
    const windowInfo = await this.cdp.evaluate(`
      JSON.stringify({
        title: document.title,
        url: window.location.href,
        width: window.innerWidth, height: window.innerHeight,
        screenX: window.screenX, screenY: window.screenY,
        focused: document.hasFocus(),
      })
    `) as string;
    const info = JSON.parse(windowInfo);

    return {
      window: {
        title: `${info.title} — ${info.url}`,
        size: { width: info.width, height: info.height },
        position: { x: info.screenX, y: info.screenY },
        focused: info.focused,
      },
      activeElement: undefined,
      modals: [],
      menus: [],
    };
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    this.ensureConnected();
    const subId = randomUUID();

    switch (event) {
      case 'treeChanged': {
        const handler = (params: Record<string, unknown>) => {
          callback({ type: 'treeChanged', timestamp: Date.now(), changes: { mutation: { old: null, new: params } } });
          this.cachedTree = null;
        };
        this.cdp.on('DOM.documentUpdated', handler);
        this.cdp.on('DOM.childNodeInserted', handler);
        this.cdp.on('DOM.childNodeRemoved', handler);
        this.subscriptions.set(subId, {
          event,
          cleanup: () => {
            this.cdp.off('DOM.documentUpdated', handler);
            this.cdp.off('DOM.childNodeInserted', handler);
            this.cdp.off('DOM.childNodeRemoved', handler);
          },
        });
        break;
      }
      case 'stateChanged': {
        const handler = (params: Record<string, unknown>) => {
          callback({ type: 'stateChanged', timestamp: Date.now(), changes: { navigation: { old: null, new: params } } });
          this.cachedTree = null;
        };
        this.cdp.on('Page.frameNavigated', handler);
        this.cdp.on('Page.loadEventFired', handler);
        this.subscriptions.set(subId, {
          event,
          cleanup: () => {
            this.cdp.off('Page.frameNavigated', handler);
            this.cdp.off('Page.loadEventFired', handler);
          },
        });
        break;
      }
      default: break;
    }

    return {
      id: subId, event,
      unsubscribe: () => {
        const sub = this.subscriptions.get(subId);
        if (sub) { sub.cleanup(); this.subscriptions.delete(subId); }
      },
    };
  }

  async disconnect(): Promise<void> {
    for (const [, sub] of this.subscriptions) sub.cleanup();
    this.subscriptions.clear();
    this.cachedTree = null;
    await this.cdp.disconnect();
  }

  // ═══════════════════════════════════════════════════════════
  // COOKIE MANAGEMENT (via CDP Network domain)
  // ═══════════════════════════════════════════════════════════

  private async doGetCookies(params?: ActionParams): Promise<ActionResult> {
    try {
      const cdpParams: Record<string, unknown> = {};
      if (params?.url) {
        cdpParams.urls = [params.url];
      }
      const result = await this.cdp.send('Network.getCookies', cdpParams);
      const cookies = (result as { cookies?: unknown[] }).cookies || [];

      // Filter by name if specified
      let filtered = cookies as Array<Record<string, unknown>>;
      if (params?.cookieName) {
        filtered = filtered.filter(c => c.name === params.cookieName);
      }
      if (params?.domain) {
        filtered = filtered.filter(c =>
          (c.domain as string)?.includes(params.domain!)
        );
      }

      return {
        success: true,
        result: {
          count: filtered.length,
          cookies: filtered.map(c => ({
            name: c.name,
            value: c.value,
            domain: c.domain,
            path: c.path,
            expires: c.expires,
            size: c.size,
            httpOnly: c.httpOnly,
            secure: c.secure,
            sameSite: c.sameSite,
            session: c.session,
          })),
        },
      };
    } catch (err) {
      return { success: false, error: `Get cookies failed: ${err}` };
    }
  }

  private async doSetCookie(params?: ActionParams): Promise<ActionResult> {
    if (!params?.cookieName) return { success: false, error: 'cookieName is required' };

    try {
      // Get current URL if domain not specified
      let url = params.url;
      if (!url && !params.domain) {
        url = await this.cdp.evaluate('window.location.href') as string;
      }

      const cookieParams: Record<string, unknown> = {
        name: params.cookieName,
        value: params.cookieValue || '',
      };

      if (url) cookieParams.url = url;
      if (params.domain) cookieParams.domain = params.domain;
      if (params.path) cookieParams.path = params.path;
      if (params.secure !== undefined) cookieParams.secure = params.secure;
      if (params.httpOnly !== undefined) cookieParams.httpOnly = params.httpOnly;
      if (params.sameSite) cookieParams.sameSite = params.sameSite;
      if (params.expires) cookieParams.expires = params.expires;

      const result = await this.cdp.send('Network.setCookie', cookieParams);
      const success = (result as { success?: boolean }).success !== false;

      return {
        success,
        result: success
          ? { message: `Cookie "${params.cookieName}" set successfully` }
          : { message: 'Failed to set cookie — domain/URL mismatch?' },
      };
    } catch (err) {
      return { success: false, error: `Set cookie failed: ${err}` };
    }
  }

  private async doDeleteCookie(params?: ActionParams): Promise<ActionResult> {
    if (!params?.cookieName) return { success: false, error: 'cookieName is required' };

    try {
      const deleteParams: Record<string, unknown> = {
        name: params.cookieName,
      };

      // Need either URL or domain
      if (params.url) {
        deleteParams.url = params.url;
      } else if (params.domain) {
        deleteParams.domain = params.domain;
      } else {
        // Use current page URL
        const url = await this.cdp.evaluate('window.location.href') as string;
        deleteParams.url = url;
      }

      if (params.path) deleteParams.path = params.path;

      await this.cdp.send('Network.deleteCookies', deleteParams);
      return { success: true, result: { message: `Cookie "${params.cookieName}" deleted` } };
    } catch (err) {
      return { success: false, error: `Delete cookie failed: ${err}` };
    }
  }

  private async doClearCookies(params?: ActionParams): Promise<ActionResult> {
    try {
      if (params?.domain) {
        // Clear cookies for a specific domain
        const result = await this.cdp.send('Network.getCookies');
        const cookies = (result as { cookies?: Array<Record<string, unknown>> }).cookies || [];
        const domainCookies = cookies.filter(c =>
          (c.domain as string)?.includes(params.domain!)
        );

        for (const cookie of domainCookies) {
          await this.cdp.send('Network.deleteCookies', {
            name: cookie.name,
            domain: cookie.domain,
            path: cookie.path,
          });
        }

        return {
          success: true,
          result: { message: `Cleared ${domainCookies.length} cookies for domain ${params.domain}` },
        };
      } else {
        // Clear ALL cookies
        await this.cdp.send('Network.clearBrowserCookies');
        return { success: true, result: { message: 'All browser cookies cleared' } };
      }
    } catch (err) {
      return { success: false, error: `Clear cookies failed: ${err}` };
    }
  }

  // ═══════════════════════════════════════════════════════════
  // STORAGE (localStorage / sessionStorage via CDP Runtime)
  // ═══════════════════════════════════════════════════════════

  private async doGetStorage(storageType: 'localStorage' | 'sessionStorage', params?: ActionParams): Promise<ActionResult> {
    try {
      if (params?.storageKey) {
        // Get specific key
        const value = await this.cdp.evaluate(
          `${storageType}.getItem(${JSON.stringify(params.storageKey)})`
        );
        return {
          success: true,
          result: { key: params.storageKey, value, exists: value !== null },
        };
      } else {
        // Get all keys/values
        const data = await this.cdp.evaluate(`
          (() => {
            const items = {};
            for (let i = 0; i < ${storageType}.length; i++) {
              const key = ${storageType}.key(i);
              items[key] = ${storageType}.getItem(key);
            }
            return JSON.stringify({ count: ${storageType}.length, items });
          })()
        `) as string;
        return { success: true, result: JSON.parse(data) };
      }
    } catch (err) {
      return { success: false, error: `Get ${storageType} failed: ${err}` };
    }
  }

  private async doSetStorage(storageType: 'localStorage' | 'sessionStorage', params?: ActionParams): Promise<ActionResult> {
    if (!params?.storageKey) return { success: false, error: 'storageKey is required' };

    try {
      await this.cdp.evaluate(
        `${storageType}.setItem(${JSON.stringify(params.storageKey)}, ${JSON.stringify(params.storageValue || '')})`
      );
      return {
        success: true,
        result: { message: `${storageType} key "${params.storageKey}" set` },
      };
    } catch (err) {
      return { success: false, error: `Set ${storageType} failed: ${err}` };
    }
  }

  private async doDeleteStorage(storageType: 'localStorage' | 'sessionStorage', params?: ActionParams): Promise<ActionResult> {
    if (!params?.storageKey) return { success: false, error: 'storageKey is required' };

    try {
      await this.cdp.evaluate(
        `${storageType}.removeItem(${JSON.stringify(params.storageKey)})`
      );
      return {
        success: true,
        result: { message: `${storageType} key "${params.storageKey}" deleted` },
      };
    } catch (err) {
      return { success: false, error: `Delete ${storageType} failed: ${err}` };
    }
  }

  private async doClearStorage(storageType: 'localStorage' | 'sessionStorage'): Promise<ActionResult> {
    try {
      await this.cdp.evaluate(`${storageType}.clear()`);
      return { success: true, result: { message: `${storageType} cleared` } };
    } catch (err) {
      return { success: false, error: `Clear ${storageType} failed: ${err}` };
    }
  }

  // ═══════════════════════════════════════════════════════════
  // NAVIGATION
  // ═══════════════════════════════════════════════════════════

  private async doNavigate(params?: ActionParams): Promise<ActionResult> {
    if (!params?.url) return { success: false, error: 'url is required for navigation' };

    try {
      const result = await this.cdp.send('Page.navigate', { url: params.url });
      const frameId = (result as { frameId?: string }).frameId;
      const errorText = (result as { errorText?: string }).errorText;

      if (errorText) {
        return { success: false, error: `Navigation failed: ${errorText}` };
      }

      // Wait for load
      await new Promise<void>((resolve) => {
        const handler = () => {
          this.cdp.off('Page.loadEventFired', handler);
          resolve();
        };
        this.cdp.on('Page.loadEventFired', handler);
        // Timeout after 15s
        setTimeout(() => {
          this.cdp.off('Page.loadEventFired', handler);
          resolve();
        }, 15000);
      });

      this.cachedTree = null;
      const title = await this.cdp.evaluate('document.title') as string;

      return {
        success: true,
        result: { url: params.url, title, frameId },
      };
    } catch (err) {
      return { success: false, error: `Navigate failed: ${err}` };
    }
  }

  private async doGoBack(): Promise<ActionResult> {
    try {
      const history = await this.cdp.send('Page.getNavigationHistory');
      const entries = (history as { entries?: Array<{ url: string; id: number }> }).entries || [];
      const currentIndex = (history as { currentIndex?: number }).currentIndex || 0;

      if (currentIndex <= 0) return { success: false, error: 'No previous page in history' };

      await this.cdp.send('Page.navigateToHistoryEntry', { entryId: entries[currentIndex - 1].id });
      this.cachedTree = null;
      return { success: true, result: { message: 'Navigated back' } };
    } catch (err) {
      return { success: false, error: `Go back failed: ${err}` };
    }
  }

  private async doGoForward(): Promise<ActionResult> {
    try {
      const history = await this.cdp.send('Page.getNavigationHistory');
      const entries = (history as { entries?: Array<{ url: string; id: number }> }).entries || [];
      const currentIndex = (history as { currentIndex?: number }).currentIndex || 0;

      if (currentIndex >= entries.length - 1) return { success: false, error: 'No next page in history' };

      await this.cdp.send('Page.navigateToHistoryEntry', { entryId: entries[currentIndex + 1].id });
      this.cachedTree = null;
      return { success: true, result: { message: 'Navigated forward' } };
    } catch (err) {
      return { success: false, error: `Go forward failed: ${err}` };
    }
  }

  private async doReload(): Promise<ActionResult> {
    try {
      await this.cdp.send('Page.reload', { ignoreCache: false });
      this.cachedTree = null;
      return { success: true, result: { message: 'Page reloaded' } };
    } catch (err) {
      return { success: false, error: `Reload failed: ${err}` };
    }
  }

  // ═══════════════════════════════════════════════════════════
  // TAB MANAGEMENT
  // ═══════════════════════════════════════════════════════════

  private async doGetTabs(): Promise<ActionResult> {
    try {
      const targets = await CDPConnection.discoverTargets('127.0.0.1', this.port);
      const pages = targets.filter(t => t.type === 'page');
      this.tabs = pages;

      return {
        success: true,
        result: {
          count: pages.length,
          tabs: pages.map((t, i) => ({
            index: i,
            id: t.id,
            title: t.title,
            url: t.url,
          })),
        },
      };
    } catch (err) {
      return { success: false, error: `Get tabs failed: ${err}` };
    }
  }

  private async doSwitchTab(params?: ActionParams): Promise<ActionResult> {
    if (!params?.tabId) return { success: false, error: 'tabId is required' };

    try {
      // Refresh tab list
      const targets = await CDPConnection.discoverTargets('127.0.0.1', this.port);
      const pages = targets.filter(t => t.type === 'page');

      // Find the target — by ID or by index
      let target: CDPTarget | undefined;
      const tabIndex = parseInt(params.tabId, 10);
      if (!isNaN(tabIndex) && tabIndex >= 0 && tabIndex < pages.length) {
        target = pages[tabIndex];
      } else {
        target = pages.find(t => t.id === params.tabId);
      }

      if (!target) return { success: false, error: `Tab not found: ${params.tabId}` };

      // Disconnect current and connect to new tab
      await this.cdp.disconnect();
      this.cdp = new CDPConnection('127.0.0.1', this.port);
      await this.cdp.connect(target.webSocketDebuggerUrl);
      await this.cdp.enableDOM();
      await this.cdp.enableRuntime();
      await this.cdp.enablePage();
      await this.cdp.send('Network.enable');

      this.mapper = new DOMMapper(this.cdp);
      this.cachedTree = null;
      this.tabs = pages;

      // Activate the tab in the browser
      await this.cdp.send('Page.bringToFront');

      return {
        success: true,
        result: { message: `Switched to tab: ${target.title}`, tabId: target.id, url: target.url },
      };
    } catch (err) {
      return { success: false, error: `Switch tab failed: ${err}` };
    }
  }

  private async doCloseTab(params?: ActionParams): Promise<ActionResult> {
    try {
      if (params?.tabId) {
        // Close specific tab by activating CDP Target.closeTarget
        await this.cdp.send('Target.closeTarget', { targetId: params.tabId });
        return { success: true, result: { message: `Tab ${params.tabId} closed` } };
      } else {
        // Close current tab
        await this.cdp.send('Page.close');
        return { success: true, result: { message: 'Current tab closed' } };
      }
    } catch (err) {
      return { success: false, error: `Close tab failed: ${err}` };
    }
  }

  private async doNewTab(params?: ActionParams): Promise<ActionResult> {
    try {
      const url = params?.url || 'about:blank';
      const result = await this.cdp.send('Target.createTarget', { url });
      const targetId = (result as { targetId?: string }).targetId;

      // Switch to the new tab
      if (targetId) {
        const targets = await CDPConnection.discoverTargets('127.0.0.1', this.port);
        const newTarget = targets.find(t => t.id === targetId);
        if (newTarget) {
          await this.cdp.disconnect();
          this.cdp = new CDPConnection('127.0.0.1', this.port);
          await this.cdp.connect(newTarget.webSocketDebuggerUrl);
          await this.cdp.enableDOM();
          await this.cdp.enableRuntime();
          await this.cdp.enablePage();
          await this.cdp.send('Network.enable');
          this.mapper = new DOMMapper(this.cdp);
          this.cachedTree = null;
        }
      }

      return {
        success: true,
        result: { message: `New tab opened: ${url}`, targetId },
      };
    } catch (err) {
      return { success: false, error: `New tab failed: ${err}` };
    }
  }

  // ═══════════════════════════════════════════════════════════
  // SCRIPT EXECUTION
  // ═══════════════════════════════════════════════════════════

  private async doExecuteScript(params?: ActionParams): Promise<ActionResult> {
    if (!params?.script) return { success: false, error: 'script is required' };

    try {
      const value = await this.cdp.evaluate(params.script);
      return { success: true, result: value };
    } catch (err) {
      return { success: false, error: `Script execution failed: ${err}` };
    }
  }

  // ═══════════════════════════════════════════════════════════
  // DOM ELEMENT ACTIONS (same as Electron plugin)
  // ═══════════════════════════════════════════════════════════

  private async doClick(nodeId: number): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', { objectId, functionDeclaration: `function() { this.click(); }`, returnByValue: true });
    return { success: true };
  }

  private async doDoubleClick(nodeId: number): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function() { this.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); }`,
      returnByValue: true,
    });
    return { success: true };
  }

  private async doRightClick(nodeId: number): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function() { this.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, button: 2 })); }`,
      returnByValue: true,
    });
    return { success: true };
  }

  private async doType(nodeId: number, params?: ActionParams): Promise<ActionResult> {
    if (!params?.text) return { success: false, error: 'No text provided' };
    await this.doFocus(nodeId);
    await this.cdp.send('Input.insertText', { text: params.text });
    return { success: true };
  }

  private async doClear(nodeId: number): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function() { this.value = ''; this.dispatchEvent(new Event('input', { bubbles: true })); this.dispatchEvent(new Event('change', { bubbles: true })); }`,
      returnByValue: true,
    });
    return { success: true };
  }

  private async doSelect(nodeId: number, params?: ActionParams): Promise<ActionResult> {
    if (!params?.value) return { success: false, error: 'No value provided' };
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function(value) { this.value = value; this.dispatchEvent(new Event('change', { bubbles: true })); }`,
      arguments: [{ value: params.value }],
      returnByValue: true,
    });
    return { success: true };
  }

  private async doFocus(nodeId: number): Promise<ActionResult> {
    await this.cdp.send('DOM.focus', { nodeId });
    return { success: true };
  }

  private async doHover(nodeId: number): Promise<ActionResult> {
    const boxModel = await this.cdp.getBoxModel(nodeId);
    if (boxModel) {
      const model = (boxModel as Record<string, unknown>).model as Record<string, unknown> | undefined;
      if (model) {
        const content = model.content as number[];
        const x = (content[0] + content[2]) / 2;
        const y = (content[1] + content[5]) / 2;
        await this.cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      }
    }
    return { success: true };
  }

  private async doScroll(nodeId: number, params?: ActionParams): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    const direction = params?.direction || 'down';
    const amount = params?.amount || 300;
    const scrollX = direction === 'left' ? -amount : direction === 'right' ? amount : 0;
    const scrollY = direction === 'up' ? -amount : direction === 'down' ? amount : 0;
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function(x, y) { this.scrollBy(x, y); }`,
      arguments: [{ value: scrollX }, { value: scrollY }],
      returnByValue: true,
    });
    return { success: true };
  }

  private async doToggle(nodeId: number, action: 'check' | 'uncheck' | 'toggle'): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function(action) {
        if (action === 'toggle') this.checked = !this.checked;
        else if (action === 'check') this.checked = true;
        else this.checked = false;
        this.dispatchEvent(new Event('change', { bubbles: true }));
      }`,
      arguments: [{ value: action }],
      returnByValue: true,
    });
    return { success: true };
  }

  private async doExpandCollapse(nodeId: number, action: 'expand' | 'collapse'): Promise<ActionResult> {
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function(action) {
        this.setAttribute('aria-expanded', (action === 'expand').toString());
        this.dispatchEvent(new Event('click', { bubbles: true }));
      }`,
      arguments: [{ value: action }],
      returnByValue: true,
    });
    return { success: true };
  }

  private async doInvoke(nodeId: number, params?: ActionParams): Promise<ActionResult> {
    if (!params?.method) return { success: false, error: 'No method provided' };
    const result = await this.cdp.send('DOM.resolveNode', { nodeId });
    const objectId = (result as Record<string, unknown> & { object?: { objectId?: string } }).object?.objectId;
    if (!objectId) return { success: false, error: 'Cannot resolve node' };
    const invokeResult = await this.cdp.send('Runtime.callFunctionOn', {
      objectId,
      functionDeclaration: `function(method, args) {
        if (typeof this[method] === 'function') return this[method](...(args || []));
        throw new Error('Method not found: ' + method);
      }`,
      arguments: [{ value: params.method }, { value: params.args || [] }],
      returnByValue: true,
    });
    return { success: true, result: (invokeResult as Record<string, unknown> & { result?: { value?: unknown } }).result?.value };
  }

  // ═══════════════════════════════════════════════════════════
  // SCREENSHOT / KEYBOARD / WINDOW (same as Electron plugin)
  // ═══════════════════════════════════════════════════════════

  private async doScreenshot(params?: ActionParams): Promise<ActionResult> {
    try {
      const result = await this.cdp.send('Page.captureScreenshot', { format: 'png', quality: 100 });
      const data = (result as Record<string, unknown>).data as string;
      if (!data) return { success: false, error: 'CDP returned no screenshot data' };
      const outPath = params?.outputPath || `data/screenshots/uab-browser-${this.app.pid}-${Date.now()}.png`;
      mkdirSync(dirname(outPath), { recursive: true });
      writeFileSync(outPath, Buffer.from(data, 'base64'));
      return { success: true, result: outPath };
    } catch (err) {
      return { success: false, error: `Screenshot failed: ${err}` };
    }
  }

  private async doKeypress(params?: ActionParams): Promise<ActionResult> {
    const key = params?.key || params?.text || '';
    if (!key) return { success: false, error: 'No key provided' };
    try {
      const cdpKey = this.mapKeyToCDP(key);
      await this.cdp.send('Input.dispatchKeyEvent', {
        type: 'keyDown', key: cdpKey.key, code: cdpKey.code,
        windowsVirtualKeyCode: cdpKey.keyCode, nativeVirtualKeyCode: cdpKey.keyCode,
      });
      await this.cdp.send('Input.dispatchKeyEvent', {
        type: 'keyUp', key: cdpKey.key, code: cdpKey.code,
        windowsVirtualKeyCode: cdpKey.keyCode, nativeVirtualKeyCode: cdpKey.keyCode,
      });
      return { success: true };
    } catch (err) {
      return { success: false, error: `Keypress failed: ${err}` };
    }
  }

  private async doHotkey(params?: ActionParams): Promise<ActionResult> {
    const keys = params?.keys;
    if (!keys || keys.length === 0) return { success: false, error: 'No keys provided' };
    try {
      let modifiers = 0;
      const modKeyNames: string[] = [];
      const nonModKeys: string[] = [];
      for (const k of keys) {
        const lower = k.toLowerCase();
        if (lower === 'ctrl' || lower === 'control') { modifiers |= 2; modKeyNames.push('Control'); }
        else if (lower === 'alt') { modifiers |= 1; modKeyNames.push('Alt'); }
        else if (lower === 'shift') { modifiers |= 8; modKeyNames.push('Shift'); }
        else if (lower === 'meta' || lower === 'win') { modifiers |= 4; modKeyNames.push('Meta'); }
        else nonModKeys.push(k);
      }
      for (const modKey of modKeyNames) {
        await this.cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: modKey, modifiers });
      }
      for (const k of nonModKeys) {
        const cdpKey = this.mapKeyToCDP(k);
        await this.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyDown', key: cdpKey.key, code: cdpKey.code,
          windowsVirtualKeyCode: cdpKey.keyCode, modifiers,
        });
        await this.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyUp', key: cdpKey.key, code: cdpKey.code,
          windowsVirtualKeyCode: cdpKey.keyCode, modifiers,
        });
      }
      for (const modKey of modKeyNames.reverse()) {
        await this.cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: modKey, modifiers: 0 });
      }
      return { success: true };
    } catch (err) {
      return { success: false, error: `Hotkey failed: ${err}` };
    }
  }

  private mapKeyToCDP(key: string): { key: string; code: string; keyCode: number } {
    const lower = key.toLowerCase();
    const CDP_KEY_MAP: Record<string, { key: string; code: string; keyCode: number }> = {
      enter: { key: 'Enter', code: 'Enter', keyCode: 13 },
      return: { key: 'Enter', code: 'Enter', keyCode: 13 },
      tab: { key: 'Tab', code: 'Tab', keyCode: 9 },
      escape: { key: 'Escape', code: 'Escape', keyCode: 27 },
      esc: { key: 'Escape', code: 'Escape', keyCode: 27 },
      space: { key: ' ', code: 'Space', keyCode: 32 },
      backspace: { key: 'Backspace', code: 'Backspace', keyCode: 8 },
      delete: { key: 'Delete', code: 'Delete', keyCode: 46 },
      insert: { key: 'Insert', code: 'Insert', keyCode: 45 },
      home: { key: 'Home', code: 'Home', keyCode: 36 },
      end: { key: 'End', code: 'End', keyCode: 35 },
      pageup: { key: 'PageUp', code: 'PageUp', keyCode: 33 },
      pagedown: { key: 'PageDown', code: 'PageDown', keyCode: 34 },
      up: { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38 },
      down: { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40 },
      left: { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37 },
      right: { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
      f1: { key: 'F1', code: 'F1', keyCode: 112 }, f2: { key: 'F2', code: 'F2', keyCode: 113 },
      f3: { key: 'F3', code: 'F3', keyCode: 114 }, f4: { key: 'F4', code: 'F4', keyCode: 115 },
      f5: { key: 'F5', code: 'F5', keyCode: 116 }, f6: { key: 'F6', code: 'F6', keyCode: 117 },
      f7: { key: 'F7', code: 'F7', keyCode: 118 }, f8: { key: 'F8', code: 'F8', keyCode: 119 },
      f9: { key: 'F9', code: 'F9', keyCode: 120 }, f10: { key: 'F10', code: 'F10', keyCode: 121 },
      f11: { key: 'F11', code: 'F11', keyCode: 122 }, f12: { key: 'F12', code: 'F12', keyCode: 123 },
    };
    if (CDP_KEY_MAP[lower]) return CDP_KEY_MAP[lower];
    if (key.length === 1) {
      const code = key >= 'a' && key <= 'z' ? `Key${key.toUpperCase()}` :
                   key >= 'A' && key <= 'Z' ? `Key${key}` :
                   key >= '0' && key <= '9' ? `Digit${key}` : '';
      const keyCode = key.toUpperCase().charCodeAt(0);
      return { key, code, keyCode };
    }
    return { key, code: key, keyCode: 0 };
  }

  // ─── Window Management via Win32 API ─────────────────────

  private async doWindowAction(action: 'minimize' | 'maximize' | 'restore' | 'close'): Promise<ActionResult> {
    try {
      const actionMap: Record<string, string> = {
        minimize: '$SW_MINIMIZE = 6; [Win32Browser]::ShowWindow($hWnd, $SW_MINIMIZE) | Out-Null',
        maximize: '$SW_MAXIMIZE = 3; [Win32Browser]::ShowWindow($hWnd, $SW_MAXIMIZE) | Out-Null',
        restore: '$SW_RESTORE = 9; [Win32Browser]::ShowWindow($hWnd, $SW_RESTORE) | Out-Null',
        close: '[Win32Browser]::PostMessage($hWnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null',
      };
      const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32Browser {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
}
'@ -ErrorAction SilentlyContinue

$targetPid = ${this.app.pid}
$hWnd = [IntPtr]::Zero
[Win32Browser]::EnumWindows({
  param($hwnd, $lparam)
  $wpid = 0
  [Win32Browser]::GetWindowThreadProcessId($hwnd, [ref]$wpid) | Out-Null
  if ($wpid -eq $targetPid -and [Win32Browser]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero) | Out-Null

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found for PID ${this.app.pid}' } | ConvertTo-Json -Compress
} else {
  ${actionMap[action]}
  @{ success = $true; action = '${action}'; wpid = $targetPid } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 10000) as { success: boolean; error?: string };
      return result.success
        ? { success: true, result: { action } }
        : { success: false, error: result.error || `Window ${action} failed` };
    } catch (err) {
      return { success: false, error: `Window ${action} failed: ${err}` };
    }
  }

  private async doWindowMove(params?: ActionParams): Promise<ActionResult> {
    const x = params?.x ?? 0;
    const y = params?.y ?? 0;
    try {
      const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32BrowserMove {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@ -ErrorAction SilentlyContinue

$targetPid = ${this.app.pid}
$hWnd = [IntPtr]::Zero
[Win32BrowserMove]::EnumWindows({
  param($hwnd, $lparam)
  $wpid = 0
  [Win32BrowserMove]::GetWindowThreadProcessId($hwnd, [ref]$wpid) | Out-Null
  if ($wpid -eq $targetPid -and [Win32BrowserMove]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero) | Out-Null

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found' } | ConvertTo-Json -Compress
} else {
  $rect = New-Object Win32BrowserMove+RECT
  [Win32BrowserMove]::GetWindowRect($hWnd, [ref]$rect) | Out-Null
  $w = $rect.Right - $rect.Left
  $h = $rect.Bottom - $rect.Top
  $SWP_NOSIZE = 0x0001; $SWP_NOZORDER = 0x0004
  [Win32BrowserMove]::SetWindowPos($hWnd, [IntPtr]::Zero, ${x}, ${y}, $w, $h, ($SWP_NOSIZE -bor $SWP_NOZORDER)) | Out-Null
  @{ success = $true; x = ${x}; y = ${y} } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 10000) as { success: boolean; error?: string };
      return result.success ? { success: true, result: { x, y } } : { success: false, error: result.error || 'Move failed' };
    } catch (err) {
      return { success: false, error: `Window move failed: ${err}` };
    }
  }

  private async doWindowResize(params?: ActionParams): Promise<ActionResult> {
    const w = params?.width ?? 800;
    const h = params?.height ?? 600;
    try {
      const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32BrowserResize {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@ -ErrorAction SilentlyContinue

$targetPid = ${this.app.pid}
$hWnd = [IntPtr]::Zero
[Win32BrowserResize]::EnumWindows({
  param($hwnd, $lparam)
  $wpid = 0
  [Win32BrowserResize]::GetWindowThreadProcessId($hwnd, [ref]$wpid) | Out-Null
  if ($wpid -eq $targetPid -and [Win32BrowserResize]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero) | Out-Null

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found' } | ConvertTo-Json -Compress
} else {
  $rect = New-Object Win32BrowserResize+RECT
  [Win32BrowserResize]::GetWindowRect($hWnd, [ref]$rect) | Out-Null
  $SWP_NOMOVE = 0x0002; $SWP_NOZORDER = 0x0004
  [Win32BrowserResize]::SetWindowPos($hWnd, [IntPtr]::Zero, $rect.Left, $rect.Top, ${w}, ${h}, ($SWP_NOMOVE -bor $SWP_NOZORDER)) | Out-Null
  @{ success = $true; width = ${w}; height = ${h} } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 10000) as { success: boolean; error?: string };
      return result.success ? { success: true, result: { width: w, height: h } } : { success: false, error: result.error || 'Resize failed' };
    } catch (err) {
      return { success: false, error: `Window resize failed: ${err}` };
    }
  }

  // ─── Query Helpers ────────────────────────────────────────

  private toCSSSelector(selector: ElementSelector): string | null {
    if (!selector.type) return null;
    const typeMap: Record<string, string> = {
      button: 'button, [role="button"], input[type="button"], input[type="submit"]',
      textfield: 'input[type="text"], input[type="email"], input[type="password"], input[type="search"], input[type="url"], input[type="tel"], input[type="number"], input:not([type])',
      textarea: 'textarea',
      link: 'a[href], [role="link"]',
      checkbox: 'input[type="checkbox"], [role="checkbox"]',
      radio: 'input[type="radio"], [role="radio"]',
      select: 'select, [role="combobox"], [role="listbox"]',
      menu: '[role="menu"], menu',
      menuitem: '[role="menuitem"], menuitem',
      list: 'ul, ol, [role="list"]',
      listitem: 'li, [role="listitem"], [role="option"]',
      image: 'img, [role="img"]',
      heading: 'h1, h2, h3, h4, h5, h6, [role="heading"]',
      dialog: 'dialog, [role="dialog"], [role="alertdialog"]',
      tab: '[role="tab"]',
      table: 'table, [role="grid"]',
    };
    return typeMap[selector.type] || null;
  }

  private async queryCDP(cssSelector: string, selector: ElementSelector): Promise<UIElement[]> {
    const doc = await this.cdp.getDocument(0);
    const rootId = (doc as Record<string, unknown> & { root?: { nodeId?: number } }).root?.nodeId;
    if (!rootId) return [];
    const nodeIds = await this.cdp.querySelectorAll(rootId, cssSelector);
    const elements: UIElement[] = [];
    for (const nid of nodeIds.slice(0, selector.limit || 100)) {
      const el = await this.mapper.mapNode(nid);
      if (el && this.matchesSelector(el, selector)) elements.push(el);
    }
    return elements;
  }

  private filterTree(elements: UIElement[], selector: ElementSelector, depth: number, maxDepth?: number): UIElement[] {
    const results: UIElement[] = [];
    const limit = selector.limit || 100;
    for (const el of elements) {
      if (results.length >= limit) break;
      if (this.matchesSelector(el, selector)) results.push(el);
      if ((!maxDepth || depth < maxDepth) && el.children.length > 0) {
        for (const child of this.filterTree(el.children, selector, depth + 1, maxDepth)) {
          if (results.length >= limit) break;
          results.push(child);
        }
      }
    }
    return results;
  }

  private matchesSelector(element: UIElement, selector: ElementSelector): boolean {
    if (selector.type && element.type !== selector.type) return false;
    if (selector.visible !== undefined && element.visible !== selector.visible) return false;
    if (selector.enabled !== undefined && element.enabled !== selector.enabled) return false;
    if (selector.label && !element.label.toLowerCase().includes(selector.label.toLowerCase())) return false;
    if (selector.labelExact && element.label !== selector.labelExact) return false;
    if (selector.labelRegex && !new RegExp(selector.labelRegex, 'i').test(element.label)) return false;
    if (selector.properties) {
      for (const [key, value] of Object.entries(selector.properties)) {
        if (element.properties[key] !== value) return false;
      }
    }
    return true;
  }

  private ensureConnected(): void {
    if (!this.connected) throw new Error(`Not connected to ${this.app.name}`);
  }
}
