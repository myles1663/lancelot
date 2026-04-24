// ============================================================
// Main - installer flow
// ============================================================

import path from 'node:path';
import { exec } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import chalk from 'chalk';
import ora from 'ora';

import { PROVIDERS, COMMS } from './constants.mjs';
import { showBanner, showStep, showSuccess, showError, showInfo } from './ui.mjs';
import { runAllChecks } from './prereqs.mjs';
import {
  promptInstallDir, promptOwnerName, promptProvider, promptAuthMethod, promptApiKey,
  promptCommsChannel, promptTelegramToken, promptTelegramChatId,
  promptGoogleChatSpace, promptWarRoomAuthModel, promptWarRoomUsername, promptWarRoomPassword,
  promptOidcIssuerUrl, promptOidcClientId, promptOidcClientSecret, promptOidcBaseUrl,
  promptOidcAllowedGroups, promptConfirm,
} from './prompts.mjs';
import { writeEnvFile, patchDockerCompose } from './config.mjs';
import { downloadModel } from './model.mjs';
import { cloneRepo, dockerBuild, dockerUp, waitForHealthy, startHostAgent } from './docker.mjs';
import { markOnboardingComplete } from './onboarding.mjs';
import { loadState, saveState, clearState, isStepComplete } from './state.mjs';

const TOTAL_STEPS = 8;

const DEFAULT_RUNTIME = {
  chalk,
  ora,
  exec,
  processRef: process,
  PROVIDERS,
  COMMS,
  showBanner,
  showStep,
  showSuccess,
  showError,
  showInfo,
  runAllChecks,
  promptInstallDir,
  promptOwnerName,
  promptProvider,
  promptAuthMethod,
  promptApiKey,
  promptCommsChannel,
  promptTelegramToken,
  promptTelegramChatId,
  promptGoogleChatSpace,
  promptWarRoomAuthModel,
  promptWarRoomUsername,
  promptWarRoomPassword,
  promptOidcIssuerUrl,
  promptOidcClientId,
  promptOidcClientSecret,
  promptOidcBaseUrl,
  promptOidcAllowedGroups,
  promptConfirm,
  writeEnvFile,
  patchDockerCompose,
  downloadModel,
  cloneRepo,
  dockerBuild,
  dockerUp,
  waitForHealthy,
  startHostAgent,
  markOnboardingComplete,
  loadState,
  saveState,
  clearState,
  isStepComplete,
};

async function loadRuntimeOverride(processRef = process) {
  const specifier = processRef.env?.CREATE_LANCELOT_RUNTIME_MODULE;
  if (!specifier) {
    return null;
  }

  const runtimeModule = specifier.includes('://')
    ? specifier
    : pathToFileURL(specifier).href;
  const loaded = await import(runtimeModule);
  return loaded.runtime || loaded.default || null;
}

function registerSigintHandler(runtime) {
  runtime.processRef.on('SIGINT', () => {
    console.log('');
    console.log(runtime.chalk.yellow('  Installation interrupted.'));
    console.log(runtime.chalk.gray('  Run ') + runtime.chalk.white('npx create-lancelot --resume') + runtime.chalk.gray(' to continue.'));
    console.log('');
    console.log(runtime.chalk.gray("  If this doesn't resolve the issue, open a ticket:"));
    console.log(runtime.chalk.cyan.underline('  https://github.com/myles1663/lancelot/issues'));
    console.log('');
    runtime.processRef.exit(130);
  });
}

function createInitialConfig(opts) {
  return {
    startedAt: new Date().toISOString(),
    installDir: null,
    ownerName: null,
    provider: opts.provider || null,
    authMode: null,
    apiKey: null,
    commsType: null,
    telegramToken: null,
    telegramChatId: null,
    chatSpaceName: null,
    hasGpu: false,
    gpuLayers: 0,
    gpuName: null,
    warRoomAuthModel: null,
    warRoomPasswordResetCode: null,
    oidcIssuerUrl: null,
    oidcClientId: null,
    oidcClientSecret: null,
    oidcBaseUrl: null,
    oidcAllowedGroups: null,
    oidcAllowAnyAuthenticated: false,
  };
}

export async function run(opts, runtime = DEFAULT_RUNTIME) {
  runtime.showBanner();
  registerSigintHandler(runtime);

  let completed = [];
  let config = createInitialConfig(opts);

  if (opts.resume) {
    const state = await runtime.loadState();
    if (state) {
      completed = state.completedSteps || [];
      config = { ...config, ...state.config, installDir: state.installDir };
      runtime.showInfo(`Resuming installation from: ${state.installDir}`);
      runtime.showInfo(`Completed steps: ${completed.join(', ')}`);
    } else {
      runtime.showInfo('No previous installation state found. Starting fresh.');
    }
  }

  try {
    if (!runtime.isStepComplete(completed, 'prereqs')) {
      runtime.showStep(1, TOTAL_STEPS, 'Checking prerequisites');
      const prereqResults = await runtime.runAllChecks(config.installDir || opts.directory);
      config.hasGpu = prereqResults.hasGpu;
      config.gpuLayers = prereqResults.gpuLayers;
      config.gpuName = prereqResults.gpuName;
      completed.push('prereqs');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'directory')) {
      runtime.showStep(2, TOTAL_STEPS, 'Installation location');
      const dir = await runtime.promptInstallDir(opts.directory);
      config.installDir = path.resolve(dir);
      completed.push('directory');
      await runtime.saveState(completed, config);
    }

    if (!config.ownerName) {
      config.ownerName = await runtime.promptOwnerName();
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'provider')) {
      runtime.showStep(3, TOTAL_STEPS, 'LLM Provider');

      let providerSelected = false;
      while (!providerSelected) {
        config.provider = await runtime.promptProvider(config.provider);
        config.authMode = await runtime.promptAuthMethod(config.provider);

        if (config.authMode === 'oauth') {
          console.log('');
          console.log(runtime.chalk.gray("  OAuth selected - you'll sign in via browser after Lancelot starts."));
          console.log('');
          config.apiKey = '';
          providerSelected = true;
        } else {
          const key = await runtime.promptApiKey(config.provider);
          if (key === null) {
            config.provider = null;
            config.authMode = null;
            continue;
          }
          config.apiKey = key;
          providerSelected = true;
        }
      }

      completed.push('provider');
      await runtime.saveState(completed, config);
    } else if (!config.apiKey && config.authMode !== 'oauth') {
      runtime.showStep(3, TOTAL_STEPS, 'LLM Provider (re-enter API key)');
      config.apiKey = await runtime.promptApiKey(config.provider);
    }

    if (!runtime.isStepComplete(completed, 'comms')) {
      runtime.showStep(4, TOTAL_STEPS, 'Communications');
      config.commsType = await runtime.promptCommsChannel();

      if (config.commsType === 'telegram') {
        config.telegramToken = await runtime.promptTelegramToken();
        config.telegramChatId = await runtime.promptTelegramChatId();
      } else if (config.commsType === 'google_chat') {
        config.chatSpaceName = await runtime.promptGoogleChatSpace();
      }

      completed.push('comms');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'warroom_auth')) {
      runtime.showStep(5, TOTAL_STEPS, 'War Room Authentication');
      console.log(runtime.chalk.gray('  The War Room is your command center for monitoring and controlling Lancelot.'));
      config.warRoomAuthModel = await runtime.promptWarRoomAuthModel();

      if (config.warRoomAuthModel === 'oidc') {
        config.oidcIssuerUrl = await runtime.promptOidcIssuerUrl();
        config.oidcClientId = await runtime.promptOidcClientId();
        config.oidcClientSecret = await runtime.promptOidcClientSecret();
        config.oidcBaseUrl = await runtime.promptOidcBaseUrl();
        config.oidcAllowedGroups = await runtime.promptOidcAllowedGroups();
        config.oidcAllowAnyAuthenticated = String(config.oidcAllowedGroups || '').trim().toLowerCase() === 'open';
        if (config.oidcAllowAnyAuthenticated) {
          config.oidcAllowedGroups = '';
        }
      } else {
        config.warRoomUser = await runtime.promptWarRoomUsername();
        config.warRoomPassword = await runtime.promptWarRoomPassword();
      }

      completed.push('warroom_auth');
      await runtime.saveState(completed, config);
    }

    const proceed = await runtime.promptConfirm(config);
    if (!proceed) {
      runtime.showInfo('Installation cancelled. Run again to start over.');
      await runtime.clearState();
      runtime.processRef.exit(0);
      return;
    }

    if (runtime.isStepComplete(completed, 'warroom_auth')) {
      if (config.warRoomAuthModel === 'oidc' && !config.oidcClientSecret) {
        runtime.showStep(5, TOTAL_STEPS, 'War Room Enterprise Authentication (re-enter secret)');
        config.oidcClientSecret = await runtime.promptOidcClientSecret();
      } else if (config.warRoomAuthModel !== 'oidc' && !config.warRoomPassword) {
        runtime.showStep(5, TOTAL_STEPS, 'War Room Login (re-enter credentials)');
        config.warRoomUser = await runtime.promptWarRoomUsername();
        config.warRoomPassword = await runtime.promptWarRoomPassword();
      }
    }

    if (!runtime.isStepComplete(completed, 'clone')) {
      runtime.showStep(6, TOTAL_STEPS, 'Setting up project');
      await runtime.cloneRepo(config.installDir);
      completed.push('clone');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'config')) {
      const configSpinner = runtime.ora('  Generating configuration...').start();
      await runtime.writeEnvFile(config.installDir, config);
      await runtime.patchDockerCompose(config.installDir, config);
      await runtime.markOnboardingComplete(config.installDir, config);
      configSpinner.succeed('  Configuration generated');
      completed.push('config');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'model')) {
      runtime.showStep(7, TOTAL_STEPS, 'Downloading local AI model');
      const modelSpinner = runtime.ora('  Preparing model download...').start();

      try {
        await runtime.downloadModel(config.installDir, (progress) => {
          if (progress.done) {
            modelSpinner.succeed(`  ${progress.message}`);
          } else if (progress.message) {
            modelSpinner.text = `  ${progress.message}`;
          } else {
            const bar = buildProgressBar(progress.percent, runtime.chalk);
            modelSpinner.text = `  ${bar} ${progress.percent}% - ${progress.downloaded} / ${progress.total} - ${progress.speed}`;
          }
        });
      } catch (error) {
        modelSpinner.fail(`  Model download failed: ${error.message}`);
        runtime.showInfo('You can retry with: npx create-lancelot --resume');
        throw error;
      }

      completed.push('model');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'docker_build')) {
      runtime.showStep(8, TOTAL_STEPS, 'Pulling images and starting Lancelot');
      await runtime.dockerBuild(config.installDir);
      completed.push('docker_build');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'docker_up')) {
      await runtime.dockerUp(config.installDir);
      completed.push('docker_up');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'health_check')) {
      await runtime.waitForHealthy();
      completed.push('health_check');
      await runtime.saveState(completed, config);
    }

    if (!runtime.isStepComplete(completed, 'host_agent')) {
      await runtime.startHostAgent(config.installDir);
      completed.push('host_agent');
      await runtime.saveState(completed, config);
    }

    if (config.authMode === 'oauth' && !runtime.isStepComplete(completed, 'oauth')) {
      await runOAuthFlow(config, runtime);
      completed.push('oauth');
      await runtime.saveState(completed, config);
    }

    completed.push('done');
    await runtime.clearState();

    runtime.showSuccess({
      directory: config.installDir,
      providerName: runtime.PROVIDERS[config.provider]?.name || config.provider,
      commsName: config.commsType === 'skip' ? 'Not configured' : (runtime.COMMS[config.commsType]?.name || config.commsType),
      warRoomAuthModel: config.warRoomAuthModel || 'local',
      warRoomUser: config.warRoomUser || '',
      warRoomPassword: config.warRoomPassword,
      warRoomPasswordResetCode: config.warRoomPasswordResetCode,
      oidcIssuerUrl: config.oidcIssuerUrl,
    });

    const warRoomUrl = 'http://localhost:8000';
    const platform = runtime.processRef.platform;
    const openCmd = platform === 'win32' ? `start ${warRoomUrl}`
      : platform === 'darwin' ? `open ${warRoomUrl}`
        : `xdg-open ${warRoomUrl}`;
    runtime.exec(openCmd, () => {});
  } catch (error) {
    runtime.showError(error.message);
    runtime.showInfo(`Run ${runtime.chalk.white('npx create-lancelot --resume')} to continue from where you left off.`);
    runtime.processRef.exit(1);
  }
}

export async function runCli(opts, processRef = process) {
  const override = await loadRuntimeOverride(processRef);
  return run(opts, override || DEFAULT_RUNTIME);
}

async function runOAuthFlow(config, runtime = DEFAULT_RUNTIME) {
  const baseUrl = 'http://localhost:8000';
  const apiToken = config._generatedApiToken;

  console.log('');
  console.log(runtime.chalk.white.bold('  Anthropic OAuth Setup'));
  console.log(runtime.chalk.gray('  Opening your browser to sign in with Anthropic...'));
  console.log('');

  const spinner = runtime.ora('  Initiating OAuth flow...').start();
  let authUrl;

  try {
    const response = await fetch(`${baseUrl}/api/v1/providers/oauth/initiate`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiToken}`,
        'Content-Type': 'application/json',
      },
    });
    const data = await response.json();
    if (data.status !== 'ok' || !data.auth_url) {
      throw new Error(data.message || 'Failed to generate OAuth URL');
    }
    authUrl = data.auth_url;
    spinner.succeed('  OAuth flow initiated');
  } catch (error) {
    spinner.fail(`  OAuth initiation failed: ${error.message}`);
    console.log('');
    console.log(runtime.chalk.yellow('  You can complete OAuth setup later in the War Room.'));
    console.log(runtime.chalk.gray('  Go to: http://localhost:8000 -> Settings -> Provider -> Anthropic OAuth'));
    return;
  }

  const platform = runtime.processRef.platform;
  const openCmd = platform === 'win32' ? `start "" "${authUrl}"`
    : platform === 'darwin' ? `open "${authUrl}"`
      : `xdg-open "${authUrl}"`;
  runtime.exec(openCmd, (err) => {
    if (err) {
      console.log(runtime.chalk.yellow('  Could not open browser automatically.'));
      console.log(runtime.chalk.yellow('  Please open the URL above manually.'));
    }
  });

  console.log(runtime.chalk.cyan('  Browser opened - sign in with your Anthropic account.'));
  console.log('');
  console.log(runtime.chalk.white('  If the browser did not open, visit this URL manually:'));
  console.log(runtime.chalk.underline.cyan(`  ${authUrl}`));
  console.log('');
  console.log(runtime.chalk.gray('  Waiting for authorization... (press Ctrl+C to skip - you can finish in War Room)'));
  console.log('');

  const pollSpinner = runtime.ora('  Waiting for OAuth authorization...').start();
  const maxAttempts = 60;
  const pollInterval = 2000;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, pollInterval));

    try {
      const response = await fetch(`${baseUrl}/api/v1/providers/oauth/status`, {
        headers: { Authorization: `Bearer ${apiToken}` },
      });
      const status = await response.json();

      if (status.configured && status.status === 'CONNECTED') {
        pollSpinner.succeed('  OAuth connected - Anthropic account linked!');
        return;
      }
      if (status.status === 'EXPIRED' || status.status === 'error') {
        pollSpinner.fail(`  OAuth failed: ${status.error || status.status}`);
        console.log(runtime.chalk.yellow('  You can retry OAuth setup in the War Room.'));
        return;
      }
    } catch {
      // Server might still be starting; keep polling.
    }
  }

  pollSpinner.warn('  OAuth authorization timed out.');
  console.log(runtime.chalk.yellow('  You can complete OAuth setup in the War Room settings.'));
}

function buildProgressBar(percent, runtimeChalk = chalk) {
  const width = 20;
  const filled = Math.round(width * (percent / 100));
  const empty = width - filled;
  return runtimeChalk.green('*'.repeat(filled)) + runtimeChalk.gray('.'.repeat(empty));
}
