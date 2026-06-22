import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PermissionManager,
  RISK_TERMINOLOGY,
  validateRiskTerminology,
} from '../dist/permissions.js';

test('risk terminology mapping locks governance, tool fabric, and UAB labels', () => {
  assert.deepEqual(RISK_TERMINOLOGY, {
    safe: { toolFabric: 'low', governance: 'T0/T1' },
    moderate: { toolFabric: 'medium', governance: 'T1/T2' },
    destructive: { toolFabric: 'high', governance: 'T3' },
  });

  assert.doesNotThrow(() => validateRiskTerminology());
});

test('risk terminology validation fails on label drift', () => {
  assert.throws(
    () => validateRiskTerminology({
      safe: { toolFabric: 'low', governance: 'T0/T1' },
      moderate: { toolFabric: 'medium', governance: 'T1/T2' },
      critical: { toolFabric: 'high', governance: 'T3' },
    }),
    /labels drifted/,
  );
});

test('risk terminology validation fails on mapping drift', () => {
  assert.throws(
    () => validateRiskTerminology({
      safe: { toolFabric: 'low', governance: 'T0/T1' },
      moderate: { toolFabric: 'low', governance: 'T1/T2' },
      destructive: { toolFabric: 'high', governance: 'T3' },
    }),
    /mapping drifted for moderate/,
  );
});

test('action risk manifest validation fails on unknown manifest category', () => {
  assert.throws(
    () => validateRiskTerminology(RISK_TERMINOLOGY, {
      read_only: ['query'],
      mutating: ['click'],
      destructive: ['close'],
      sensitive_app_patterns: [],
      experimental: ['launchMissiles'],
    }),
    /unknown keys/,
  );
});

test('permission risk taxonomy treats UI actions as mutating', () => {
  const permissions = new PermissionManager();

  for (const action of ['click', 'doubleclick', 'rightclick', 'drag', 'scroll', 'focus']) {
    assert.equal(permissions.getRiskLevel(action), 'moderate', `${action} should be moderate`);
  }
});

test('permission risk taxonomy treats irreversible and window-control actions as destructive', () => {
  const permissions = new PermissionManager();

  for (const action of ['close', 'invoke', 'move', 'resize', 'sendEmail', 'deleteCookie', 'closeTab']) {
    assert.equal(permissions.getRiskLevel(action), 'destructive', `${action} should be destructive`);
  }
});

test('permission risk taxonomy keeps read-only inspection actions safe', () => {
  const permissions = new PermissionManager();

  for (const action of ['screenshot', 'readDocument', 'readCell', 'getTabs', 'getCookies']) {
    assert.equal(permissions.getRiskLevel(action), 'safe', `${action} should be safe`);
  }
});

test('permission risk taxonomy fails closed for unknown action risk terms', () => {
  const permissions = new PermissionManager();

  assert.equal(permissions.getRiskLevel('unknownGovernedAction'), 'destructive');
});
