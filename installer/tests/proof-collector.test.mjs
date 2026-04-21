import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

import { generateEnvContent, patchDockerCompose } from '../src/config.mjs';
import { markOnboardingComplete } from '../src/onboarding.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const installerRoot = path.resolve(__dirname, '..');
const proofScriptPath = path.join(installerRoot, 'scripts', 'collect-install-proof.mjs');
const templateRepoPath = path.join(installerRoot, 'tests', 'fixtures', 'template-repo');

async function startProofServer() {
  const server = createServer((req, res) => {
    let payload = { status: 'ok' };
    if (req.url === '/health/live') {
      payload = { status: 'alive' };
    } else if (req.url === '/health/ready') {
      payload = { ready: true, local_llm_ready: true };
    } else if (req.url === '/health') {
      payload = { status: 'ok' };
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(payload));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

function spawnNode(args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, options);
    let stdout = '';
    let stderr = '';

    child.stdout?.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr?.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', reject);
    child.on('close', (code) => {
      resolve({ status: code, stdout, stderr });
    });
  });
}

test('proof collector writes sanitized machine-readable installer evidence', async () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'create-lancelot-proof-'));
  const installDir = path.join(tempDir, 'install');
  const outputDir = path.join(tempDir, 'proof');

  await fs.cp(templateRepoPath, installDir, { recursive: true });
  const config = {
    provider: 'openai',
    authMode: 'api_key',
    apiKey: 'sk-proof-test',
    commsType: 'skip',
    hasGpu: false,
    gpuLayers: 0,
    warRoomAuthModel: 'local',
    warRoomUser: 'operator',
    warRoomPassword: 'password123',
    ownerName: 'Myles',
    installDir,
  };

  writeFileSync(path.join(installDir, '.env'), generateEnvContent(config), 'utf8');
  await patchDockerCompose(installDir, config);
  await markOnboardingComplete(installDir, config);
  await fs.mkdir(path.join(installDir, 'local_models', 'weights'), { recursive: true });
  writeFileSync(path.join(installDir, 'local_models', 'weights', 'smoke.gguf'), 'fixture-model', 'utf8');

  const appServer = await startProofServer();
  const hostAgentServer = await startProofServer();

  try {
    const result = await spawnNode(
      [
        proofScriptPath,
        '--install-dir', installDir,
        '--output-dir', outputDir,
        '--base-url', appServer.url,
        '--host-agent-url', `${hostAgentServer.url}/health`,
        '--allow-partial',
      ],
      {
        cwd: installerRoot,
        encoding: 'utf8',
      },
    );

    assert.equal(result.status, 0, result.stderr);

    const manifest = JSON.parse(readFileSync(path.join(outputDir, 'installer-proof.json'), 'utf8'));
    const markdown = readFileSync(path.join(outputDir, 'installer-proof.md'), 'utf8');
    const sanitizedEnv = readFileSync(path.join(outputDir, 'sanitized.env'), 'utf8');

    assert.equal(manifest.installDir, installDir);
    assert.equal(manifest.files['.env'].exists, true);
    assert.equal(manifest.files['docker-compose.yml'].exists, true);
    assert.equal(manifest.files['lancelot_data/onboarding_snapshot.json'].exists, true);
    assert.equal(manifest.health.root.ok, true);
    assert.equal(manifest.health.live.ok, true);
    assert.equal(manifest.health.ready.ok, true);
    assert.equal(manifest.health.hostAgent.ok, true);
    assert.ok(manifest.files['local_models/weights'].files.some((entry) => entry.name === 'smoke.gguf'));
    assert.match(sanitizedEnv, /^OPENAI_API_KEY=<redacted>$/m);
    assert.match(sanitizedEnv, /^WARROOM_PASSWORD=<redacted>$/m);
    assert.doesNotMatch(sanitizedEnv, /sk-proof-test/);
    assert.match(markdown, /Lancelot Installer Proof Bundle/);
  } finally {
    await appServer.close();
    await hostAgentServer.close();
  }
});
