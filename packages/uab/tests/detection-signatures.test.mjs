import test from 'node:test';
import assert from 'node:assert/strict';

import { DETECTION_SIGNATURES, FrameworkDetector } from '../dist/detector.js';

test('detector exports the documented framework signature inventory', () => {
  const frameworks = DETECTION_SIGNATURES.map(sig => sig.framework).sort();
  assert.deepEqual(frameworks, [
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

test('detector signature inventory preserves core discovery signals', () => {
  const detector = new FrameworkDetector();
  const inventory = detector.getSignatureInventory();
  const byFramework = new Map(inventory.map(item => [item.framework, item]));

  assert.match(byFramework.get('electron').modules.join(' '), /libcef\.dll/i);
  assert.match(byFramework.get('office').modules.join(' '), /xlcall32\.dll/i);
  assert.match(byFramework.get('flutter').modules.join(' '), /flutter_windows\.dll/i);
  assert.match(byFramework.get('java-swing').modules.join(' '), /jvm\.dll/i);
  assert.match(byFramework.get('qt6').modules.join(' '), /qt6core\.dll/i);
});
