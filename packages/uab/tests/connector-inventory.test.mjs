import test from 'node:test';
import assert from 'node:assert/strict';

import { UABConnector } from '../dist/connector.js';

test('connector exposes runtime hook inventory through public API', async () => {
  const connector = new UABConnector({
    persistent: false,
    extensionBridge: false,
    loadProfiles: false,
  });

  await connector.start();
  const hooks = connector.hookInventory().map(item => item.id).sort();
  await connector.stop();

  assert.deepEqual(hooks, [
    'browser-cdp',
    'electron-cdp',
    'flutter-uia',
    'gtk-uia',
    'java-jab-uia',
    'office-com+uia',
    'qt-uia',
    'win-uia',
  ]);
});

test('connector exposes framework detection inventory through public API', () => {
  const connector = new UABConnector({
    persistent: false,
    extensionBridge: false,
    loadProfiles: false,
  });

  const signatures = connector.signatureInventory().map(item => item.framework).sort();
  assert.deepEqual(signatures, [
    'dotnet',
    'electron',
    'flutter',
    'gtk3',
    'gtk4',
    'java-swing',
    'office',
    'qt5',
    'qt6',
    'wpf',
  ]);
});
