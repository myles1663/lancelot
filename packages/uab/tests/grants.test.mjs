import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';

import { ChainExecutor } from '../dist/chains.js';
import { UABConnector } from '../dist/connector.js';
import { PermissionManager } from '../dist/permissions.js';
import { PluginManager } from '../dist/plugins/base.js';
import { ControlRouter } from '../dist/router.js';
import { UABServer } from '../dist/server.js';
import { UABService } from '../dist/service.js';
import { validateUABAuthorityGrant } from '../dist/governance/grants.js';

const SECRET = 'uab-a3-test-secret-not-production';
const BASE_NOW = new Date('2026-06-17T12:00:00.000Z');
let grantSequence = 0;

function canonicalize(value) {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item)).join(',')}]`;
  }
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
    .join(',')}}`;
}

function signGrant(grant, secret = SECRET) {
  const payload = {};
  for (const [key, value] of Object.entries(grant)) {
    if (key !== 'signature') {
      payload[key] = value;
    }
  }
  return createHmac('sha256', secret).update(canonicalize(payload), 'utf8').digest('hex');
}

function makeGrant(overrides = {}) {
  grantSequence += 1;
  const defaultNonceSuffix = String(426614174000 + grantSequence).padStart(12, '0');
  const grant = {
    grant_id: `grant-uab-a3-click-${grantSequence}`,
    issued_at: '2026-06-17T11:59:30.000Z',
    expires_at: '2999-01-01T00:00:00.000Z',
    nonce: `123e4567-e89b-42d3-a456-${defaultNonceSuffix}`,
    risk_tier: 'T1/T2',
    uab_risk: 'moderate',
    capability: 'desktop.control',
    app_name: 'KnownGoodApp',
    app_pid: 4242,
    action: 'click',
    selector_scope: 'button#save',
    sensitive_read: false,
    mutating: true,
    destructive: false,
    external_submission: false,
    credential_sensitive: false,
    policy_version: 'uab-a3-policy',
    soul_version: 'uab-a3-soul',
    workflow_id: 'workflow-uab-a3',
    run_id: 'run-uab-a3',
    parent_receipt_id: null,
    approval_id: 'approval-uab-a3',
    ...overrides,
  };
  grant.signature = signGrant(grant);
  return grant;
}

function makeActionGrant(action, overrides = {}) {
  const riskByAction = {
    click: { risk_tier: 'T1/T2', uab_risk: 'moderate', mutating: true, destructive: false },
    hotkey: { risk_tier: 'T1/T2', uab_risk: 'moderate', mutating: true, destructive: false },
    close: { risk_tier: 'T3', uab_risk: 'destructive', mutating: true, destructive: true },
    sendEmail: {
      risk_tier: 'T3',
      uab_risk: 'destructive',
      mutating: true,
      destructive: true,
      external_submission: true,
    },
    setCookie: {
      risk_tier: 'T1/T2',
      uab_risk: 'moderate',
      mutating: true,
      destructive: false,
      credential_sensitive: true,
    },
    screenshot: {
      risk_tier: 'T0/T1',
      uab_risk: 'safe',
      mutating: false,
      destructive: false,
      sensitive_read: true,
    },
  };
  return makeGrant({
    ...(riskByAction[action] ?? {}),
    action,
    selector_scope: '',
    ...overrides,
  });
}

function grantContext(overrides = {}) {
  return {
    appName: 'KnownGoodApp',
    appPid: 4242,
    action: 'click',
    selectorScope: 'button#save',
    expectedFlags: {
      sensitive_read: false,
      mutating: true,
      destructive: false,
      external_submission: false,
      credential_sensitive: false,
    },
    now: BASE_NOW,
    ...overrides,
  };
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
    pid: 4242,
    name: 'KnownGoodApp',
    path: 'known-good.exe',
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
  await connector.connect(4242);
  return { connector, router };
}

test('UAB authority grants validate signature, scope, expiry, nonce, and flags', () => {
  const valid = validateUABAuthorityGrant(makeGrant(), SECRET, grantContext());
  assert.equal(valid.valid, true);
  assert.equal(valid.reasonCode, 'valid');

  const tampered = makeGrant();
  tampered.capability = 'desktop.admin';
  assert.equal(validateUABAuthorityGrant(tampered, SECRET, grantContext()).reasonCode, 'invalid_signature');

  assert.equal(
    validateUABAuthorityGrant(makeGrant({ expires_at: '2026-06-17T11:59:59.000Z' }), SECRET, grantContext()).reasonCode,
    'grant_expired',
  );
  assert.equal(
    validateUABAuthorityGrant(makeGrant({ app_pid: 9999 }), SECRET, grantContext()).reasonCode,
    'wrong_pid',
  );
  assert.equal(
    validateUABAuthorityGrant(makeGrant({ action: 'close', destructive: true }), SECRET, grantContext()).reasonCode,
    'wrong_action',
  );
  assert.equal(
    validateUABAuthorityGrant(makeGrant({ selector_scope: 'button#delete' }), SECRET, grantContext()).reasonCode,
    'wrong_selector_scope',
  );

  const missingSignature = makeGrant();
  delete missingSignature.signature;
  assert.equal(validateUABAuthorityGrant(missingSignature, SECRET, grantContext()).reasonCode, 'missing_signature');

  const unknownRisk = makeGrant({ uab_risk: 'critical' });
  unknownRisk.signature = signGrant(unknownRisk);
  assert.equal(validateUABAuthorityGrant(unknownRisk, SECRET, grantContext()).reasonCode, 'unknown_uab_risk');

  const invalidNonce = makeGrant({ nonce: 'not-a-uuid' });
  invalidNonce.signature = signGrant(invalidNonce);
  assert.equal(validateUABAuthorityGrant(invalidNonce, SECRET, grantContext()).reasonCode, 'invalid_nonce');

  const wrongFlag = makeGrant({ mutating: false });
  wrongFlag.signature = signGrant(wrongFlag);
  assert.equal(validateUABAuthorityGrant(wrongFlag, SECRET, grantContext()).reasonCode, 'flag_mismatch');
});

test('PermissionManager requires grants for governed actions and allows safe non-sensitive reads', () => {
  const permissions = new PermissionManager({
    authoritySecret: SECRET,
    exemptPids: new Set([4242]),
  });
  const app = createApp();

  for (const action of ['click', 'close', 'sendEmail', 'setCookie', 'screenshot']) {
    const missing = permissions.check(4242, action, app);
    assert.equal(missing.allowed, false, `${action} should require a grant`);
    assert.equal(missing.reasonCode, 'missing_authority_grant');
  }

  assert.equal(permissions.check(4242, 'getTabs', app).allowed, true);

  const valid = permissions.check(
    4242,
    'click',
    app,
    { uabAuthorityGrant: makeGrant() },
    'button#save',
  );
  assert.equal(valid.allowed, true);

  const tamperedGrant = makeGrant();
  tamperedGrant.capability = 'desktop.admin';
  const tampered = permissions.check(
    4242,
    'click',
    app,
    { uabAuthorityGrant: tamperedGrant },
    'button#save',
  );
  assert.equal(tampered.allowed, false);
  assert.match(tampered.reason, /invalid_signature/);

  const wrongSelector = permissions.check(
    4242,
    'click',
    app,
    { uabAuthorityGrant: makeGrant() },
    'button#other',
  );
  assert.equal(wrongSelector.allowed, false);
  assert.match(wrongSelector.reason, /wrong_selector_scope/);

  const screenshot = permissions.check(
    4242,
    'screenshot',
    app,
    { uabAuthorityGrant: makeActionGrant('screenshot') },
  );
  assert.equal(screenshot.allowed, true);
});

test('PermissionManager denies replayed authority grant nonce before execution', () => {
  const permissions = new PermissionManager({
    authoritySecret: SECRET,
    exemptPids: new Set([4242]),
  });
  const app = createApp();
  const replayedGrant = makeGrant();

  const first = permissions.check(
    4242,
    'click',
    app,
    { uabAuthorityGrant: replayedGrant },
    'button#save',
  );
  const second = permissions.check(
    4242,
    'click',
    app,
    { uabAuthorityGrant: replayedGrant },
    'button#save',
  );

  assert.equal(first.allowed, true);
  assert.equal(second.allowed, false);
  assert.equal(second.reasonCode, 'replayed_nonce');
  assert.match(second.reason, /replayed_nonce/);
});

test('connector action and fallback replay validate supplied grants before execution', async () => {
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
    receivedParams: undefined,
    async enumerate() { return []; },
    async query() { return []; },
    async act(_elementId, _action, params) {
      this.actCalls += 1;
      this.receivedParams = params;
      return { success: true, backend: 'win-uia' };
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
    const validGrant = makeGrant();
    const result = await connector.act(4242, 'button#save', 'click', { uabAuthorityGrant: validGrant });
    assert.equal(result.success, true);
    assert.equal(result.backend, 'win-uia');
    assert.equal(primaryConnection.actCalls, 1);
    assert.equal(primaryConnection.disconnectCalls, 1);
    assert.equal(fallbackConnection.actCalls, 1);
    assert.equal(fallbackConnection.receivedParams.uabAuthorityGrant.grant_id, validGrant.grant_id);
    assert.equal(router.getRoute(4242)?.method, 'win-uia');

    const tamperedGrant = makeGrant();
    tamperedGrant.capability = 'desktop.admin';
    const denied = await connector.act(4242, 'button#save', 'click', { uabAuthorityGrant: tamperedGrant });
    assert.equal(denied.success, false);
    assert.match(denied.error, /invalid_signature/);

    const missing = await connector.act(4242, 'button#save', 'click');
    assert.equal(missing.success, false);
    assert.match(missing.error, /missing authority grant|required/i);
  } finally {
    await connector.stop();
  }
});

test('server act endpoint propagates JSON params into connector grant validation', async () => {
  const primaryConnection = {
    app: createApp(),
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

  const server = new UABServer({
    port: 0,
    connector: {
      persistent: false,
      extensionBridge: false,
      loadProfiles: false,
      authoritySecret: SECRET,
    },
  });

  await server.start();
  try {
    const manager = new PluginManager();
    manager.register(new PrimaryBrowserPlugin(primaryConnection));
    server.connector.router = new ControlRouter(manager);
    server.connector.registry.register(createApp());

    const connectResponse = await fetch(`${server.address}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: 4242 }),
    });
    assert.equal(connectResponse.status, 200);

    const tamperedGrant = makeGrant();
    tamperedGrant.capability = 'desktop.admin';
    const response = await fetch(`${server.address}/act`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pid: 4242,
        elementId: 'button#save',
        action: 'click',
        params: { uabAuthorityGrant: tamperedGrant },
      }),
    });
    assert.equal(response.status, 200);
    const payload = await response.json();
    assert.equal(payload.success, false);
    assert.match(payload.error, /invalid_signature/);
  } finally {
    await server.stop();
  }
});

test('service act path validates supplied grants before execution', async () => {
  const primaryConnection = {
    app: createApp(),
    connected: true,
    actCalls: 0,
    async enumerate() { return []; },
    async query() { return []; },
    async act() {
      this.actCalls += 1;
      return { success: true };
    },
    async state() {
      return {
        window: {
          title: 'service-state',
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
  const manager = new PluginManager();
  manager.register(new PrimaryBrowserPlugin(primaryConnection));

  const service = new UABService();
  service.router = new ControlRouter(manager);
  service.permissions.options.authoritySecret = SECRET;

  await service.connect(createApp());
  const tamperedGrant = makeGrant();
  tamperedGrant.capability = 'desktop.admin';

  const denied = await service.act(4242, 'button#save', 'click', { uabAuthorityGrant: tamperedGrant });
  assert.equal(denied.success, false);
  assert.match(denied.error, /invalid_signature/);
  assert.equal(primaryConnection.actCalls, 0);

  const missing = await service.act(4242, 'button#save', 'click');
  assert.equal(missing.success, false);
  assert.match(missing.error, /missing authority grant|required/i);
  assert.equal(primaryConnection.actCalls, 0);
});

test('action chains fail denied action results and preserve grants in action step params', async () => {
  const grant = makeGrant();
  const calls = [];
  const executor = new ChainExecutor({
    async query() {
      return [{ id: 'button#save' }];
    },
    async act(pid, elementId, action, params) {
      calls.push({ pid, elementId, action, params });
      return { success: true };
    },
  });

  const result = await executor.execute({
    name: 'grant-propagation-chain',
    pid: 4242,
    stepDelay: 0,
    steps: [
      {
        type: 'action',
        selector: { label: 'Save' },
        action: 'click',
        params: { uabAuthorityGrant: grant },
      },
    ],
  });

  assert.equal(result.success, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.uabAuthorityGrant.grant_id, grant.grant_id);

  const deniedExecutor = new ChainExecutor({
    async query() {
      return [{ id: 'button#save' }];
    },
    async act() {
      return { success: false, error: 'UAB authority grant required for governed action "click"' };
    },
  });
  const denied = await deniedExecutor.execute({
    name: 'missing-grant-chain',
    pid: 4242,
    stepDelay: 0,
    steps: [
      {
        type: 'action',
        selector: { label: 'Save' },
        action: 'click',
      },
    ],
  });

  assert.equal(denied.success, false);
  assert.match(denied.error, /authority grant required/);
});
