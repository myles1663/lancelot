import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { run } from '../src/index.mjs';
import { generateEnvContent } from '../src/config.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const installerRoot = path.resolve(__dirname, '..');
const cliPath = path.join(installerRoot, 'bin', 'create-lancelot.mjs');
const runtimeFixturePath = path.join(installerRoot, 'tests', 'runtime-fixture.mjs');
const filesystemRuntimeFixturePath = path.join(installerRoot, 'tests', 'filesystem-runtime-fixture.mjs');
const realModulesRuntimeFixturePath = path.join(installerRoot, 'tests', 'real-modules-runtime-fixture.mjs');
const stateRuntimeFixturePath = path.join(installerRoot, 'tests', 'state-runtime-fixture.mjs');
const templateRepoPath = path.join(installerRoot, 'tests', 'fixtures', 'template-repo');

function prependPath(env, dir) {
  const current = env.Path || env.PATH || '';
  const next = `${dir}${path.delimiter}${current}`;
  return { ...env, Path: next, PATH: next };
}

function writeExecutable(binDir, name, contents) {
  mkdirSync(binDir, { recursive: true });
  const fileName = process.platform === 'win32' ? `${name}.cmd` : name;
  const filePath = path.join(binDir, fileName);
  writeFileSync(filePath, contents, 'utf8');
  if (process.platform !== 'win32') {
    chmodSync(filePath, 0o755);
  }
  return filePath;
}

function createFakeDocker(binDir) {
  if (process.platform === 'win32') {
    writeExecutable(
      binDir,
      'docker',
      [
        '@echo off',
        'setlocal',
        'echo %*>>"%INSTALLER_DOCKER_LOG%"',
        'if "%1"=="--version" echo Docker version 26.1.0&& exit /b 0',
        'if "%1"=="info" echo Server: Docker Engine&& exit /b 0',
        'if "%1"=="compose" (',
        '  if "%2"=="version" echo Docker Compose version v2.28.0&& exit /b 0',
        '  if "%2"=="pull" echo pulling&& exit /b 0',
        '  if "%2"=="build" echo building&& exit /b 0',
        '  if "%2"=="up" echo starting&& exit /b 0',
        '  if "%2"=="down" echo stopping&& exit /b 0',
        ')',
        'exit /b 0',
      ].join('\r\n'),
    );
    writeExecutable(
      binDir,
      'where',
      [
        '@echo off',
        'if "%1"=="pythonw" (',
        `  echo ${path.join(binDir, 'pythonw.cmd')}`,
        '  exit /b 0',
        ')',
        'exit /b 1',
      ].join('\r\n'),
    );
    writeExecutable(binDir, 'pythonw', '@echo off\r\nexit /b 0\r\n');
    return;
  }

  writeExecutable(
    binDir,
    'docker',
    [
      '#!/bin/sh',
      'echo "$@" >> "$INSTALLER_DOCKER_LOG"',
      'if [ "$1" = "--version" ]; then echo "Docker version 26.1.0"; exit 0; fi',
      'if [ "$1" = "info" ]; then echo "Server: Docker Engine"; exit 0; fi',
      'if [ "$1" = "compose" ] && [ "$2" = "version" ]; then echo "Docker Compose version v2.28.0"; exit 0; fi',
      'if [ "$1" = "compose" ] && [ "$2" = "pull" ]; then case ",${INSTALLER_DOCKER_FAIL_PULL_TARGETS:-}," in *",$3,"*) exit 1;; esac; echo "pulling"; exit 0; fi',
      'if [ "$1" = "compose" ] && [ "$2" = "build" ]; then echo "building"; exit 0; fi',
      'if [ "$1" = "compose" ] && [ "$2" = "up" ]; then echo "starting"; exit 0; fi',
      'if [ "$1" = "compose" ] && [ "$2" = "down" ]; then echo "stopping"; exit 0; fi',
      'exit 0',
    ].join('\n'),
  );
  writeExecutable(binDir, 'python3', '#!/bin/sh\nexit 0\n');
}

function createLocalGitRepo(tempDir) {
  const repoPath = path.join(tempDir, 'template-git-repo');
  cpSync(templateRepoPath, repoPath, { recursive: true });

  const init = spawnSync('git', ['init', '--quiet'], { cwd: repoPath, encoding: 'utf8' });
  assert.equal(init.status, 0, init.stderr);

  const add = spawnSync('git', ['add', '.'], { cwd: repoPath, encoding: 'utf8' });
  assert.equal(add.status, 0, add.stderr);

  const commit = spawnSync(
    'git',
    ['-c', 'user.name=Installer Test', '-c', 'user.email=installer@example.com', 'commit', '-m', 'template', '--quiet'],
    { cwd: repoPath, encoding: 'utf8' },
  );
  assert.equal(commit.status, 0, commit.stderr);

  return repoPath;
}

async function startHealthServer(body) {
  let hitCount = 0;
  const server = createServer((req, res) => {
    hitCount += 1;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}/health`;

  return {
    url,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
    getHitCount: () => hitCount,
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

function createSpinner() {
  return {
    text: '',
    start() {
      return this;
    },
    succeed() {},
    fail() {},
    warn() {},
  };
}

function createRuntime(overrides = {}) {
  const calls = [];
  const saves = [];
  const infos = [];
  const errors = [];
  const exits = [];
  const success = [];
  const execs = [];

  const runtime = {
    chalk: {
      yellow: (value) => value,
      gray: (value) => value,
      white: (value) => value,
      cyan: Object.assign((value) => value, { underline: (value) => value }),
    },
    ora: () => createSpinner(),
    exec: (command, callback) => {
      execs.push(command);
      if (callback) callback(null);
    },
    processRef: {
      platform: 'win32',
      on() {},
      exit(code) {
        exits.push(code);
      },
    },
    PROVIDERS: {
      openai: { name: 'OpenAI' },
      anthropic: { name: 'Anthropic' },
    },
    COMMS: {
      skip: { name: 'Skip' },
      telegram: { name: 'Telegram' },
      google_chat: { name: 'Google Chat' },
    },
    showBanner() {
      calls.push('showBanner');
    },
    showStep(step, total, label) {
      calls.push(`step:${step}/${total}:${label}`);
    },
    showSuccess(payload) {
      success.push(payload);
    },
    showError(message) {
      errors.push(message);
    },
    showInfo(message) {
      infos.push(message);
    },
    async runAllChecks(targetDir) {
      calls.push(`runAllChecks:${targetDir}`);
      return { hasGpu: false, gpuLayers: 0, gpuName: null };
    },
    async promptInstallDir(defaultDir) {
      calls.push(`promptInstallDir:${defaultDir}`);
      return defaultDir;
    },
    async promptOwnerName() {
      calls.push('promptOwnerName');
      return 'Myles';
    },
    async promptProvider(currentProvider) {
      calls.push(`promptProvider:${currentProvider ?? 'none'}`);
      return currentProvider || 'openai';
    },
    async promptAuthMethod(provider) {
      calls.push(`promptAuthMethod:${provider}`);
      return 'api_key';
    },
    async promptApiKey(provider) {
      calls.push(`promptApiKey:${provider}`);
      return 'sk-test';
    },
    async promptCommsChannel() {
      calls.push('promptCommsChannel');
      return 'skip';
    },
    async promptTelegramToken() {
      throw new Error('unexpected telegram prompt');
    },
    async promptTelegramChatId() {
      throw new Error('unexpected telegram chat prompt');
    },
    async promptGoogleChatSpace() {
      throw new Error('unexpected google chat prompt');
    },
    async promptWarRoomAuthModel() {
      calls.push('promptWarRoomAuthModel');
      return 'local';
    },
    async promptWarRoomUsername() {
      calls.push('promptWarRoomUsername');
      return 'operator';
    },
    async promptWarRoomPassword() {
      calls.push('promptWarRoomPassword');
      return 'password123';
    },
    async promptOidcIssuerUrl() {
      throw new Error('unexpected OIDC prompt');
    },
    async promptOidcClientId() {
      throw new Error('unexpected OIDC prompt');
    },
    async promptOidcClientSecret() {
      throw new Error('unexpected OIDC prompt');
    },
    async promptOidcBaseUrl() {
      throw new Error('unexpected OIDC prompt');
    },
    async promptOidcAllowedGroups() {
      throw new Error('unexpected OIDC prompt');
    },
    async promptConfirm() {
      calls.push('promptConfirm');
      return true;
    },
    async writeEnvFile(installDir) {
      calls.push(`writeEnvFile:${installDir}`);
    },
    async patchDockerCompose(installDir) {
      calls.push(`patchDockerCompose:${installDir}`);
    },
    async downloadModel(installDir, onProgress) {
      calls.push(`downloadModel:${installDir}`);
      onProgress({ done: true, message: 'downloaded' });
    },
    async cloneRepo(installDir) {
      calls.push(`cloneRepo:${installDir}`);
    },
    async dockerBuild(installDir) {
      calls.push(`dockerBuild:${installDir}`);
    },
    async dockerUp(installDir) {
      calls.push(`dockerUp:${installDir}`);
    },
    async waitForHealthy() {
      calls.push('waitForHealthy');
    },
    async startHostAgent(installDir) {
      calls.push(`startHostAgent:${installDir}`);
    },
    async markOnboardingComplete(installDir) {
      calls.push(`markOnboardingComplete:${installDir}`);
    },
    async loadState() {
      calls.push('loadState');
      return null;
    },
    async saveState(completed, config) {
      saves.push({
        completed: [...completed],
        config: JSON.parse(JSON.stringify(config)),
      });
    },
    async clearState() {
      calls.push('clearState');
    },
    isStepComplete(completed, step) {
      return completed.includes(step);
    },
  };

  return {
    runtime: { ...runtime, ...overrides },
    calls,
    saves,
    infos,
    errors,
    exits,
    success,
    execs,
  };
}

test('CLI help exposes the supported installer contract', () => {
  const result = spawnSync(process.execPath, [cliPath, '--help'], {
    cwd: installerRoot,
    encoding: 'utf8',
  });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /--directory <path>/);
  assert.match(result.stdout, /--provider <name>/);
  assert.match(result.stdout, /--resume/);
  assert.doesNotMatch(result.stdout, /skip-model/);
});

test('run completes the installer happy path and persists progress', async () => {
  const { runtime, calls, saves, errors, exits, success, execs } = createRuntime();
  const expectedInstallDir = path.resolve('lancelot');

  await run({ directory: './lancelot', provider: 'openai', resume: false }, runtime);

  assert.deepEqual(errors, []);
  assert.deepEqual(exits, []);
  assert.equal(success.length, 1);
  assert.equal(success[0].providerName, 'OpenAI');
  assert.ok(calls.includes(`cloneRepo:${expectedInstallDir}`));
  assert.ok(calls.includes(`downloadModel:${expectedInstallDir}`));
  assert.ok(calls.includes(`dockerBuild:${expectedInstallDir}`));
  assert.ok(calls.includes(`dockerUp:${expectedInstallDir}`));
  assert.ok(calls.includes('waitForHealthy'));
  assert.ok(calls.includes(`startHostAgent:${expectedInstallDir}`));
  assert.ok(calls.includes('clearState'));
  assert.ok(execs.some((command) => command.includes('http://localhost:8000')));
  assert.deepEqual(
    saves.at(-1).completed,
    ['prereqs', 'directory', 'provider', 'comms', 'warroom_auth', 'clone', 'config', 'model', 'docker_build', 'docker_up', 'health_check', 'host_agent'],
  );
});

test('run resumes from saved state and only re-prompts missing secrets', async () => {
  const resumeState = {
    installDir: 'C:\\resume\\lancelot',
    completedSteps: ['prereqs', 'directory', 'provider', 'comms', 'warroom_auth'],
    config: {
      installDir: 'C:\\resume\\lancelot',
      ownerName: 'Myles',
      provider: 'openai',
      authMode: 'api_key',
      apiKey: '',
      commsType: 'skip',
      warRoomAuthModel: 'local',
      warRoomUser: 'operator',
      warRoomPassword: '',
    },
  };

  const { runtime, calls, infos, errors, exits, success } = createRuntime({
    async loadState() {
      return resumeState;
    },
  });

  await run({ directory: './ignored', provider: null, resume: true }, runtime);

  assert.deepEqual(errors, []);
  assert.deepEqual(exits, []);
  assert.equal(success.length, 1);
  assert.ok(infos.some((message) => message.includes('Resuming installation from: C:\\resume\\lancelot')));
  assert.ok(calls.includes('promptApiKey:openai'));
  assert.ok(calls.includes('promptWarRoomPassword'));
  assert.ok(!calls.some((entry) => entry.startsWith('runAllChecks:')));
  assert.ok(!calls.some((entry) => entry.startsWith('promptInstallDir:')));
  assert.ok(!calls.some((entry) => entry.startsWith('promptProvider:')));
  assert.ok(calls.includes('cloneRepo:C:\\resume\\lancelot'));
  assert.ok(calls.includes('downloadModel:C:\\resume\\lancelot'));
});

test('CLI executes the real installer entrypoint end-to-end with an injected runtime fixture', () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'create-lancelot-cli-'));
  const logPath = path.join(tempDir, 'runtime-log.json');
  const result = spawnSync(
    process.execPath,
    [cliPath, '--directory', './custom-install', '--provider', 'openai'],
    {
      cwd: installerRoot,
      encoding: 'utf8',
      env: {
        ...process.env,
        CREATE_LANCELOT_RUNTIME_MODULE: runtimeFixturePath,
        INSTALLER_RUNTIME_LOG: logPath,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);

  const log = JSON.parse(readFileSync(logPath, 'utf8'));
  const expectedInstallDir = path.resolve(installerRoot, 'custom-install');

  assert.deepEqual(log.errors, []);
  assert.equal(log.success.length, 1);
  assert.equal(log.success[0].providerName, 'OpenAI');
  assert.ok(log.calls.includes(`cloneRepo:${expectedInstallDir}`));
  assert.ok(log.calls.includes(`downloadModel:${expectedInstallDir}`));
  assert.ok(log.calls.includes(`dockerBuild:${expectedInstallDir}`));
  assert.ok(log.calls.includes(`dockerUp:${expectedInstallDir}`));
  assert.ok(log.calls.includes(`startHostAgent:${expectedInstallDir}`));
  assert.ok(log.calls.includes('clearState'));
  assert.ok(log.execs.some((command) => command.includes('http://localhost:8000')));
});

test('CLI filesystem smoke writes real config, compose, and onboarding artifacts', () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'create-lancelot-fs-smoke-'));
  const installDir = path.join(tempDir, 'lancelot');
  const logPath = path.join(tempDir, 'runtime-log.json');
  const result = spawnSync(
    process.execPath,
    [cliPath, '--directory', installDir, '--provider', 'openai'],
    {
      cwd: installerRoot,
      encoding: 'utf8',
      env: {
        ...process.env,
        CREATE_LANCELOT_RUNTIME_MODULE: filesystemRuntimeFixturePath,
        INSTALLER_RUNTIME_LOG: logPath,
        INSTALLER_TEMPLATE_SOURCE: templateRepoPath,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);

  const log = JSON.parse(readFileSync(logPath, 'utf8'));
  const envContent = readFileSync(path.join(installDir, '.env'), 'utf8');
  const composeContent = readFileSync(path.join(installDir, 'docker-compose.yml'), 'utf8');
  const onboardingSnapshot = JSON.parse(
    readFileSync(path.join(installDir, 'lancelot_data', 'onboarding_snapshot.json'), 'utf8'),
  );
  const userMd = readFileSync(path.join(installDir, 'lancelot_data', 'USER.md'), 'utf8');

  assert.deepEqual(log.errors, []);
  assert.equal(log.success.length, 1);
  assert.ok(log.calls.includes(`cloneRepo:${installDir}`));
  assert.ok(log.calls.includes(`writeEnvFile:${installDir}`));
  assert.ok(log.calls.includes(`patchDockerCompose:${installDir}`));
  assert.ok(log.calls.includes(`markOnboardingComplete:${installDir}`));

  assert.match(envContent, /^LANCELOT_PROVIDER=openai$/m);
  assert.match(envContent, /^WARROOM_USERNAME=operator$/m);
  assert.match(envContent, /^HOST_AGENT_TOKEN=(?!lancelot-host-agent).+$/m);
  assert.match(envContent, /^LOCAL_LLM_IMAGE=ghcr\.io\/myles1663\/lancelot-local-llm:llama-cpp-0\.3\.19-cpu$/m);
  assert.match(envContent, /^LOCAL_LLM_PULL_POLICY=missing$/m);
  assert.match(envContent, /^LOCAL_LLM_WHEEL_VARIANT=cpu$/m);
  assert.match(envContent, /^LANCELOT_COMMS_TYPE=none$/m);
  assert.match(envContent, /^LANCELOT_DATA_MOUNT=\.\/lancelot_data$/m);
  assert.match(envContent, /^LANCELOT_WORKSPACE_MOUNT=\.\/lancelot_workspace$/m);
  assert.match(envContent, /^LOCAL_LLM_HEALTH_START_PERIOD=900s$/m);
  assert.match(composeContent, /\$\{LANCELOT_DATA_MOUNT:-lancelot_data\}:\/home\/lancelot\/data/);
  assert.match(composeContent, /\$\{LANCELOT_WORKSPACE_MOUNT:-lancelot_workspace\}:\/home\/lancelot\/workspace/);
  assert.doesNotMatch(composeContent, /deploy:\s*\n\s*resources:\s*\n\s*reservations:/m);
  assert.match(composeContent, /LOCAL_MODEL_GPU_LAYERS=0/);
  assert.equal(onboardingSnapshot.state, 'READY');
  assert.equal(onboardingSnapshot.flagship_provider, 'openai');
  assert.equal(onboardingSnapshot.credential_status, 'verified');
  assert.match(userMd, /OnboardingComplete: True/);
  assert.match(userMd, /Provider: openai/);
});

test('CLI smoke uses the real installer modules for clone, docker, health, and host-agent startup', async () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'create-lancelot-real-flow-'));
  const installDir = path.join(tempDir, 'lancelot');
  const logPath = path.join(tempDir, 'runtime-log.json');
  const dockerLogPath = path.join(tempDir, 'docker-log.txt');
  const binDir = path.join(tempDir, 'bin');
  const repoPath = createLocalGitRepo(tempDir);
  const healthServer = await startHealthServer({ status: 'ok' });
  const hostAgentServer = await startHealthServer({ status: 'ok' });

  createFakeDocker(binDir);

  try {
    const env = prependPath(
      {
        ...process.env,
        CREATE_LANCELOT_RUNTIME_MODULE: realModulesRuntimeFixturePath,
        INSTALLER_RUNTIME_LOG: logPath,
        INSTALLER_DOCKER_LOG: dockerLogPath,
        CREATE_LANCELOT_REPO_URL: repoPath,
        CREATE_LANCELOT_HEALTH_URL: healthServer.url,
        CREATE_LANCELOT_HOST_AGENT_HEALTH_URL: hostAgentServer.url,
      },
      binDir,
    );

    const result = await spawnNode(
      [cliPath, '--directory', installDir, '--provider', 'openai'],
      {
        cwd: installerRoot,
        encoding: 'utf8',
        env,
      },
    );

    assert.equal(result.status, 0, result.stderr);

    const log = JSON.parse(readFileSync(logPath, 'utf8'));
    const dockerLog = readFileSync(dockerLogPath, 'utf8');
    const envContent = readFileSync(path.join(installDir, '.env'), 'utf8');
    const onboardingSnapshot = JSON.parse(
      readFileSync(path.join(installDir, 'lancelot_data', 'onboarding_snapshot.json'), 'utf8'),
    );

    assert.deepEqual(log.errors, []);
    assert.equal(log.success.length, 1);
    assert.ok(log.calls.includes(`cloneRepo:${installDir}`));
    assert.ok(log.calls.includes(`dockerBuild:${installDir}`));
    assert.ok(log.calls.includes(`dockerUp:${installDir}`));
    assert.ok(log.calls.includes('waitForHealthy'));
    assert.ok(log.calls.includes(`startHostAgent:${installDir}`));
    assert.match(dockerLog, /"?compose"?\s+"?pull"?\s+"?lancelot-core"?/i);
    assert.match(dockerLog, /"?compose"?\s+"?pull"?\s+"?local-llm"?/i);
    assert.doesNotMatch(dockerLog, /"?compose"?\s+"?build"?\s+"?local-llm"?/i);
    assert.match(dockerLog, /"?compose"?\s+"?up"?\s+"?-d"?/i);
    assert.match(envContent, /^LANCELOT_PROVIDER=openai$/m);
    assert.match(envContent, /^LANCELOT_CORE_IMAGE=ghcr\.io\/myles1663\/lancelot:latest$/m);
    assert.equal(onboardingSnapshot.state, 'READY');
    assert.ok(readFileSync(path.join(installDir, '.git', 'HEAD'), 'utf8').includes('ref:'));
    assert.ok(healthServer.getHitCount() > 0);
    assert.ok(hostAgentServer.getHitCount() > 0);
  } finally {
    await healthServer.close();
    await hostAgentServer.close();
  }
});

test('generated env selects the staged CUDA local model image when GPU offload is enabled', () => {
  const envContent = generateEnvContent({
    provider: 'openai',
    authMode: 'api_key',
    apiKey: 'sk-test',
    commsType: 'skip',
    hasGpu: true,
    gpuLayers: 15,
    warRoomAuthModel: 'local',
    warRoomUser: 'operator',
    warRoomPassword: 'password123',
  });

  assert.match(envContent, /^LOCAL_LLM_IMAGE=ghcr\.io\/myles1663\/lancelot-local-llm:llama-cpp-0\.3\.19-cu123$/m);
  assert.match(envContent, /^LOCAL_LLM_WHEEL_VARIANT=cu123$/m);
  assert.match(envContent, /^LOCAL_MODEL_GPU_LAYERS=15$/m);
});

test('CLI resume flow uses the real isolated HOME state file and clears it after recovery', async () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'create-lancelot-state-proof-'));
  const homeDir = path.join(tempDir, 'home');
  mkdirSync(homeDir, { recursive: true });
  const installDir = path.join(tempDir, 'lancelot');
  const statePath = path.join(homeDir, '.create-lancelot-state.json');
  const failLogPath = path.join(tempDir, 'runtime-log-fail.json');
  const resumeLogPath = path.join(tempDir, 'runtime-log-resume.json');

  const baseEnv = {
    ...process.env,
    HOME: homeDir,
    USERPROFILE: homeDir,
    CREATE_LANCELOT_RUNTIME_MODULE: stateRuntimeFixturePath,
    INSTALLER_TEMPLATE_SOURCE: templateRepoPath,
  };

  const failed = await spawnNode(
    [cliPath, '--directory', installDir, '--provider', 'openai'],
    {
      cwd: installerRoot,
      encoding: 'utf8',
      env: {
        ...baseEnv,
        INSTALLER_RUNTIME_LOG: failLogPath,
        INSTALLER_FAIL_AT_STEP: 'model',
      },
    },
  );

  assert.equal(failed.status, 1, failed.stderr);
  assert.equal(existsSync(statePath), true);

  const persistedState = JSON.parse(readFileSync(statePath, 'utf8'));
  assert.equal(persistedState.installDir, installDir);
  assert.deepEqual(
    persistedState.completedSteps,
    ['prereqs', 'directory', 'provider', 'comms', 'warroom_auth', 'clone', 'config'],
  );
  assert.equal('apiKey' in persistedState.config, false);
  assert.equal('warRoomPassword' in persistedState.config, false);

  const resumed = await spawnNode(
    [cliPath, '--resume'],
    {
      cwd: installerRoot,
      encoding: 'utf8',
      env: {
        ...baseEnv,
        INSTALLER_RUNTIME_LOG: resumeLogPath,
      },
    },
  );

  assert.equal(resumed.status, 0, resumed.stderr);

  const resumeLog = JSON.parse(readFileSync(resumeLogPath, 'utf8'));
  const onboardingSnapshot = JSON.parse(
    readFileSync(path.join(installDir, 'lancelot_data', 'onboarding_snapshot.json'), 'utf8'),
  );

  assert.ok(resumeLog.infos.some((message) => message.includes(`Resuming installation from: ${installDir}`)));
  assert.ok(resumeLog.calls.includes(`downloadModel:${installDir}`));
  assert.ok(resumeLog.calls.includes(`dockerBuild:${installDir}`));
  assert.ok(resumeLog.calls.includes(`startHostAgent:${installDir}`));
  assert.equal(onboardingSnapshot.state, 'READY');
  assert.equal(existsSync(statePath), false);
});
