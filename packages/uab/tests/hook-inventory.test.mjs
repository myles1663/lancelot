import test from 'node:test';
import assert from 'node:assert/strict';

import { PluginManager } from '../dist/plugins/base.js';
import { ExtensionWSServer } from '../dist/plugins/chrome-ext/ws-server.js';
import { ChromeExtPlugin } from '../dist/plugins/chrome-ext/index.js';
import { BrowserPlugin } from '../dist/plugins/browser/index.js';
import { ElectronPlugin } from '../dist/plugins/electron/index.js';
import { OfficePlugin } from '../dist/plugins/office/index.js';
import { QtPlugin } from '../dist/plugins/qt/index.js';
import { GtkPlugin } from '../dist/plugins/gtk/index.js';
import { JavaPlugin } from '../dist/plugins/java/index.js';
import { FlutterPlugin } from '../dist/plugins/flutter/index.js';
import { WinUIAPlugin } from '../dist/plugins/win-uia/index.js';
import { FRAMEWORK_HOOKS } from '../dist/hooks.js';

function buildPluginManager() {
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
  return manager;
}

test('framework hook inventory covers every claimed standalone hook', () => {
  const manager = buildPluginManager();
  const inventory = manager.getHookInventory();
  const ids = inventory.map(item => item.id).sort();

  assert.deepEqual(ids, [
    'browser-cdp',
    'chrome-extension',
    'electron-cdp',
    'flutter-uia',
    'gtk-uia',
    'java-jab-uia',
    'office-com+uia',
    'qt-uia',
    'win-uia',
  ]);
});

test('framework hook descriptors are explicit about native hooks versus bridges', () => {
  assert.equal(FRAMEWORK_HOOKS['chrome-extension'].integration, 'native');
  assert.equal(FRAMEWORK_HOOKS['browser-cdp'].integration, 'native');
  assert.equal(FRAMEWORK_HOOKS['electron-cdp'].integration, 'native');
  assert.equal(FRAMEWORK_HOOKS['office-com+uia'].integration, 'native');

  assert.equal(FRAMEWORK_HOOKS['qt-uia'].integration, 'bridge');
  assert.equal(FRAMEWORK_HOOKS['gtk-uia'].integration, 'bridge');
  assert.equal(FRAMEWORK_HOOKS['java-jab-uia'].integration, 'bridge');
  assert.equal(FRAMEWORK_HOOKS['flutter-uia'].integration, 'bridge');

  assert.equal(FRAMEWORK_HOOKS['win-uia'].integration, 'fallback');
});

test('framework hook inventory preserves documented discovery signals', () => {
  assert.match(FRAMEWORK_HOOKS['electron-cdp'].discoverySignals.join(' '), /remote-debugging-port/);
  assert.match(FRAMEWORK_HOOKS['office-com+uia'].discoverySignals.join(' '), /Office DLL signatures/);
  assert.match(FRAMEWORK_HOOKS['qt-uia'].discoverySignals.join(' '), /Qt window class names/);
  assert.match(FRAMEWORK_HOOKS['java-jab-uia'].discoverySignals.join(' '), /jvm\.dll/);
  assert.match(FRAMEWORK_HOOKS['flutter-uia'].discoverySignals.join(' '), /FlutterDesktopView/);
});
