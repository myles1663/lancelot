import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';

import { cloneRepo, dockerBuild, dockerUp, waitForHealthy, startHostAgent } from '../src/docker.mjs';
import { writeEnvFile, patchDockerCompose } from '../src/config.mjs';
import { markOnboardingComplete } from '../src/onboarding.mjs';

const logPath = process.env.INSTALLER_RUNTIME_LOG;

if (!logPath) {
  throw new Error('INSTALLER_RUNTIME_LOG is required for installer runtime fixture');
}

const state = {
  calls: [],
  saves: [],
  infos: [],
  errors: [],
  exits: [],
  success: [],
  execs: [],
};

function flush() {
  fs.writeFileSync(logPath, JSON.stringify(state, null, 2), 'utf8');
}

function record(key, value) {
  state[key].push(value);
  flush();
}

function createSpinner() {
  return {
    text: '',
    start() {
      return this;
    },
    succeed(message) {
      if (message) record('infos', message);
    },
    fail(message) {
      if (message) record('errors', message);
    },
    warn(message) {
      if (message) record('infos', message);
    },
  };
}

export const runtime = {
  chalk: {
    yellow: (value) => value,
    gray: (value) => value,
    white: Object.assign((value) => value, { bold: (value) => value }),
    cyan: Object.assign((value) => value, { underline: (value) => value }),
    green: (value) => value,
    underline: (value) => value,
  },
  ora: () => createSpinner(),
  exec: (command, callback) => {
    record('execs', command);
    if (callback) callback(null);
  },
  processRef: {
    platform: process.platform,
    env: process.env,
    on() {},
    exit(code) {
      record('exits', code);
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
    record('calls', 'showBanner');
  },
  showStep(step, total, label) {
    record('calls', `step:${step}/${total}:${label}`);
  },
  showSuccess(payload) {
    record('success', payload);
  },
  showError(message) {
    record('errors', message);
  },
  showInfo(message) {
    record('infos', message);
  },
  async runAllChecks(targetDir) {
    record('calls', `runAllChecks:${targetDir}`);
    return { hasGpu: false, gpuLayers: 0, gpuName: null };
  },
  async promptInstallDir(defaultDir) {
    record('calls', `promptInstallDir:${defaultDir}`);
    return defaultDir;
  },
  async promptOwnerName() {
    record('calls', 'promptOwnerName');
    return 'Myles';
  },
  async promptProvider(currentProvider) {
    record('calls', `promptProvider:${currentProvider ?? 'none'}`);
    return currentProvider || 'openai';
  },
  async promptAuthMethod(provider) {
    record('calls', `promptAuthMethod:${provider}`);
    return 'api_key';
  },
  async promptApiKey(provider) {
    record('calls', `promptApiKey:${provider}`);
    return 'sk-test';
  },
  async promptCommsChannel() {
    record('calls', 'promptCommsChannel');
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
    record('calls', 'promptWarRoomAuthModel');
    return 'local';
  },
  async promptWarRoomUsername() {
    record('calls', 'promptWarRoomUsername');
    return 'operator';
  },
  async promptWarRoomPassword() {
    record('calls', 'promptWarRoomPassword');
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
    record('calls', 'promptConfirm');
    return true;
  },
  async cloneRepo(installDir) {
    record('calls', `cloneRepo:${installDir}`);
    await cloneRepo(installDir);
  },
  async writeEnvFile(installDir, config) {
    record('calls', `writeEnvFile:${installDir}`);
    await writeEnvFile(installDir, config);
  },
  async patchDockerCompose(installDir, config) {
    record('calls', `patchDockerCompose:${installDir}`);
    await patchDockerCompose(installDir, config);
  },
  async markOnboardingComplete(installDir, config) {
    record('calls', `markOnboardingComplete:${installDir}`);
    await markOnboardingComplete(installDir, config);
  },
  async downloadModel(installDir, onProgress) {
    record('calls', `downloadModel:${installDir}`);
    const weightsDir = path.join(installDir, 'local_models', 'weights');
    await fsp.mkdir(weightsDir, { recursive: true });
    await fsp.writeFile(path.join(weightsDir, 'smoke-model.gguf'), 'fixture-model', 'utf8');
    onProgress({ done: true, message: 'downloaded' });
  },
  async dockerBuild(installDir) {
    record('calls', `dockerBuild:${installDir}`);
    await dockerBuild(installDir);
  },
  async dockerUp(installDir) {
    record('calls', `dockerUp:${installDir}`);
    await dockerUp(installDir);
  },
  async waitForHealthy() {
    record('calls', 'waitForHealthy');
    return waitForHealthy();
  },
  async startHostAgent(installDir) {
    record('calls', `startHostAgent:${installDir}`);
    await startHostAgent(installDir);
  },
  async loadState() {
    record('calls', 'loadState');
    return null;
  },
  async saveState(completed, config) {
    record('saves', {
      completed: [...completed],
      config: JSON.parse(JSON.stringify(config)),
    });
  },
  async clearState() {
    record('calls', 'clearState');
  },
  isStepComplete(completed, step) {
    return completed.includes(step);
  },
};

flush();
