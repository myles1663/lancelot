// ============================================================
// State — resume persistence for interrupted installs
// ============================================================

import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { STEPS } from './constants.mjs';

const STATE_FILE = path.join(os.homedir(), '.create-lancelot-state.json');

export async function loadState() {
  try {
    const raw = await fs.readFile(STATE_FILE, 'utf8');
    const data = JSON.parse(raw);
    if (data.version !== 1) return null;
    return data;
  } catch {
    return null;
  }
}

export async function saveState(completedSteps, config) {
  const data = {
    version: 1,
    installDir: config.installDir || '',
    startedAt: config.startedAt || new Date().toISOString(),
    completedSteps,
    config: {
      provider: config.provider,
      authMode: config.authMode,
      commsType: config.commsType,
      telegramChatId: config.telegramChatId,
      hasGpu: config.hasGpu,
      gpuLayers: config.gpuLayers,
    },
  };
  // Note: API keys and tokens are NEVER stored in state
  const tmp = STATE_FILE + '.tmp';
  await fs.writeFile(tmp, JSON.stringify(data, null, 2));
  await fs.rename(tmp, STATE_FILE);
}

export async function clearState() {
  try {
    await fs.unlink(STATE_FILE);
  } catch {
    // File may not exist
  }
}

export function getResumeStep(completedSteps) {
  for (const step of STEPS) {
    if (!completedSteps.includes(step)) {
      return step;
    }
  }
  return 'done';
}

export function isStepComplete(completedSteps, step) {
  return completedSteps.includes(step);
}
