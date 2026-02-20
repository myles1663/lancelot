// ============================================================
// Main — 12-step installer flow
// ============================================================

import path from 'node:path';
import { exec } from 'node:child_process';
import chalk from 'chalk';
import ora from 'ora';

import { PROVIDERS, COMMS } from './constants.mjs';
import { showBanner, showStep, showSuccess, showError, showInfo } from './ui.mjs';
import { runAllChecks } from './prereqs.mjs';
import {
  promptInstallDir, promptProvider, promptAuthMethod, promptApiKey,
  promptCommsChannel, promptTelegramToken, promptTelegramChatId,
  promptGoogleChatSpace, promptConfirm,
} from './prompts.mjs';
import { writeEnvFile, patchDockerCompose } from './config.mjs';
import { downloadModel } from './model.mjs';
import { cloneRepo, dockerBuild, dockerUp, waitForHealthy } from './docker.mjs';
import { markOnboardingComplete } from './onboarding.mjs';
import { loadState, saveState, clearState, isStepComplete } from './state.mjs';

const TOTAL_STEPS = 7;

export async function run(opts) {
  showBanner();

  // Handle Ctrl+C gracefully
  process.on('SIGINT', () => {
    console.log('');
    console.log(chalk.yellow('  Installation interrupted.'));
    console.log(chalk.gray('  Run ') + chalk.white('npx create-lancelot --resume') + chalk.gray(' to continue.'));
    console.log('');
    console.log(chalk.gray("  If this doesn't resolve the issue, open a ticket:"));
    console.log(chalk.cyan.underline('  https://github.com/myles1663/lancelot/issues'));
    console.log('');
    process.exit(130);
  });

  // ── Resume state ──
  let completed = [];
  let config = {
    startedAt: new Date().toISOString(),
    installDir: null,
    provider: opts.provider || null,
    authMode: null,  // 'api_key' or 'oauth'
    apiKey: null,
    commsType: null,
    telegramToken: null,
    telegramChatId: null,
    chatSpaceName: null,
    hasGpu: false,
    gpuLayers: 0,
    gpuName: null,
  };

  if (opts.resume) {
    const state = await loadState();
    if (state) {
      completed = state.completedSteps || [];
      config = { ...config, ...state.config, installDir: state.installDir };
      showInfo(`Resuming installation from: ${state.installDir}`);
      showInfo(`Completed steps: ${completed.join(', ')}`);
    } else {
      showInfo('No previous installation state found. Starting fresh.');
    }
  }

  try {
    // ── Step 1: Prerequisites ──
    if (!isStepComplete(completed, 'prereqs')) {
      showStep(1, TOTAL_STEPS, 'Checking prerequisites');
      const prereqResults = await runAllChecks(config.installDir || opts.directory);
      config.hasGpu = prereqResults.hasGpu;
      config.gpuLayers = prereqResults.gpuLayers;
      config.gpuName = prereqResults.gpuName;
      completed.push('prereqs');
      await saveState(completed, config);
    }

    // ── Step 2: Install directory ──
    if (!isStepComplete(completed, 'directory')) {
      showStep(2, TOTAL_STEPS, 'Installation location');
      const dir = await promptInstallDir(opts.directory);
      config.installDir = path.resolve(dir);
      completed.push('directory');
      await saveState(completed, config);
    }

    // ── Step 3: Provider + API key / OAuth ──
    if (!isStepComplete(completed, 'provider')) {
      showStep(3, TOTAL_STEPS, 'LLM Provider');

      let providerSelected = false;
      while (!providerSelected) {
        config.provider = await promptProvider(config.provider);

        // Check if provider supports OAuth
        config.authMode = await promptAuthMethod(config.provider);

        if (config.authMode === 'oauth') {
          console.log('');
          console.log(chalk.gray('  OAuth selected — you\'ll sign in via browser after Lancelot starts.'));
          console.log('');
          config.apiKey = '';
          providerSelected = true;
        } else {
          const key = await promptApiKey(config.provider);
          if (key === null) {
            // User wants to switch provider
            config.provider = null;
            config.authMode = null;
            continue;
          }
          config.apiKey = key;
          providerSelected = true;
        }
      }

      completed.push('provider');
      await saveState(completed, config);
    } else if (!config.apiKey && config.authMode !== 'oauth') {
      // Resumed — need to re-prompt for API key (never stored, not OAuth)
      showStep(3, TOTAL_STEPS, 'LLM Provider (re-enter API key)');
      config.apiKey = await promptApiKey(config.provider);
    }

    // ── Step 4: Communications ──
    if (!isStepComplete(completed, 'comms')) {
      showStep(4, TOTAL_STEPS, 'Communications');
      config.commsType = await promptCommsChannel();

      if (config.commsType === 'telegram') {
        config.telegramToken = await promptTelegramToken();
        config.telegramChatId = await promptTelegramChatId();
      } else if (config.commsType === 'google_chat') {
        config.chatSpaceName = await promptGoogleChatSpace();
      }

      completed.push('comms');
      await saveState(completed, config);
    }

    // ── Confirm ──
    const proceed = await promptConfirm(config);
    if (!proceed) {
      showInfo('Installation cancelled. Run again to start over.');
      await clearState();
      process.exit(0);
    }

    // ── Step 5: Clone + Configure ──
    if (!isStepComplete(completed, 'clone')) {
      showStep(5, TOTAL_STEPS, 'Setting up project');
      await cloneRepo(config.installDir);
      completed.push('clone');
      await saveState(completed, config);
    }

    if (!isStepComplete(completed, 'config')) {
      const configSpinner = ora('  Generating configuration...').start();
      await writeEnvFile(config.installDir, config);
      await patchDockerCompose(config.installDir, config);
      await markOnboardingComplete(config.installDir, config);
      configSpinner.succeed('  Configuration generated');
      completed.push('config');
      await saveState(completed, config);
    }

    // ── Step 6: Model download ──
    if (!isStepComplete(completed, 'model')) {
      showStep(6, TOTAL_STEPS, 'Downloading local AI model');

      if (opts.skipModel) {
        showInfo('Skipping model download (--skip-model)');
        showInfo('The local utility model will not be available.');
      } else {
        const modelSpinner = ora('  Preparing model download...').start();
        try {
          await downloadModel(config.installDir, (progress) => {
            if (progress.done) {
              modelSpinner.succeed(`  ${progress.message}`);
            } else if (progress.message) {
              modelSpinner.text = `  ${progress.message}`;
            } else {
              const bar = buildProgressBar(progress.percent);
              modelSpinner.text = `  ${bar} ${progress.percent}% — ${progress.downloaded} / ${progress.total} — ${progress.speed}`;
            }
          });
        } catch (e) {
          modelSpinner.fail(`  Model download failed: ${e.message}`);
          showInfo('You can retry with: npx create-lancelot --resume');
          showInfo('Or skip with: npx create-lancelot --skip-model');
          throw e;
        }
      }

      completed.push('model');
      await saveState(completed, config);
    }

    // ── Step 7: Docker build + start ──
    if (!isStepComplete(completed, 'docker_build')) {
      showStep(7, TOTAL_STEPS, 'Building and starting Lancelot');
      await dockerBuild(config.installDir);
      completed.push('docker_build');
      await saveState(completed, config);
    }

    if (!isStepComplete(completed, 'docker_up')) {
      await dockerUp(config.installDir);
      completed.push('docker_up');
      await saveState(completed, config);
    }

    if (!isStepComplete(completed, 'health_check')) {
      await waitForHealthy();
      completed.push('health_check');
      await saveState(completed, config);
    }

    // ── OAuth flow (after Docker is running) ──
    if (config.authMode === 'oauth' && !isStepComplete(completed, 'oauth')) {
      await runOAuthFlow(config);
      completed.push('oauth');
      await saveState(completed, config);
    }

    // ── Done! ──
    completed.push('done');
    await clearState();

    showSuccess({
      directory: config.installDir,
      providerName: PROVIDERS[config.provider]?.name || config.provider,
      commsName: config.commsType === 'skip' ? 'Not configured' : (COMMS[config.commsType]?.name || config.commsType),
    });

    // Auto-open War Room in the default browser
    const warRoomUrl = 'http://localhost:8000';
    const platform = process.platform;
    const openCmd = platform === 'win32' ? `start ${warRoomUrl}`
                  : platform === 'darwin' ? `open ${warRoomUrl}`
                  : `xdg-open ${warRoomUrl}`;
    exec(openCmd, () => {}); // Fire and forget — don't block on failure

  } catch (e) {
    showError(e.message);
    showInfo('Run ' + chalk.white('npx create-lancelot --resume') + ' to continue from where you left off.');
    process.exit(1);
  }
}

async function runOAuthFlow(config) {
  const baseUrl = 'http://localhost:8000';
  const apiToken = config._generatedApiToken;

  console.log('');
  console.log(chalk.white.bold('  Anthropic OAuth Setup'));
  console.log(chalk.gray('  Opening your browser to sign in with Anthropic...'));
  console.log('');

  // Step 1: Initiate OAuth — get the auth URL
  const spinner = ora('  Initiating OAuth flow...').start();
  let authUrl;
  try {
    const resp = await fetch(`${baseUrl}/api/v1/providers/oauth/initiate`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiToken}`,
        'Content-Type': 'application/json',
      },
    });
    const data = await resp.json();
    if (data.status !== 'ok' || !data.auth_url) {
      throw new Error(data.message || 'Failed to generate OAuth URL');
    }
    authUrl = data.auth_url;
    spinner.succeed('  OAuth flow initiated');
  } catch (e) {
    spinner.fail(`  OAuth initiation failed: ${e.message}`);
    console.log('');
    console.log(chalk.yellow('  You can complete OAuth setup later in the War Room.'));
    console.log(chalk.gray('  Go to: http://localhost:8000 → Settings → Provider → Anthropic OAuth'));
    return;
  }

  // Step 2: Open browser
  const platform = process.platform;
  const openCmd = platform === 'win32' ? `start "${authUrl}"`
                : platform === 'darwin' ? `open "${authUrl}"`
                : `xdg-open "${authUrl}"`;
  exec(openCmd, () => {});

  console.log(chalk.cyan('  Browser opened — sign in with your Anthropic account.'));
  console.log(chalk.gray('  Waiting for authorization...'));
  console.log('');

  // Step 3: Poll for completion
  const pollSpinner = ora('  Waiting for OAuth authorization...').start();
  const maxAttempts = 120; // 4 minutes
  const pollInterval = 2000;

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, pollInterval));

    try {
      const resp = await fetch(`${baseUrl}/api/v1/providers/oauth/status`, {
        headers: { 'Authorization': `Bearer ${apiToken}` },
      });
      const status = await resp.json();

      if (status.configured && status.status === 'CONNECTED') {
        pollSpinner.succeed('  OAuth connected — Anthropic account linked!');
        return;
      }
      if (status.status === 'EXPIRED' || status.status === 'error') {
        pollSpinner.fail(`  OAuth failed: ${status.error || status.status}`);
        console.log(chalk.yellow('  You can retry OAuth setup in the War Room.'));
        return;
      }
    } catch {
      // Server might be busy — keep polling
    }
  }

  pollSpinner.warn('  OAuth authorization timed out.');
  console.log(chalk.yellow('  You can complete OAuth setup in the War Room settings.'));
}

function buildProgressBar(percent) {
  const width = 20;
  const filled = Math.round(width * (percent / 100));
  const empty = width - filled;
  return chalk.green('█'.repeat(filled)) + chalk.gray('░'.repeat(empty));
}
