import test from 'node:test';
import assert from 'node:assert/strict';

import { PluginManager } from '../dist/plugins/base.js';
import { ControlRouter } from '../dist/router.js';
import { ExtensionWSServer } from '../dist/plugins/chrome-ext/ws-server.js';
import { ChromeExtPlugin } from '../dist/plugins/chrome-ext/index.js';
import { BrowserPlugin } from '../dist/plugins/browser/index.js';
import { ElectronPlugin } from '../dist/plugins/electron/index.js';
import { OfficePlugin } from '../dist/plugins/office/index.js';
import { DirectApiPlugin } from '../dist/plugins/direct-api/index.js';
import { QtPlugin } from '../dist/plugins/qt/index.js';
import { GtkPlugin } from '../dist/plugins/gtk/index.js';
import { JavaPlugin } from '../dist/plugins/java/index.js';
import { FlutterPlugin } from '../dist/plugins/flutter/index.js';
import { WinUIAPlugin } from '../dist/plugins/win-uia/index.js';

process.env.ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || 'test-key';

function buildRealRouter() {
  const manager = new PluginManager();
  const extensionServer = new ExtensionWSServer(0);
  manager.register(new ChromeExtPlugin(extensionServer));
  manager.register(new BrowserPlugin());
  manager.register(new ElectronPlugin());
  manager.register(new OfficePlugin());
  manager.register(new QtPlugin());
  manager.register(new GtkPlugin());
  manager.register(new JavaPlugin());
  manager.register(new FlutterPlugin());
  manager.register(new WinUIAPlugin());
  return new ControlRouter(manager);
}

test('router exposes framework-specific method cascade for claimed hooks', () => {
  const router = buildRealRouter();

  assert.deepEqual(
    router.describeAvailableMethods({ pid: 1, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 }),
    ['browser-cdp', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 2, name: 'Slack', path: 'slack.exe', framework: 'electron', confidence: 1 }),
    ['electron-cdp', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 3, name: 'Excel', path: 'excel.exe', framework: 'office', confidence: 1 }),
    ['office-com+uia', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 4, name: 'Qt App', path: 'qt.exe', framework: 'qt6', confidence: 1 }),
    ['qt-uia', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 5, name: 'GTK App', path: 'gtk.exe', framework: 'gtk3', confidence: 1 }),
    ['gtk-uia', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 6, name: 'IDEA', path: 'idea64.exe', framework: 'java-swing', confidence: 1 }),
    ['java-jab-uia', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 7, name: 'Flutter App', path: 'flutter.exe', framework: 'flutter', confidence: 1 }),
    ['flutter-uia', 'win-uia', 'vision'],
  );
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 8, name: 'Unknown App', path: 'unknown.exe', framework: 'unknown', confidence: 0.5 }),
    ['win-uia', 'vision'],
  );
});

test('router prioritizes direct-api ahead of framework hooks when an app exposes one', () => {
  const manager = new PluginManager();
  manager.register(new DirectApiPlugin());
  manager.register(new BrowserPlugin());
  manager.register(new WinUIAPlugin());

  const router = new ControlRouter(manager);
  assert.deepEqual(
    router.describeAvailableMethods({
      pid: 10,
      name: 'Browser with API',
      path: 'browser-api.exe',
      framework: 'browser',
      confidence: 1,
      connectionInfo: {
        directApi: { baseUrl: 'http://127.0.0.1:33121' },
      },
    }),
    ['direct-api', 'browser-cdp', 'win-uia', 'vision'],
  );
});

test('router includes chrome-extension hook when the extension bridge is connected', () => {
  const extensionServer = new ExtensionWSServer(0);
  Object.defineProperty(extensionServer, 'connected', { get: () => true });

  const manager = new PluginManager();
  manager.register(new ChromeExtPlugin(extensionServer));
  manager.register(new BrowserPlugin());
  manager.register(new WinUIAPlugin());

  const router = new ControlRouter(manager);
  assert.deepEqual(
    router.describeAvailableMethods({ pid: 9, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 }),
    ['chrome-extension', 'browser-cdp', 'win-uia', 'vision'],
  );
});

test('router falls through from one framework hook to the next documented hook', async () => {
  class FakeConnection {
    constructor(app) {
      this.app = app;
      this.connected = true;
    }
    async enumerate() { return []; }
    async query() { return []; }
    async act() { return { success: true }; }
    async state() { return { window: { title: 'x', size: { width: 1, height: 1 }, position: { x: 0, y: 0 }, focused: true }, modals: [], menus: [] }; }
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; }
    async disconnect() {}
  }

  class FailingBrowserHook {
    framework = 'browser';
    name = 'Failing Chrome Extension';
    controlMethod = 'chrome-extension';
    canHandle(app) { return app.framework === 'browser'; }
    async connect() { throw new Error('extension unavailable'); }
  }

  class WorkingBrowserHook {
    framework = 'browser';
    name = 'Working Browser CDP';
    controlMethod = 'browser-cdp';
    canHandle(app) { return app.framework === 'browser'; }
    async connect(app) { return new FakeConnection(app); }
  }

  const manager = new PluginManager();
  manager.register(new FailingBrowserHook());
  manager.register(new WorkingBrowserHook());

  const router = new ControlRouter(manager);
  const conn = await router.connect({ pid: 42, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 });

  assert.equal(conn.method, 'browser-cdp');
});

test('router falls through from framework hooks to win-uia when structured hooks are unavailable', async () => {
  class FakeConnection {
    constructor(app) {
      this.app = app;
      this.connected = true;
    }
    async enumerate() { return []; }
    async query() { return []; }
    async act() { return { success: true, backend: 'win-uia' }; }
    async state() { return { window: { title: 'fallback', size: { width: 1, height: 1 }, position: { x: 0, y: 0 }, focused: true }, modals: [], menus: [] }; }
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; }
    async disconnect() {}
  }

  class FailingChromeExtension {
    framework = 'browser';
    name = 'Failing Chrome Extension';
    controlMethod = 'chrome-extension';
    canHandle(app) { return app.framework === 'browser'; }
    async connect() { throw new Error('extension unavailable'); }
  }

  class FailingBrowserCdp {
    framework = 'browser';
    name = 'Failing Browser CDP';
    controlMethod = 'browser-cdp';
    canHandle(app) { return app.framework === 'browser'; }
    async connect() { throw new Error('cdp unavailable'); }
  }

  const manager = new PluginManager();
  manager.register(new FailingChromeExtension());
  manager.register(new FailingBrowserCdp());

  const router = new ControlRouter(manager);
  router.uiaFallback = {
    controlMethod: 'win-uia',
    canHandle: () => true,
    connect: async (app) => new FakeConnection(app),
  };
  router.visionFallback = {
    controlMethod: 'vision',
    canHandle: () => false,
    connect: async () => { throw new Error('vision unavailable'); },
  };

  const conn = await router.connect({ pid: 43, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 });

  assert.equal(conn.method, 'win-uia');
});

test('router falls through to vision when framework hooks and win-uia are unavailable', async () => {
  class FakeConnection {
    constructor(app) {
      this.app = app;
      this.connected = true;
    }
    async enumerate() { return []; }
    async query() { return []; }
    async act() { return { success: true, backend: 'vision' }; }
    async state() { return { window: { title: 'vision', size: { width: 1, height: 1 }, position: { x: 0, y: 0 }, focused: true }, modals: [], menus: [] }; }
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; }
    async disconnect() {}
  }

  class FailingBrowserHook {
    framework = 'browser';
    name = 'Failing Browser Hook';
    controlMethod = 'browser-cdp';
    canHandle(app) { return app.framework === 'browser'; }
    async connect() { throw new Error('cdp unavailable'); }
  }

  const manager = new PluginManager();
  manager.register(new FailingBrowserHook());

  const router = new ControlRouter(manager);
  router.uiaFallback = {
    controlMethod: 'win-uia',
    canHandle: () => true,
    connect: async () => { throw new Error('uia unavailable'); },
  };
  router.visionFallback = {
    controlMethod: 'vision',
    canHandle: () => true,
    connect: async (app) => new FakeConnection(app),
  };

  const conn = await router.connect({ pid: 44, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 });

  assert.equal(conn.method, 'vision');
});

test('routed connection replays mutating actions on the next fallback when the primary method fails', async () => {
  class FailingConnection {
    constructor(app) {
      this.app = app;
      this.connected = true;
      this.disconnectCalls = 0;
      this.actCalls = 0;
    }
    async enumerate() { return []; }
    async query() { return []; }
    async act() {
      this.actCalls += 1;
      return { success: false, error: 'primary failed' };
    }
    async state() { return { window: { title: 'primary', size: { width: 1, height: 1 }, position: { x: 0, y: 0 }, focused: true }, modals: [], menus: [] }; }
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; }
    async disconnect() { this.disconnectCalls += 1; }
  }

  class WorkingConnection {
    constructor(app) {
      this.app = app;
      this.connected = true;
      this.actCalls = 0;
    }
    async enumerate() { return []; }
    async query() { return []; }
    async act(elementId, action, params) {
      this.actCalls += 1;
      return { success: true, backend: 'win-uia', elementId, action, params };
    }
    async state() { return { window: { title: 'fallback', size: { width: 1, height: 1 }, position: { x: 0, y: 0 }, focused: true }, modals: [], menus: [] }; }
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; }
    async disconnect() {}
  }

  class WorkingBrowserHook {
    framework = 'browser';
    name = 'Working Browser CDP';
    controlMethod = 'browser-cdp';
    constructor(connection) {
      this.connection = connection;
    }
    canHandle(app) { return app.framework === 'browser'; }
    async connect() { return this.connection; }
  }

  const primaryConnection = new FailingConnection({ pid: 45, name: 'Chrome' });
  const fallbackConnection = new WorkingConnection({ pid: 45, name: 'Chrome' });

  const manager = new PluginManager();
  manager.register(new WorkingBrowserHook(primaryConnection));

  const router = new ControlRouter(manager);
  router.uiaFallback = {
    controlMethod: 'win-uia',
    canHandle: () => true,
    connect: async () => fallbackConnection,
  };
  router.visionFallback = {
    controlMethod: 'vision',
    canHandle: () => false,
    connect: async () => { throw new Error('vision unavailable'); },
  };

  const conn = await router.connect({ pid: 45, name: 'Chrome', path: 'chrome.exe', framework: 'browser', confidence: 1 });
  const result = await conn.act('field-1', 'click', { x: 1, y: 2 });

  assert.equal(primaryConnection.actCalls, 1);
  assert.equal(primaryConnection.disconnectCalls, 1);
  assert.equal(fallbackConnection.actCalls, 1);
  assert.equal(conn.method, 'win-uia');
  assert.equal(result.success, true);
  assert.equal(result.backend, 'win-uia');
});
