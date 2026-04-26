import test from 'node:test';
import assert from 'node:assert/strict';

import { UABServer } from '../dist/server.js';
import { PluginManager } from '../dist/plugins/base.js';
import { ControlRouter } from '../dist/router.js';

test('server info publishes hook, framework signature, and concerto inventory', async () => {
  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
    },
  });

  await server.start();
  try {
    const response = await fetch(`${server.address}/info`);
    assert.equal(response.status, 200);
    const payload = await response.json();

    assert.equal(payload.name, 'Universal App Bridge Server');
    assert.ok(Array.isArray(payload.frameworkHooks));
    assert.ok(Array.isArray(payload.frameworkSignatures));
    assert.ok(Array.isArray(payload.concertoMethods));
    assert.ok(payload.frameworkHooks.some(item => item.id === 'office-com+uia'));
    assert.ok(payload.frameworkSignatures.some(item => item.framework === 'electron'));
    assert.ok(payload.concertoMethods.some(item => item.id === 'keyboard-native'));
  } finally {
    await server.stop();
  }
});

test('server exposes excel benchmark dry-run output', async () => {
  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
    },
  });

  await server.start();
  try {
    const response = await fetch(`${server.address}/excel/benchmark`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        rowCount: 250,
        outputPath: 'data/uab-benchmarks/server-test.xlsx',
        manifestPath: 'data/uab-benchmarks/server-proof.json',
        dryRun: true,
      }),
    });
    assert.equal(response.status, 200);
    const payload = await response.json();

    assert.equal(payload.dryRun, true);
    assert.equal(payload.options.rowCount, 250);
    assert.equal(payload.options.outputPath, 'data/uab-benchmarks/server-test.xlsx');
    assert.equal(payload.options.manifestPath, 'data/uab-benchmarks/server-proof.json');
    assert.match(payload.resolvedPaths.outputPath, /data[\\/]+uab-benchmarks[\\/]+server-test\.xlsx$/);
    assert.match(payload.resolvedPaths.manifestPath, /data[\\/]+uab-benchmarks[\\/]+server-proof\.json$/);
    assert.match(payload.script, /Workbooks\.Add/);
    assert.match(payload.script, /PivotCaches\(\)\.Create/);
    assert.match(payload.script, /ChartObjects\(\)\.Add/);
    assert.match(payload.script, /verify-artifact/);
    assert.match(payload.script, /Workbooks\.Open/);
  } finally {
    await server.stop();
  }
});

test('server exposes excel probe output', async () => {
  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
    },
  });

  await server.start();
  try {
    const response = await fetch(`${server.address}/excel/probe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    assert.equal(response.status, 200);
    const payload = await response.json();

    assert.equal(typeof payload.available, 'boolean');
    if (payload.error !== undefined) {
      assert.equal(typeof payload.error, 'string');
    }
  } finally {
    await server.stop();
  }
});

test('server plan endpoint accepts runtime context and changes the selected method', async () => {
  class PrimaryBrowserPlugin {
    framework = 'browser';
    name = 'Primary Browser Hook';
    controlMethod = 'browser-cdp';

    constructor(connection) {
      this.connection = connection;
    }

    canHandle(app) {
      return app.framework === 'browser';
    }

    async connect(app) {
      this.connection.app = app;
      this.connection.connected = true;
      return this.connection;
    }
  }

  const primaryConnection = {
    connected: true,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() {
      return {
        window: {
          title: 'primary-state',
          size: { width: 1, height: 1 },
          position: { x: 0, y: 0 },
          focused: true,
        },
        modals: [],
        menus: [],
      };
    },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() {},
  };
  const fallbackConnection = {
    connected: true,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() {
      return {
        window: {
          title: 'fallback-state',
          size: { width: 1, height: 1 },
          position: { x: 0, y: 0 },
          focused: true,
        },
        modals: [],
        menus: [],
      };
    },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() {},
  };

  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
    },
  });

  await server.start();
  try {
    const manager = new PluginManager();
    manager.register(new PrimaryBrowserPlugin(primaryConnection));

    const router = new ControlRouter(manager);
    router.uiaFallback = {
      controlMethod: 'win-uia',
      canHandle: () => true,
      connect: async (app) => {
        fallbackConnection.app = app;
        return fallbackConnection;
      },
    };
    router.visionFallback = {
      controlMethod: 'vision',
      canHandle: () => true,
      connect: async (app) => {
        return {
          ...fallbackConnection,
          app,
        };
      },
    };

    server.connector.router = router;
    server.connector.registry.register({
      pid: 9901,
      name: 'Chrome',
      path: 'chrome.exe',
      framework: 'browser',
      confidence: 1,
    });

    const connectResponse = await fetch(`${server.address}/connect`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ target: 9901 }),
    });
    assert.equal(connectResponse.status, 200);

    const response = await fetch(`${server.address}/plan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        pid: 9901,
        action: 'describe',
        context: {
          preferredRole: 'connection',
          preferredControl: 'precise',
          weights: {
            speed: 2,
            outcome: 4,
            control: 4,
            cost: 4,
            role: 2,
            affinity: 0,
          },
          note: 'Prefer structured low-cost inspection over API-backed vision.',
        },
      }),
    });
    assert.equal(response.status, 200);
    const payload = await response.json();

    assert.equal(payload.plan.primaryMethod, 'browser-cdp');
    assert.match(payload.plan.rationale, /Runtime context:/);
  } finally {
    await server.stop();
  }
});

test('server plan endpoint injects live runtime evidence from the connected route', async () => {
  class PrimaryBrowserPlugin {
    framework = 'browser';
    name = 'Primary Browser Hook';
    controlMethod = 'browser-cdp';

    constructor(connection) {
      this.connection = connection;
    }

    canHandle(app) {
      return app.framework === 'browser';
    }

    async connect(app) {
      this.connection.app = app;
      this.connection.connected = true;
      return this.connection;
    }
  }

  const primaryConnection = {
    connected: true,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() {
      return {
        window: {
          title: 'primary-state',
          size: { width: 1, height: 1 },
          position: { x: 0, y: 0 },
          focused: true,
        },
        modals: [],
        menus: [],
      };
    },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() {},
  };
  const fallbackConnection = {
    connected: true,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() {
      return {
        window: {
          title: 'fallback-state',
          size: { width: 1, height: 1 },
          position: { x: 0, y: 0 },
          focused: true,
        },
        modals: [],
        menus: [],
      };
    },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() {},
  };

  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
    },
  });

  await server.start();
  try {
    const manager = new PluginManager();
    manager.register(new PrimaryBrowserPlugin(primaryConnection));

    const router = new ControlRouter(manager);
    router.uiaFallback = {
      controlMethod: 'win-uia',
      canHandle: () => true,
      connect: async (app) => {
        fallbackConnection.app = app;
        return fallbackConnection;
      },
    };
    router.visionFallback = {
      controlMethod: 'vision',
      canHandle: () => false,
      connect: async () => {
        throw new Error('vision unavailable');
      },
    };

    server.connector.router = router;
    server.connector.connectionMgr = {
      track() {},
      get() {
        return {
          healthFailures: 2,
          reconnectAttempts: 1,
        };
      },
      async shutdown() {},
    };
    server.connector.registry.register({
      pid: 9902,
      name: 'Chrome',
      path: 'chrome.exe',
      framework: 'browser',
      confidence: 1,
    });

    const connectResponse = await fetch(`${server.address}/connect`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ target: 9902 }),
    });
    assert.equal(connectResponse.status, 200);

    const response = await fetch(`${server.address}/plan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        pid: 9902,
        action: 'click',
      }),
    });
    assert.equal(response.status, 200);
    const payload = await response.json();

    assert.equal(payload.plan.primaryMethod, 'win-uia');
    assert.ok(Array.isArray(payload.plan.runtimeEvidence));
    assert.ok(payload.plan.runtimeEvidence.some((item) => /already connected/i.test(item)));
    assert.ok(payload.plan.runtimeEvidence.some((item) => /health failure/i.test(item) || /active route is penalized/i.test(item)));
  } finally {
    await server.stop();
  }
});
