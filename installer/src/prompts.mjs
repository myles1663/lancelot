// ============================================================
// Prompts — interactive user prompts
// ============================================================

import { input, select, password, confirm } from '@inquirer/prompts';
import chalk from 'chalk';
import ora from 'ora';
import { PROVIDERS, COMMS, DEFAULT_DIR } from './constants.mjs';
import { validateApiKey } from './validate.mjs';

export async function promptInstallDir(defaultDir) {
  const dir = await input({
    message: 'Where should we install Lancelot?',
    default: defaultDir || DEFAULT_DIR,
  });
  return dir;
}

export async function promptProvider(preselected) {
  if (preselected && PROVIDERS[preselected]) {
    return preselected;
  }

  const choices = Object.entries(PROVIDERS).map(([key, p]) => ({
    name: `${p.name}${p.recommended ? chalk.green(' (recommended)') : ''} — ${p.description}`,
    value: key,
  }));

  return await select({
    message: 'Choose your LLM provider:',
    choices,
  });
}

export async function promptApiKey(provider) {
  const providerInfo = PROVIDERS[provider];

  console.log(chalk.gray(`  Get your key at: ${providerInfo.signupUrl}`));

  while (true) {
    const key = await password({
      message: `Enter your ${providerInfo.name} API key:`,
      mask: '*',
    });

    if (!key || key.trim().length < 10) {
      console.log(chalk.red('  API key seems too short. Please try again.'));
      continue;
    }

    // Validate via HTTP
    const spinner = ora('  Validating API key...').start();
    const result = await validateApiKey(provider, key.trim());

    if (result.valid) {
      if (result.warning) {
        spinner.warn(`  ${result.warning}`);
      } else {
        spinner.succeed('  API key validated');
      }
      return key.trim();
    } else {
      spinner.fail(`  ${result.error}`);
      const retry = await confirm({
        message: 'Try again with a different key?',
        default: true,
      });
      if (!retry) {
        const switchProvider = await confirm({
          message: 'Switch to a different provider?',
          default: false,
        });
        if (switchProvider) return null; // Signal to re-prompt provider
        process.exit(1);
      }
    }
  }
}

export async function promptCommsChannel() {
  const choices = Object.entries(COMMS).map(([key, c]) => ({
    name: `${c.name}${c.recommended ? chalk.green(' (recommended)') : ''} — ${c.description}`,
    value: key,
  }));

  return await select({
    message: 'Choose communication channel:',
    choices,
  });
}

export async function promptTelegramToken() {
  console.log(chalk.gray('  Create a bot via @BotFather on Telegram to get your token.'));
  return await password({
    message: 'Enter your Telegram bot token:',
    mask: '*',
    validate: (val) => {
      if (!val || val.length < 20 || !val.includes(':')) {
        return 'Token should be in format: 123456:ABC-DEF (from BotFather)';
      }
      return true;
    },
  });
}

export async function promptTelegramChatId() {
  console.log(chalk.gray('  Send a message to your bot, then check https://api.telegram.org/bot<TOKEN>/getUpdates'));
  return await input({
    message: 'Enter your Telegram chat ID:',
    validate: (val) => {
      if (!val || !/^-?\d+$/.test(val.trim())) {
        return 'Chat ID should be a number (can be negative for groups)';
      }
      return true;
    },
  });
}

export async function promptGoogleChatSpace() {
  return await input({
    message: 'Enter your Google Chat space name (e.g. spaces/AAAA...):',
    validate: (val) => {
      if (!val || !val.startsWith('spaces/')) {
        return 'Space name should start with "spaces/"';
      }
      return true;
    },
  });
}

export async function promptConfirm(config) {
  const providerInfo = PROVIDERS[config.provider];
  const commsInfo = COMMS[config.commsType];

  console.log('');
  console.log(chalk.white.bold('  Configuration Summary:'));
  console.log(chalk.gray(`    Location:   ${config.installDir}`));
  console.log(chalk.gray(`    Provider:   ${providerInfo?.name || config.provider}`));
  console.log(chalk.gray(`    Comms:      ${commsInfo?.name || config.commsType}`));
  if (config.hasGpu) {
    console.log(chalk.gray(`    GPU:        ${config.gpuName} (${config.gpuLayers} layers)`));
  } else {
    console.log(chalk.gray('    GPU:        CPU only'));
  }
  console.log('');

  return await confirm({
    message: 'Proceed with installation?',
    default: true,
  });
}
