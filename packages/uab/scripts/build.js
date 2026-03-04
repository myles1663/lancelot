#!/usr/bin/env node
/**
 * UAB Build Script (Lancelot standalone)
 *
 * Compiles TypeScript from src/ to dist/ directly using the
 * local tsconfig.json.  No parent repo dependency.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(__dirname, '..');

console.log('Building Universal App Bridge...');
try {
  execSync('npx tsc', { cwd: PKG_ROOT, stdio: 'inherit' });
} catch {
  console.error('Build failed.');
  process.exit(1);
}

// Verify key outputs
const required = ['dist/index.js', 'dist/service.js', 'dist/daemon.js', 'dist/cli.js'];
for (const file of required) {
  if (!fs.existsSync(path.join(PKG_ROOT, file))) {
    console.error(`Missing: ${file}`);
    process.exit(1);
  }
}

const pkg = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8'));
console.log(`${pkg.name}@${pkg.version} built successfully.`);
