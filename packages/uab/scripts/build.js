#!/usr/bin/env node

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(__dirname, '..');
const DIST_DIR = path.join(PKG_ROOT, 'dist');

console.log('Building Universal App Bridge...\n');

try {
  execSync('npx tsc -p tsconfig.json', {
    cwd: PKG_ROOT,
    stdio: 'inherit',
  });
} catch {
  console.error('\nTypeScript compilation failed');
  process.exit(1);
}

const requiredFiles = [
  'dist/index.js',
  'dist/cli.js',
  'dist/daemon.js',
  'dist/connector.js',
  'dist/server.js',
  'dist/service.js',
  'dist/router.js',
  'dist/hooks.js',
  'dist/plugins/browser/index.js',
  'dist/plugins/chrome-ext/index.js',
  'dist/plugins/electron/index.js',
  'dist/plugins/office/index.js',
  'dist/plugins/qt/index.js',
  'dist/plugins/gtk/index.js',
  'dist/plugins/java/index.js',
  'dist/plugins/flutter/index.js',
  'dist/plugins/win-uia/index.js',
  'dist/plugins/vision/index.js',
  'data/action-risk.json',
];

const missing = requiredFiles.filter(file => !fs.existsSync(path.join(PKG_ROOT, file)));
if (missing.length > 0) {
  console.error('\nMissing build outputs:');
  for (const file of missing) console.error(` - ${file}`);
  process.exit(1);
}

let fileCount = 0;
for (const _entry of fs.readdirSync(DIST_DIR, { recursive: true })) {
  fileCount += 1;
}

console.log(`\nBuilt successfully. Verified ${requiredFiles.length} required outputs across ${fileCount} dist entries.`);
