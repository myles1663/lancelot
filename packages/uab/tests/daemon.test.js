import assert from 'node:assert/strict';

import { createRpcDispatcher } from '../dist/daemon.js';

function createConnector(overrides = {}) {
  return {
    running: true,
    async start() {},
    async stop() {},
    async scan() { return []; },
    apps() { return []; },
    async find() { return []; },
    async inspectPid() { return null; },
    async connect(target) {
      return {
        pid: typeof target === 'number' ? target : 4242,
        name: typeof target === 'string' ? target : 'Fake App',
        framework: 'browser',
        method: 'uab-hook',
        elementCount: 12,
      };
    },
    async disconnect() {},
    async disconnectAll() {},
    isConnected() { return true; },
    getConnections() {
      return [{ pid: 4242, name: 'Fake App', framework: 'browser', method: 'uab-hook', elementCount: 0 }];
    },
    async enumerate() { return []; },
    async query() { return []; },
    async act() { return { success: true }; },
    async state() { return { window: { title: 'fake' } }; },
    async keypress() { return { success: true }; },
    async hotkey() { return { success: true }; },
    async window() { return { success: true }; },
    async screenshot() { return { success: true, path: 'fake.png' }; },
    cacheStats() { return { hits: 1, misses: 0, hitRate: 1 }; },
    auditLog() { return []; },
    healthSummary() { return [{ pid: 4242, name: 'Fake App', healthy: true, uptimeMs: 1000, failures: 0, method: 'uab-hook' }]; },
    async runHealthChecks() {},
    async spatialMap() { return { rows: [] }; },
    async textMap() { return 'Window: Fake'; },
    async findByDescription() { return []; },
    async focused(pid) { return { pid, name: 'Search', type: 'Edit', automationId: 'search', bounds: { x: 1, y: 2, width: 3, height: 4 }, center: { x: 2.5, y: 4 }, patterns: [], className: 'Edit', path: ['Window', 'Search'], timestamp: 123 }; },
    async findByPath() { return [{ name: 'Save', type: 'Button', automationId: 'save', bounds: { x: 1, y: 2, w: 3, h: 4 }, patterns: 'Invoke' }]; },
    async watchChanges() { return [{ type: 'focus', timestamp: 1 }]; },
    async atomicChain() { return { success: true, stepsCompleted: 2, totalSteps: 2, durationMs: 50, error: '' }; },
    async smartInvoke() { return { success: true, method: 'invoke' }; },
    ...overrides,
  };
}

function createLegacyService(overrides = {}) {
  return {
    running: true,
    async start() {},
    async stop() {},
    async executeChain(chain) {
      return { success: true, chain };
    },
    ...overrides,
  };
}

async function run() {
  {
    const dispatch = createRpcDispatcher({
      connector: createConnector(),
      legacyService: createLegacyService(),
    });

    const result = await dispatch('getStatus', {});
    assert.equal(result.version, '1.3.0');
    assert.equal(result.transport, 'json-rpc-compat');
    assert.equal(result.connectedApps, 1);
    assert.ok(Array.isArray(result.standaloneFeatures));
    assert.match(result.standaloneFeatures.join(','), /focused/);
  }

  {
    const dispatch = createRpcDispatcher({
      connector: createConnector(),
      legacyService: createLegacyService(),
    });

    const result = await dispatch('connect', { name: 'Notepad' });
    assert.deepEqual(result, {
      success: true,
      pid: 4242,
      name: 'Notepad',
      framework: 'browser',
      connectionMethod: 'uab-hook',
      elementCount: 12,
    });
  }

  {
    let received = null;
    const dispatch = createRpcDispatcher({
      connector: createConnector(),
      legacyService: createLegacyService({
        async executeChain(chain) {
          received = chain;
          return { success: true, routed: 'legacy' };
        },
      }),
    });

    const result = await dispatch('chain', { pid: 7, steps: [{ action: 'click' }] });
    assert.deepEqual(received, { pid: 7, steps: [{ action: 'click' }] });
    assert.deepEqual(result, { success: true, routed: 'legacy' });
  }

  {
    const dispatch = createRpcDispatcher({
      connector: createConnector({
        isConnected() { return false; },
      }),
      legacyService: createLegacyService(),
    });

    const result = await dispatch('textMap', { pid: 99, format: 'compact' });
    assert.deepEqual(result, {
      text: 'Window: Fake',
      timing: 0,
    });
  }

  {
    const dispatch = createRpcDispatcher({
      connector: createConnector(),
      legacyService: createLegacyService(),
    });

    const result = await dispatch('focused', { pid: 5150 });
    assert.equal(result.pid, 5150);
    assert.equal(result.name, 'Search');
    assert.deepEqual(result.path, ['Window', 'Search']);
  }

  console.log('daemon dispatcher tests passed');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
