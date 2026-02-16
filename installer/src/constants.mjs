// ============================================================
// Constants — shared config for the create-lancelot installer
// ============================================================

export const REPO_URL = 'https://github.com/myles1663/lancelot.git';
export const DEFAULT_DIR = './lancelot';

export const PROVIDERS = {
  gemini: {
    name: 'Google Gemini',
    envVar: 'GEMINI_API_KEY',
    envProvider: 'gemini',
    recommended: true,
    description: 'Generous free tier, fast models',
    keyPrefix: 'AIza',
    validationUrl: 'https://generativelanguage.googleapis.com/v1beta/models',
    signupUrl: 'https://aistudio.google.com/apikey',
  },
  openai: {
    name: 'OpenAI',
    envVar: 'OPENAI_API_KEY',
    envProvider: 'openai',
    recommended: false,
    description: 'GPT-4o, pay-as-you-go',
    keyPrefix: 'sk-',
    validationUrl: 'https://api.openai.com/v1/models',
    signupUrl: 'https://platform.openai.com/api-keys',
  },
  anthropic: {
    name: 'Anthropic',
    envVar: 'ANTHROPIC_API_KEY',
    envProvider: 'anthropic',
    recommended: false,
    description: 'Claude, pay-as-you-go',
    keyPrefix: 'sk-ant-',
    validationUrl: 'https://api.anthropic.com/v1/messages',
    signupUrl: 'https://console.anthropic.com/',
  },
  xai: {
    name: 'xAI (Grok)',
    envVar: 'XAI_API_KEY',
    envProvider: 'xai',
    recommended: false,
    description: 'Grok models, pay-as-you-go',
    keyPrefix: 'xai-',
    validationUrl: 'https://api.x.ai/v1/models',
    signupUrl: 'https://console.x.ai/',
  },
};

export const COMMS = {
  telegram: {
    name: 'Telegram',
    recommended: true,
    description: 'Simple setup via BotFather',
  },
  google_chat: {
    name: 'Google Chat',
    recommended: false,
    description: 'Requires Google Cloud project',
  },
  skip: {
    name: 'Skip for now',
    recommended: false,
    description: 'Configure later in the War Room',
  },
};

export const MIN_DISK_GB = 10;
export const MIN_RAM_GB = 8;

export const HEALTH_CHECK_URL = 'http://localhost:8000/health';
export const HEALTH_CHECK_INTERVAL_MS = 3000;
export const HEALTH_CHECK_MAX_ATTEMPTS = 60; // 3 min timeout

export const STEPS = [
  'prereqs',
  'directory',
  'clone',
  'provider',
  'comms',
  'config',
  'model',
  'docker_build',
  'docker_up',
  'health_check',
  'onboarding',
  'done',
];
