#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { execa } from 'execa';

function parseArgs(argv) {
  const args = {
    installDir: null,
    outputDir: null,
    baseUrl: 'http://localhost:8000',
    hostAgentUrl: 'http://127.0.0.1:9111/health',
    allowPartial: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--install-dir') {
      args.installDir = argv[++i];
    } else if (arg === '--output-dir') {
      args.outputDir = argv[++i];
    } else if (arg === '--base-url') {
      args.baseUrl = argv[++i];
    } else if (arg === '--host-agent-url') {
      args.hostAgentUrl = argv[++i];
    } else if (arg === '--allow-partial') {
      args.allowPartial = true;
    } else if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!args.installDir || !args.outputDir) {
    throw new Error('--install-dir and --output-dir are required');
  }

  args.installDir = path.resolve(args.installDir);
  args.outputDir = path.resolve(args.outputDir);
  return args;
}

function printHelp() {
  console.log(`Usage: node installer/scripts/collect-install-proof.mjs --install-dir <path> --output-dir <path> [options]

Options:
  --base-url <url>        Lancelot base URL (default: http://localhost:8000)
  --host-agent-url <url>  Host Agent health URL (default: http://127.0.0.1:9111/health)
  --allow-partial         Collect evidence without failing on missing health/command checks
  -h, --help              Show this help
`);
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

async function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', reject);
  });
}

function classifySecretKey(key) {
  return (
    key.includes('TOKEN')
    || key.includes('PASSWORD')
    || key.includes('SECRET')
    || key.endsWith('_KEY')
    || key.endsWith('_RESET_CODE')
  );
}

function sanitizeEnvContent(raw) {
  const lines = raw.split(/\r?\n/);
  const envSummary = {};
  const sanitizedLines = lines.map((line) => {
    if (!line || line.trimStart().startsWith('#') || !line.includes('=')) {
      return line;
    }
    const idx = line.indexOf('=');
    const key = line.slice(0, idx);
    const value = line.slice(idx + 1);
    if (classifySecretKey(key)) {
      envSummary[key] = { present: value.length > 0, redacted: true };
      return `${key}=<redacted>`;
    }
    envSummary[key] = value;
    return line;
  });

  return {
    sanitized: sanitizedLines.join('\n'),
    summary: envSummary,
  };
}

async function safeCommand(command, args, options = {}) {
  try {
    const result = await execa(command, args, {
      reject: false,
      timeout: 15000,
      ...options,
    });
    return {
      ok: result.exitCode === 0,
      exitCode: result.exitCode,
      stdout: (result.stdout || '').trim(),
      stderr: (result.stderr || '').trim(),
    };
  } catch (error) {
    return {
      ok: false,
      exitCode: null,
      stdout: '',
      stderr: error.message,
    };
  }
}

async function fetchJson(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(5000) });
    const text = await response.text();
    let body = text;
    try {
      body = JSON.parse(text);
    } catch {
      // Keep raw body for diagnostics.
    }
    return {
      ok: response.ok,
      status: response.status,
      body,
    };
  } catch (error) {
    return {
      ok: false,
      status: null,
      error: error.message,
    };
  }
}

async function collectFileEvidence(installDir) {
  const targets = [
    '.env',
    'docker-compose.yml',
    'lancelot_data/onboarding_snapshot.json',
    'lancelot_data/USER.md',
    'host_agent/agent.py',
  ];

  const files = {};
  for (const relativePath of targets) {
    const fullPath = path.join(installDir, relativePath);
    const exists = fs.existsSync(fullPath);
    files[relativePath] = { exists };
    if (!exists) {
      continue;
    }
    const stat = await fsp.stat(fullPath);
    files[relativePath].size = stat.size;
    files[relativePath].sha256 = await sha256File(fullPath);
  }

  const weightsDir = path.join(installDir, 'local_models', 'weights');
  const weightEvidence = [];
  if (fs.existsSync(weightsDir)) {
    const entries = await fsp.readdir(weightsDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const fullPath = path.join(weightsDir, entry.name);
      const stat = await fsp.stat(fullPath);
      weightEvidence.push({
        name: entry.name,
        size: stat.size,
        sha256: await sha256File(fullPath),
      });
    }
  }
  files['local_models/weights'] = {
    exists: fs.existsSync(weightsDir),
    files: weightEvidence,
  };

  return files;
}

function summarizeChecks(proof) {
  const checks = [];
  checks.push({
    label: 'Install directory exists',
    ok: proof.installDirExists,
  });
  checks.push({
    label: 'Generated .env present',
    ok: proof.files['.env']?.exists === true,
  });
  checks.push({
    label: 'docker-compose.yml present',
    ok: proof.files['docker-compose.yml']?.exists === true,
  });
  checks.push({
    label: 'Onboarding snapshot present',
    ok: proof.files['lancelot_data/onboarding_snapshot.json']?.exists === true,
  });
  checks.push({
    label: 'Installer health endpoint healthy',
    ok: proof.health.root?.ok === true,
  });
  checks.push({
    label: 'War Room live endpoint healthy',
    ok: proof.health.live?.ok === true,
  });
  checks.push({
    label: 'War Room ready endpoint healthy',
    ok: proof.health.ready?.ok === true,
  });
  checks.push({
    label: 'Host Agent health endpoint healthy',
    ok: proof.health.hostAgent?.ok === true,
  });
  checks.push({
    label: 'Docker CLI available',
    ok: proof.commands.docker?.ok === true,
  });
  checks.push({
    label: 'Docker Compose CLI available',
    ok: proof.commands.compose?.ok === true,
  });
  return checks;
}

function renderMarkdown(proof) {
  const checks = summarizeChecks(proof);
  const lines = [
    '# Lancelot Installer Proof Bundle',
    '',
    `- Collected: ${proof.collectedAt}`,
    `- Install directory: \`${proof.installDir}\``,
    `- Base URL: \`${proof.baseUrl}\``,
    `- Host Agent URL: \`${proof.hostAgentUrl}\``,
    '',
    '## Summary Checks',
    '',
    ...checks.map((check) => `- ${check.ok ? '[x]' : '[ ]'} ${check.label}`),
    '',
    '## Host',
    '',
    `- Platform: ${proof.host.platform} ${proof.host.release} (${proof.host.arch})`,
    `- Node: ${proof.host.node}`,
    `- CPUs: ${proof.host.cpuCount}`,
    `- RAM (GB): ${proof.host.memoryGb}`,
    '',
    '## Commands',
    '',
    `- Git: ${proof.commands.git?.stdout || proof.commands.git?.stderr || 'not available'}`,
    `- Docker: ${proof.commands.docker?.stdout || proof.commands.docker?.stderr || 'not available'}`,
    `- Docker Compose: ${proof.commands.compose?.stdout || proof.commands.compose?.stderr || 'not available'}`,
    '',
    '## Health',
    '',
    `- /health: ${proof.health.root?.status ?? 'unreachable'}`,
    `- /health/live: ${proof.health.live?.status ?? 'unreachable'}`,
    `- /health/ready: ${proof.health.ready?.status ?? 'unreachable'}`,
    `- Host Agent: ${proof.health.hostAgent?.status ?? 'unreachable'}`,
    '',
    '## Files',
    '',
    ...Object.entries(proof.files).map(([name, data]) => {
      if (name === 'local_models/weights') {
        const count = data.files?.length || 0;
        return `- ${name}: ${data.exists ? `${count} file(s)` : 'missing'}`;
      }
      return `- ${name}: ${data.exists ? `present (sha256 ${data.sha256})` : 'missing'}`;
    }),
    '',
    '## Notes',
    '',
    '- Raw secrets are not included in this bundle.',
    '- See `installer-proof.json` for the machine-readable evidence set.',
  ];

  if (proof.warnings.length > 0) {
    lines.push('', '## Warnings', '', ...proof.warnings.map((warning) => `- ${warning}`));
  }
  if (proof.failures.length > 0) {
    lines.push('', '## Failures', '', ...proof.failures.map((failure) => `- ${failure}`));
  }

  return lines.join('\n') + '\n';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  await fsp.mkdir(args.outputDir, { recursive: true });

  const proof = {
    collectedAt: new Date().toISOString(),
    installDir: args.installDir,
    installDirExists: fs.existsSync(args.installDir),
    outputDir: args.outputDir,
    baseUrl: args.baseUrl.replace(/\/$/, ''),
    hostAgentUrl: args.hostAgentUrl,
    host: {
      platform: os.platform(),
      release: os.release(),
      arch: os.arch(),
      node: process.version,
      cpuCount: os.cpus().length,
      memoryGb: Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10,
    },
    commands: {},
    files: {},
    envSummary: {},
    health: {},
    warnings: [],
    failures: [],
  };

  if (!proof.installDirExists) {
    proof.failures.push(`Install directory does not exist: ${args.installDir}`);
  }

  proof.commands.git = await safeCommand('git', ['--version']);
  proof.commands.docker = await safeCommand('docker', ['--version']);
  proof.commands.compose = await safeCommand('docker', ['compose', 'version']);
  proof.commands.composePs = await safeCommand('docker', ['compose', 'ps'], { cwd: args.installDir });

  if (proof.installDirExists) {
    proof.files = await collectFileEvidence(args.installDir);

    const envPath = path.join(args.installDir, '.env');
    if (fs.existsSync(envPath)) {
      const envRaw = await fsp.readFile(envPath, 'utf8');
      const sanitizedEnv = sanitizeEnvContent(envRaw);
      proof.envSummary = sanitizedEnv.summary;
      await fsp.writeFile(path.join(args.outputDir, 'sanitized.env'), sanitizedEnv.sanitized, 'utf8');
    } else {
      proof.failures.push('Missing generated .env file');
    }

    const onboardingPath = path.join(args.installDir, 'lancelot_data', 'onboarding_snapshot.json');
    if (fs.existsSync(onboardingPath)) {
      try {
        proof.onboardingSnapshot = JSON.parse(await fsp.readFile(onboardingPath, 'utf8'));
      } catch (error) {
        proof.failures.push(`Could not parse onboarding snapshot: ${error.message}`);
      }
    }
  }

  const healthBase = proof.baseUrl;
  proof.health.root = await fetchJson(`${healthBase}/health`);
  proof.health.live = await fetchJson(`${healthBase}/health/live`);
  proof.health.ready = await fetchJson(`${healthBase}/health/ready`);
  proof.health.hostAgent = await fetchJson(args.hostAgentUrl);

  const requiredChecks = summarizeChecks(proof);
  for (const check of requiredChecks) {
    if (!check.ok) {
      proof.failures.push(check.label);
    }
  }

  if (!proof.commands.composePs.ok) {
    proof.warnings.push(`docker compose ps unavailable: ${proof.commands.composePs.stderr || 'command failed'}`);
  }

  await fsp.writeFile(
    path.join(args.outputDir, 'installer-proof.json'),
    JSON.stringify(proof, null, 2),
    'utf8',
  );
  await fsp.writeFile(
    path.join(args.outputDir, 'installer-proof.md'),
    renderMarkdown(proof),
    'utf8',
  );

  if (proof.failures.length > 0 && !args.allowPartial) {
    process.exitCode = 1;
  }
}

await main();
