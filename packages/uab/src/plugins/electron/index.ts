/**
 * Electron Framework Plugin
 *
 * Connects to Electron apps via Chrome DevTools Protocol (CDP)
 * and exposes them through the UAB Unified API.
 *
 * Covers: VS Code, Slack, Discord, Spotify, Notion, Figma,
 * Teams, Obsidian, Postman, 1Password, Signal, and hundreds more.
 */

import type {
  FrameworkPlugin, PluginConnection, DetectedApp,
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
  AppState, UABEventType, UABEventCallback, UABEvent, Subscription,
} from '../../types.js';
import { CDPConnection } from './cdp.js';
import { DOMMapper } from './mapper.js';
import { randomUUID } from 'crypto';
import { writeFileSync, mkdirSync } from 'fs';
import { dirname } from 'path';
import { runPSJsonInteractive } from '../../ps-exec.js';

export class ElectronPlugin implements FrameworkPlugin {
  readonly framework = 'electron' as const;
  readonly name = 'Electron (CDP)';

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'electron';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    // Try finding a debug port on the given PID or its children.
    // Electron apps: main process often doesn't have the debug port,
    // but a child process (or a sibling launched with --remote-debugging-port) might.
    const portsToTry = this.findDebugPorts(app);

    for (const actualPort of portsToTry) {
      try {
        const targets = await CDPConnection.discoverTargets('127.0.0.1', actualPort);
        const pageTarget = targets.find(t => t.type === 'page');
        if (!pageTarget) continue;

        const cdp = new CDPConnection('127.0.0.1', actualPort);
        await cdp.connect(pageTarget.webSocketDebuggerUrl);
        await cdp.enableDOM();
        await cdp.enableRuntime();
        await cdp.enablePage();
        return new ElectronConnection(app, cdp);
      } catch {
        // This port didn't work, try the next one
      }
    }

    throw new Error(
      `Cannot find CDP debug port for ${app.name} (PID: ${app.pid}).\n` +
      `Relaunch with: ${CDPConnection.getEnableCommand(app.path, 9222)}\n` +
      `Or set ELECTRON_ENABLE_REMOTE_DEBUGGING=1 environment variable.`
    );
  }

  /**
   * Find all potential CDP debug ports for an Electron app.
   * Checks the main process AND all child processes (renderer, GPU, utility).
   */
  private findDebugPorts(app: DetectedApp): number[] {
    const ports: number[] = [];

    // Explicit port from detection
    if (app.connectionInfo?.debugPort) {
      ports.push(app.connectionInfo.debugPort as number);
    }

    // Check the main PID's command line
    const mainPort = CDPConnection.findDebugPort(app.pid);
    if (mainPort && !ports.includes(mainPort)) {
      ports.push(mainPort);
    }

    // Check child processes — Electron's main process spawns renderer/GPU
    // children that may have the debug port
    try {
      const { execSync } = require('child_process');
      const cmd = process.platform === 'win32'
        ? `wmic process where "ParentProcessId=${app.pid}" get ProcessId,CommandLine /format:csv`
        : `pgrep -P ${app.pid}`;
      const output = execSync(cmd, { encoding: 'utf-8', timeout: 5000 });

      if (process.platform === 'win32') {
        const portMatches = output.matchAll(/--remote-debugging-port=(\d+)/g);
        for (const m of portMatches) {
          const p = parseInt(m[1], 10);
          if (!ports.includes(p)) ports.push(p);
        }
      } else {
        // On macOS/Linux, check each child's command line
        const childPids = output.trim().split('\n').filter(Boolean);
        for (const cpid of childPids) {
          const childPort = CDPConnection.findDebugPort(parseInt(cpid.trim(), 10));
          if (childPort && !ports.includes(childPort)) ports.push(childPort);
        }
      }
    } catch {
      // Can't enumerate children — proceed with what we have
    }

    // Common fallback ports
    for (const p of [9222, 9229, 9223, 9224, 9225]) {
      if (!ports.includes(p)) ports.push(p);
    }

    return ports;
  }
}

class ElectronConnection implements PluginConnection {
  readonly app: DetectedApp;
  private cdp: CDPConnection;
  private mapper: DOMMapper;
  private subscriptions: Map<string, { event: UABEventType; cleanup: () => void }> = new Map();
  private cachedTree: UIElement[] | null = null;
  private cacheTimestamp = 0;
  private readonly CACHE_TTL = 2000;

  constructor(app: DetectedApp, cdp: CDPConnection) {
    this.app = app;
    this.cdp = cdp;
    this.mapper = new DOMMapper(cdp);
  }

  get connected(): boolean { return this.cdp.connected; }

  async enumerate(): Promise<UIElement[]> {
    this.ensureConnected();
    if (this.cachedTree && Date.now() - this.cacheTimestamp < this.CACHE_TTL) {
      return this.cachedTree;
    }
    const tree = await this.mapper.mapDocument();
    // Populate bounds for interactive elements (top 2 levels to avoid slowdown)
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

    // Element-free actions (screenshot, keypress, hotkey, window management)
    switch (action) {
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

    const nodeId = this.mapper.getNodeId(elementId);
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
        width: window.innerWidth, height: window.innerHeight,
        screenX: window.screenX, screenY: window.screenY,
        focused: document.hasFocus(),
      })
    `) as string;
    const info = JSON.parse(windowInfo);

    return {
      window: {
        title: info.title,
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
      case 'elementChanged': {
        const handler = (params: Record<string, unknown>) => {
          callback({ type: 'elementChanged', timestamp: Date.now(), changes: { attribute: { old: null, new: params } } });
        };
        this.cdp.on('DOM.attributeModified', handler);
        this.cdp.on('DOM.attributeRemoved', handler);
        this.subscriptions.set(subId, {
          event,
          cleanup: () => {
            this.cdp.off('DOM.attributeModified', handler);
            this.cdp.off('DOM.attributeRemoved', handler);
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
      case 'dataChanged': {
        await this.cdp.evaluate(`
          (() => {
            if (window.__uab_data_observer) return;
            window.__uab_data_observer = new MutationObserver((mutations) => {
              console.log('__UAB_DATA_CHANGE__', JSON.stringify(mutations.length));
            });
            window.__uab_data_observer.observe(document.body, {
              attributes: true, childList: true, subtree: true, characterData: true,
            });
          })()
        `);
        const consoleHandler = (params: Record<string, unknown>) => {
          const args = (params as Record<string, unknown> & { args?: Array<{ value?: string }> }).args || [];
          if (args[0]?.value?.includes('__UAB_DATA_CHANGE__')) {
            callback({ type: 'dataChanged', timestamp: Date.now() });
          }
        };
        await this.cdp.send('Runtime.enable');
        this.cdp.on('Runtime.consoleAPICalled', consoleHandler);
        this.subscriptions.set(subId, {
          event,
          cleanup: () => {
            this.cdp.off('Runtime.consoleAPICalled', consoleHandler);
            this.cdp.evaluate('if(window.__uab_data_observer){window.__uab_data_observer.disconnect();delete window.__uab_data_observer;}').catch(() => {});
          },
        });
        break;
      }
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

  // ─── Action Implementations ───────────────────────────────────

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

  // ─── Screenshot via CDP ──────────────────────────────────────

  private async doScreenshot(params?: ActionParams): Promise<ActionResult> {
    try {
      const result = await this.cdp.send('Page.captureScreenshot', { format: 'png', quality: 100 });
      const data = (result as Record<string, unknown>).data as string;
      if (!data) return { success: false, error: 'CDP returned no screenshot data' };

      const outPath = params?.outputPath || `data/screenshots/uab-${this.app.pid}-${Date.now()}.png`;
      mkdirSync(dirname(outPath), { recursive: true });
      writeFileSync(outPath, Buffer.from(data, 'base64'));
      return { success: true, result: outPath };
    } catch (err) {
      return { success: false, error: `Screenshot failed: ${err}` };
    }
  }

  // ─── Keyboard via CDP ──────────────────────────────────────

  private async doKeypress(params?: ActionParams): Promise<ActionResult> {
    const key = params?.key || params?.text || '';
    if (!key) return { success: false, error: 'No key provided' };

    try {
      const cdpKey = this.mapKeyToCDP(key);
      await this.cdp.send('Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: cdpKey.key,
        code: cdpKey.code,
        windowsVirtualKeyCode: cdpKey.keyCode,
        nativeVirtualKeyCode: cdpKey.keyCode,
      });
      await this.cdp.send('Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: cdpKey.key,
        code: cdpKey.code,
        windowsVirtualKeyCode: cdpKey.keyCode,
        nativeVirtualKeyCode: cdpKey.keyCode,
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
      // Build modifier flags
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

      // Press modifier keys down
      for (const modKey of modKeyNames) {
        await this.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyDown', key: modKey, modifiers,
        });
      }

      // Press and release each non-modifier key
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

      // Release modifier keys
      for (const modKey of modKeyNames.reverse()) {
        await this.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyUp', key: modKey, modifiers: 0,
        });
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
      f1: { key: 'F1', code: 'F1', keyCode: 112 },
      f2: { key: 'F2', code: 'F2', keyCode: 113 },
      f3: { key: 'F3', code: 'F3', keyCode: 114 },
      f4: { key: 'F4', code: 'F4', keyCode: 115 },
      f5: { key: 'F5', code: 'F5', keyCode: 116 },
      f6: { key: 'F6', code: 'F6', keyCode: 117 },
      f7: { key: 'F7', code: 'F7', keyCode: 118 },
      f8: { key: 'F8', code: 'F8', keyCode: 119 },
      f9: { key: 'F9', code: 'F9', keyCode: 120 },
      f10: { key: 'F10', code: 'F10', keyCode: 121 },
      f11: { key: 'F11', code: 'F11', keyCode: 122 },
      f12: { key: 'F12', code: 'F12', keyCode: 123 },
    };

    if (CDP_KEY_MAP[lower]) return CDP_KEY_MAP[lower];

    // Single character — map to key code
    if (key.length === 1) {
      const code = key >= 'a' && key <= 'z' ? `Key${key.toUpperCase()}` :
                   key >= 'A' && key <= 'Z' ? `Key${key}` :
                   key >= '0' && key <= '9' ? `Digit${key}` : '';
      const keyCode = key.toUpperCase().charCodeAt(0);
      return { key, code, keyCode };
    }

    return { key, code: key, keyCode: 0 };
  }

  // ─── Query Helpers ────────────────────────────────────────────

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

  // ─── Window Management via Win32 API ─────────────────────────

  private async doWindowAction(action: 'minimize' | 'maximize' | 'restore' | 'close'): Promise<ActionResult> {
    try {
      const actionMap: Record<string, string> = {
        minimize: '$SW_MINIMIZE = 6; [Win32]::ShowWindow($hWnd, $SW_MINIMIZE) | Out-Null',
        maximize: '$SW_MAXIMIZE = 3; [Win32]::ShowWindow($hWnd, $SW_MAXIMIZE) | Out-Null',
        restore: '$SW_RESTORE = 9; [Win32]::ShowWindow($hWnd, $SW_RESTORE) | Out-Null',
        close: '[Win32]::PostMessage($hWnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null',
      };
      const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32 {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
}
'@ -ErrorAction SilentlyContinue

$targetPid = ${this.app.pid}
$hWnd = [IntPtr]::Zero
[Win32]::EnumWindows({
  param($hwnd, $lparam)
  $pid = 0
  [Win32]::GetWindowThreadProcessId($hwnd, [ref]$pid) | Out-Null
  if ($pid -eq $targetPid -and [Win32]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero)

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found for PID ${this.app.pid}' } | ConvertTo-Json -Compress
} else {
  ${actionMap[action]}
  @{ success = $true; action = '${action}'; pid = $targetPid } | ConvertTo-Json -Compress
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
public class Win32Move {
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
[Win32Move]::EnumWindows({
  param($hwnd, $lparam)
  $pid = 0
  [Win32Move]::GetWindowThreadProcessId($hwnd, [ref]$pid) | Out-Null
  if ($pid -eq $targetPid -and [Win32Move]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero)

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found' } | ConvertTo-Json -Compress
} else {
  $rect = New-Object Win32Move+RECT
  [Win32Move]::GetWindowRect($hWnd, [ref]$rect) | Out-Null
  $w = $rect.Right - $rect.Left
  $h = $rect.Bottom - $rect.Top
  $SWP_NOSIZE = 0x0001; $SWP_NOZORDER = 0x0004
  [Win32Move]::SetWindowPos($hWnd, [IntPtr]::Zero, ${x}, ${y}, $w, $h, ($SWP_NOSIZE -bor $SWP_NOZORDER)) | Out-Null
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
public class Win32Resize {
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
[Win32Resize]::EnumWindows({
  param($hwnd, $lparam)
  $pid = 0
  [Win32Resize]::GetWindowThreadProcessId($hwnd, [ref]$pid) | Out-Null
  if ($pid -eq $targetPid -and [Win32Resize]::IsWindowVisible($hwnd)) {
    $script:hWnd = $hwnd
    return $false
  }
  return $true
}, [IntPtr]::Zero)

if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No visible window found' } | ConvertTo-Json -Compress
} else {
  $rect = New-Object Win32Resize+RECT
  [Win32Resize]::GetWindowRect($hWnd, [ref]$rect) | Out-Null
  $SWP_NOMOVE = 0x0002; $SWP_NOZORDER = 0x0004
  [Win32Resize]::SetWindowPos($hWnd, [IntPtr]::Zero, $rect.Left, $rect.Top, ${w}, ${h}, ($SWP_NOMOVE -bor $SWP_NOZORDER)) | Out-Null
  @{ success = $true; width = ${w}; height = ${h} } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 10000) as { success: boolean; error?: string };
      return result.success ? { success: true, result: { width: w, height: h } } : { success: false, error: result.error || 'Resize failed' };
    } catch (err) {
      return { success: false, error: `Window resize failed: ${err}` };
    }
  }

  private ensureConnected(): void {
    if (!this.connected) throw new Error(`Not connected to ${this.app.name}`);
  }
}
