import test from 'node:test';
import assert from 'node:assert/strict';

import { PermissionManager } from '../dist/permissions.js';

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
