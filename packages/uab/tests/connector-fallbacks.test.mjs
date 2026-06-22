import test from 'node:test';
import assert from 'node:assert/strict';

import { UABConnector } from '../dist/connector.js';
import { PluginManager } from '../dist/plugins/base.js';
import { ControlRouter } from '../dist/router.js';
import { signUABAuthorityGrant } from '../dist/governance/grants.js';

const SECRET = 'connector-fallback-test-secret-not-production';

function makeGrant(action, overrides = {}) {
  const grant = {
    grant_id: `grant-${action}`,
    issued_at: '2026-06-17T00:00:00.000Z',
    expires_at: '2999-01-01T00:00:00.000Z',
    nonce: '123e4567-e89b-42d3-a456-426614174111',
    risk_tier: action === 'screenshot' ? 'T0/T1' : 'T1/T2',
    uab_risk: action === 'screenshot' ? 'safe' : 'moderate',
    capability: 'desktop.control',
    app_name: 'Chrome',
    app_pid: 9901,
    action,
    selector_scope: '',
    sensitive_read: action === 'screenshot',
    mutating: action !== 'screenshot',
    destructive: false,
    external_submission: false,
    credential_sensitive: false,
    policy_version: 'test',
    soul_version: 'test',
    workflow_id: 'workflow-test',
    run_id: 'run-test',
    parent_receipt_id: null,
    approval_id: 'approval-test',
    ...overrides,
  };
  grant.signature = signUABAuthorityGrant(grant, SECRET);
  return grant;
}

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

function createApp() {
  return {
    pid: 9901,
    name: 'Chrome',
    path: 'chrome.exe',
    framework: 'browser',
    confidence: 1,
  };
}

async function createInjectedConnector(primaryConnection, fallbackConnection) {
  const manager = new PluginManager();
  manager.register(new PrimaryBrowserPlugin(primaryConnection));

  const router = new ControlRouter(manager);
  router.uiaFallback = {
    controlMethod: 'win-uia',
    canHandle: () => true,
    connect: async (app) => {
      fallbackConnection.app = app;
      fallbackConnection.connected = true;
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

  const connector = new UABConnector({
    persistent: false,
    extensionBridge: false,
    loadProfiles: false,
    authoritySecret: SECRET,
  });

  await connector.start();
  connector.router = router;
  connector.registry.register(createApp());
  await connector.connect(9901);

  return { connector, router };
}

test('connector state falls through to the next route when the primary connection fails', async () => {
  const primaryConnection = {
    app: createApp(),
    connected: true,
    disconnectCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() { throw new Error('primary state failed'); },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() { this.disconnectCalls += 1; },
  };
  const fallbackConnection = {
    app: createApp(),
    connected: true,
    stateCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true, backend: 'win-uia' }; },
    async state() {
      this.stateCalls += 1;
      return {
        window: {
          title: 'fallback-state',
          size: { width: 100, height: 50 },
          position: { x: 10, y: 20 },
          focused: true,
        },
        modals: [],
        menus: [],
      };
    },
    async subscribe() { return { id: 'noop', event: 'stateChanged', unsubscribe() {} }; },
    async disconnect() {},
  };

  const { connector, router } = await createInjectedConnector(primaryConnection, fallbackConnection);

  try {
    const state = await connector.state(9901);
    assert.equal(state.window.title, 'fallback-state');
    assert.equal(primaryConnection.disconnectCalls, 1);
    assert.equal(fallbackConnection.stateCalls, 1);
    assert.equal(router.getRoute(9901)?.method, 'win-uia');
  } finally {
    await connector.stop();
  }
});

test('connector replays mutating actions on the fallback route through the public API', async () => {
  const primaryConnection = {
    app: createApp(),
    connected: true,
    disconnectCalls: 0,
    actCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act() {
      this.actCalls += 1;
      return { success: false, error: 'primary act failed' };
    },
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
    async disconnect() { this.disconnectCalls += 1; },
  };
  const fallbackConnection = {
    app: createApp(),
    connected: true,
    actCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act(elementId, action, params) {
      this.actCalls += 1;
      return { success: true, backend: 'win-uia', elementId, action, params };
    },
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

  const { connector, router } = await createInjectedConnector(primaryConnection, fallbackConnection);

  try {
    const result = await connector.act(9901, 'field-1', 'click', {
      x: 1,
      y: 2,
      uabAuthorityGrant: makeGrant('click', { selector_scope: 'field-1' }),
    });
    assert.equal(result.success, true);
    assert.equal(result.backend, 'win-uia');
    assert.equal(primaryConnection.actCalls, 1);
    assert.equal(primaryConnection.disconnectCalls, 1);
    assert.equal(fallbackConnection.actCalls, 1);
    assert.equal(router.getRoute(9901)?.method, 'win-uia');
  } finally {
    await connector.stop();
  }
});

test('connector screenshot uses the fallback-capable route wrapper', async () => {
  const primaryConnection = {
    app: createApp(),
    connected: true,
    disconnectCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act(_elementId, action) {
      if (action === 'screenshot') {
        throw new Error('primary screenshot failed');
      }
      return { success: true };
    },
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
    async disconnect() { this.disconnectCalls += 1; },
  };
  const fallbackConnection = {
    app: createApp(),
    connected: true,
    screenshotCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act(_elementId, action, params) {
      if (action === 'screenshot') {
        this.screenshotCalls += 1;
        return {
          success: true,
          path: params?.outputPath,
          data: 'ZmFrZS1wbmc=',
          backend: 'win-uia',
        };
      }
      return { success: true };
    },
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

  const { connector, router } = await createInjectedConnector(primaryConnection, fallbackConnection);

  try {
    const result = await connector.screenshot(
      9901,
      'data/screenshots/fallback-test.png',
      { uabAuthorityGrant: makeGrant('screenshot') },
    );
    assert.equal(result.success, true);
    assert.equal(result.backend, 'win-uia');
    assert.equal(result.data, 'ZmFrZS1wbmc=');
    assert.equal(primaryConnection.disconnectCalls, 1);
    assert.equal(fallbackConnection.screenshotCalls, 1);
    assert.equal(router.getRoute(9901)?.method, 'win-uia');
  } finally {
    await connector.stop();
  }
});
