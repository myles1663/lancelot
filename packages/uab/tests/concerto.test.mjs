import test from 'node:test';
import assert from 'node:assert/strict';

import { getConcertoMethodInventory, planOperation } from '../dist/index.js';

test('concerto inventory includes documented keyboard, raw input, and vision methods', () => {
  const inventory = getConcertoMethodInventory();
  assert.ok(inventory.some((item) => item.id === 'keyboard-native'));
  assert.ok(inventory.some((item) => item.id === 'os-input-injection'));
  assert.ok(inventory.some((item) => item.id === 'vision-analysis'));
  assert.ok(inventory.some((item) => item.id === 'browser-cdp'));
});

test('concerto planner picks keyboard for command operations', () => {
  const plan = planOperation('office-com+uia', 'hotkey', ['win-uia', 'vision']);
  assert.equal(plan.primaryMethod, 'keyboard-native');
  assert.deepEqual(plan.fallbackMethods, ['office-com+uia', 'win-uia', 'vision']);
  assert.equal(plan.scoringTrace[0].method, 'keyboard-native');
  assert.match(plan.rationale, /Runtime score:/);
});

test('concerto planner picks raw input for spatial operations', () => {
  const plan = planOperation('win-uia', 'drag', ['vision']);
  assert.equal(plan.primaryMethod, 'os-input-injection');
  assert.deepEqual(plan.fallbackMethods, ['win-uia', 'vision']);
  assert.ok(plan.scoringTrace[0].score > plan.scoringTrace[1].score);
});

test('concerto planner uses vision analysis for describe workflows', () => {
  const plan = planOperation('electron-cdp', 'describe', ['win-uia', 'vision']);
  assert.equal(plan.primaryMethod, 'vision-analysis');
  assert.deepEqual(plan.fallbackMethods, ['electron-cdp', 'win-uia', 'vision']);
  assert.equal(plan.scoringTrace[0].dimensions.cost, 'api');
});

test('concerto planner ranks higher-quality connection methods ahead of weaker fallbacks at runtime', () => {
  const plan = planOperation('browser-cdp', 'click', ['win-uia', 'vision']);
  assert.equal(plan.primaryMethod, 'browser-cdp');
  assert.deepEqual(plan.fallbackMethods, ['win-uia', 'vision']);

  const [primary, secondary, tertiary] = plan.scoringTrace;
  assert.equal(primary.method, 'browser-cdp');
  assert.equal(secondary.method, 'win-uia');
  assert.equal(tertiary.method, 'vision');
  assert.ok(primary.score > secondary.score);
  assert.ok(secondary.score > tertiary.score);
});

test('concerto planner still prefers the active connection for screenshots over vision-only fallbacks', () => {
  const plan = planOperation('office-com+uia', 'screenshot', ['vision']);
  assert.equal(plan.primaryMethod, 'office-com+uia');
  assert.deepEqual(plan.fallbackMethods, ['vision', 'vision-analysis']);
  assert.ok(plan.scoringTrace.every((entry, index, trace) => index === 0 || trace[index - 1].score >= entry.score));
});

test('concerto planner can change tiers when runtime priorities prefer structured low-cost transport', () => {
  const plan = planOperation('electron-cdp', 'describe', ['win-uia', 'vision'], {
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
    note: 'Prefer cheap structured transport over API-backed visual verification.',
  });

  assert.equal(plan.primaryMethod, 'electron-cdp');
  assert.equal(plan.scoringTrace[0].method, 'electron-cdp');
  assert.match(plan.rationale, /Runtime context:/);
});

test('concerto planner can demote a degraded primary method with runtime penalties', () => {
  const plan = planOperation('browser-cdp', 'click', ['win-uia', 'vision'], {
    penalties: {
      'browser-cdp': 20,
    },
    note: 'Recent browser transport failures make the structured accessibility fallback safer.',
  });

  assert.equal(plan.primaryMethod, 'win-uia');
  assert.equal(plan.scoringTrace[0].method, 'win-uia');
  assert.equal(plan.scoringTrace[1].method, 'browser-cdp');
});

test('concerto planner excludes vision transports when runtime evidence says vision is unavailable', () => {
  const plan = planOperation('electron-cdp', 'describe', ['win-uia', 'vision'], {
    runtime: {
      activeMethod: 'electron-cdp',
      availableMethods: ['electron-cdp', 'win-uia'],
      preferredMethodOrder: ['electron-cdp', 'win-uia'],
      visionAvailable: false,
    },
  });

  assert.equal(plan.primaryMethod, 'electron-cdp');
  assert.deepEqual(plan.fallbackMethods, ['win-uia']);
  assert.ok(plan.scoringTrace.every((entry) => entry.method !== 'vision' && entry.method !== 'vision-analysis'));
  assert.ok(plan.runtimeEvidence.some((item) => /vision backend/i.test(item)));
});

test('concerto planner demotes an unhealthy active transport from live runtime evidence', () => {
  const plan = planOperation('browser-cdp', 'click', ['win-uia', 'vision'], {
    runtime: {
      activeMethod: 'browser-cdp',
      availableMethods: ['browser-cdp', 'win-uia'],
      preferredMethodOrder: ['browser-cdp', 'win-uia'],
      visionAvailable: false,
      connectionHealthy: false,
      healthFailures: 2,
      reconnectAttempts: 1,
    },
  });

  assert.equal(plan.primaryMethod, 'win-uia');
  assert.equal(plan.scoringTrace[0].method, 'win-uia');
  assert.equal(plan.scoringTrace[1].method, 'browser-cdp');
  assert.ok(plan.runtimeEvidence.some((item) => /health failure/i.test(item) || /active route is penalized/i.test(item)));
});
