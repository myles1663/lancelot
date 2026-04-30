import test from 'node:test';
import assert from 'node:assert/strict';

import { waitUntil } from '../dist/server.js';

test('server readiness polling retries until the predicate reports ready', async () => {
  let calls = 0;

  const ready = await waitUntil(
    'unit_readiness',
    async () => {
      calls += 1;
      return calls >= 3;
    },
    { timeoutMs: 100, intervalMs: 1 },
    { test: true },
  );

  assert.equal(ready, true);
  assert.equal(calls, 3);
});

test('server readiness polling returns false on timeout', async () => {
  let calls = 0;

  const ready = await waitUntil(
    'unit_timeout',
    async () => {
      calls += 1;
      return false;
    },
    { timeoutMs: 5, intervalMs: 1 },
    { test: true },
  );

  assert.equal(ready, false);
  assert.ok(calls >= 1);
});
