// ============================================================
// Onboarding — write snapshot + USER.md to mark system READY
// ============================================================

import fs from 'node:fs/promises';
import path from 'node:path';

export async function markOnboardingComplete(installDir, config) {
  const dataDir = path.join(installDir, 'lancelot_data');
  await fs.mkdir(dataDir, { recursive: true });

  // Write onboarding_snapshot.json — must match schema in
  // src/core/onboarding_snapshot.py (to_dict method, lines 131-145)
  const snapshot = {
    state: 'READY',
    flagship_provider: config.provider,
    credential_status: config.authMode === 'oauth' ? 'oauth_pending' : 'verified',
    local_model_status: 'verified',
    verification_code_hash: null,
    resend_count: 0,
    last_resend_at: null,
    cooldown_until: null,
    last_error: null,
    temp_data: {},
    updated_at: Date.now() / 1000, // Unix epoch seconds (float)
  };

  await fs.writeFile(
    path.join(dataDir, 'onboarding_snapshot.json'),
    JSON.stringify(snapshot, null, 2),
    'utf8'
  );

  // Write USER.md — required by _determine_state() in onboarding.py
  // Without this file, onboarding returns "WELCOME" state
  const userMd = [
    '# User Profile',
    '- Name: Commander',
    '- Role: Owner',
    '- Bonded: True',
    `- OnboardingComplete: True`,
    `- Provider: ${config.provider}`,
    `- InstalledAt: ${new Date().toISOString()}`,
    '',
  ].join('\n');

  await fs.writeFile(
    path.join(dataDir, 'USER.md'),
    userMd,
    'utf8'
  );
}
