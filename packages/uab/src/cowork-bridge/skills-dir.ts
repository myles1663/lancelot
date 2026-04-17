/**
 * Skills Directory Detection — Claude Code CLI + Co-work
 *
 * Installs the UAB skill into BOTH plugin systems:
 *
 * 1. Claude Code CLI: ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/uab-bridge/
 * 2. Co-work: %APPDATA%/Claude/local-agent-mode-sessions/{session}/{workspace}/cowork_plugins/
 *    marketplaces/knowledge-work-plugins/uab-desktop-control/
 *
 * Both use the same SKILL.md format with frontmatter.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync } from 'fs';
import { join } from 'path';
import { homedir, platform } from 'os';
import { createLogger } from '../logger.js';

const log = createLogger('skills-dir');

const PLUGIN_NAME = 'uab-bridge';
const SKILL_NAME = 'uab-bridge';
const CLI_MARKETPLACE = 'claude-plugins-official';
const COWORK_MARKETPLACE = 'knowledge-work-plugins';
const COWORK_PLUGIN_NAME = 'uab-desktop-control';

export interface SkillsDirResult {
  path: string;
  method: 'env' | 'known-path' | 'config-search' | 'created';
  exists: boolean;
  created: boolean;
  /** Full path to the SKILL.md file (CLI) */
  skillFilePath: string;
  /** Path to the plugin root (contains skills/ dir) */
  pluginRoot: string;
  /** Co-work skill paths (may be multiple sessions) */
  coworkPaths: string[];
}

/**
 * Find or create the UAB plugin directories for BOTH Claude Code CLI and Co-work.
 */
export async function findCoworkSkillsDir(): Promise<SkillsDirResult> {
  const home = homedir();

  // ─── Claude Code CLI plugin directory ──────────────────────
  const claudeDir = join(home, '.claude');
  const pluginsBase = join(claudeDir, 'plugins', 'marketplaces', CLI_MARKETPLACE, 'plugins');
  const pluginRoot = join(pluginsBase, PLUGIN_NAME);
  const skillsDir = join(pluginRoot, 'skills');
  const skillDir = join(skillsDir, SKILL_NAME);
  const skillFilePath = join(skillDir, 'SKILL.md');

  const existed = existsSync(skillDir);
  if (!existed) {
    mkdirSync(skillDir, { recursive: true });
  }

  // ─── Co-work plugin directories ────────────────────────────
  const coworkPaths = findCoworkPluginDirs();

  return {
    path: skillsDir,
    method: existed ? 'known-path' : 'created',
    exists: existed,
    created: !existed,
    skillFilePath,
    pluginRoot,
    coworkPaths,
  };
}

/**
 * Find all Co-work session plugin directories.
 * Co-work stores plugins at:
 *   %APPDATA%/Claude/local-agent-mode-sessions/{session}/{workspace}/cowork_plugins/
 */
function findCoworkPluginDirs(): string[] {
  const paths: string[] = [];
  const os = platform();

  let sessionsDir: string;
  if (os === 'darwin') {
    sessionsDir = join(homedir(), 'Library', 'Application Support', 'Claude', 'local-agent-mode-sessions');
  } else if (os === 'win32') {
    const appData = process.env.APPDATA || '';
    if (!appData) return paths;
    sessionsDir = join(appData, 'Claude', 'local-agent-mode-sessions');
  } else {
    // Linux: check XDG_CONFIG_HOME or ~/.config
    sessionsDir = join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), 'Claude', 'local-agent-mode-sessions');
  }

  if (!existsSync(sessionsDir)) return paths;

  try {
    for (const session of readdirSync(sessionsDir, { withFileTypes: true })) {
      if (!session.isDirectory()) continue;
      const sessionPath = join(sessionsDir, session.name);
      for (const workspace of readdirSync(sessionPath, { withFileTypes: true })) {
        if (!workspace.isDirectory()) continue;
        const coworkPlugins = join(sessionPath, workspace.name, 'cowork_plugins');
        if (existsSync(coworkPlugins)) {
          const skillDir = join(
            coworkPlugins, 'marketplaces', COWORK_MARKETPLACE,
            COWORK_PLUGIN_NAME, 'skills', SKILL_NAME,
          );
          paths.push(skillDir);
        }
      }
    }
  } catch {
    log.warn('Could not enumerate Co-work sessions');
  }

  return paths;
}

/**
 * Write the skill file to ALL detected locations (CLI + Co-work).
 */
export function writeSkillToAllLocations(
  cliSkillPath: string,
  coworkPaths: string[],
  content: string,
): { cli: boolean; cowork: number } {
  let cli = false;
  let cowork = 0;

  // Write to CLI location
  try {
    mkdirSync(join(cliSkillPath, '..'), { recursive: true });
    writeFileSync(cliSkillPath, content, 'utf-8');
    cli = true;
    log.info(`Wrote CLI skill: ${cliSkillPath}`);
  } catch (err) {
    log.error(`Failed to write CLI skill: ${err}`);
  }

  // Write to all Co-work sessions
  for (const coworkDir of coworkPaths) {
    try {
      mkdirSync(coworkDir, { recursive: true });
      writeFileSync(join(coworkDir, 'SKILL.md'), content, 'utf-8');

      // Also write README.md in the plugin root
      const pluginRoot = join(coworkDir, '..', '..');
      const readme = '# UAB Desktop Control\n\nGives Claude native control of desktop applications via the Universal App Bridge (UAB).\n';
      if (!existsSync(join(pluginRoot, 'README.md'))) {
        writeFileSync(join(pluginRoot, 'README.md'), readme, 'utf-8');
      }

      cowork++;
      log.info(`Wrote Co-work skill: ${coworkDir}`);
    } catch (err) {
      log.warn(`Failed to write Co-work skill: ${err}`);
    }
  }

  return { cli, cowork };
}

/**
 * Register UAB as an enabled plugin in Claude Code CLI settings.
 */
export async function registerPlugin(): Promise<{ success: boolean; message: string }> {
  const home = homedir();
  const claudeDir = join(home, '.claude');
  const pluginsDir = join(claudeDir, 'plugins');
  const pluginKey = `${PLUGIN_NAME}@${CLI_MARKETPLACE}`;

  try {
    // Update installed_plugins.json
    const installedPath = join(pluginsDir, 'installed_plugins.json');
    let installed: Record<string, unknown> = { version: 2, plugins: {} };

    if (existsSync(installedPath)) {
      try { installed = JSON.parse(readFileSync(installedPath, 'utf-8')); } catch { /* */ }
    } else {
      mkdirSync(pluginsDir, { recursive: true });
    }

    const plugins = (installed.plugins || {}) as Record<string, unknown[]>;
    if (!plugins[pluginKey]) {
      const pluginsBase = join(pluginsDir, 'marketplaces', CLI_MARKETPLACE, 'plugins');
      const pluginRoot = join(pluginsBase, PLUGIN_NAME);

      plugins[pluginKey] = [{
        scope: 'user',
        installPath: pluginRoot,
        version: 'local',
        installedAt: new Date().toISOString(),
        lastUpdated: new Date().toISOString(),
      }];
      installed.plugins = plugins;
      writeFileSync(installedPath, JSON.stringify(installed, null, 2), 'utf-8');
    }

    // Update settings.json
    const settingsPath = join(claudeDir, 'settings.json');
    let settings: Record<string, unknown> = {};
    if (existsSync(settingsPath)) {
      try { settings = JSON.parse(readFileSync(settingsPath, 'utf-8')); } catch { /* */ }
    }

    const enabled = (settings.enabledPlugins || {}) as Record<string, boolean>;
    if (!enabled[pluginKey]) {
      enabled[pluginKey] = true;
      settings.enabledPlugins = enabled;
      writeFileSync(settingsPath, JSON.stringify(settings, null, 2), 'utf-8');
    }

    return { success: true, message: `Plugin registered and enabled.` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, message: msg };
  }
}

/**
 * Unregister UAB plugin from Claude Code settings.
 */
export async function unregisterPlugin(): Promise<{ success: boolean; message: string }> {
  const home = homedir();
  const claudeDir = join(home, '.claude');
  const pluginKey = `${PLUGIN_NAME}@${CLI_MARKETPLACE}`;

  try {
    const installedPath = join(claudeDir, 'plugins', 'installed_plugins.json');
    if (existsSync(installedPath)) {
      const installed = JSON.parse(readFileSync(installedPath, 'utf-8'));
      delete (installed.plugins || {})[pluginKey];
      writeFileSync(installedPath, JSON.stringify(installed, null, 2), 'utf-8');
    }

    const settingsPath = join(claudeDir, 'settings.json');
    if (existsSync(settingsPath)) {
      const settings = JSON.parse(readFileSync(settingsPath, 'utf-8'));
      delete (settings.enabledPlugins || {})[pluginKey];
      writeFileSync(settingsPath, JSON.stringify(settings, null, 2), 'utf-8');
    }

    return { success: true, message: 'Plugin unregistered.' };
  } catch (err) {
    return { success: false, message: err instanceof Error ? err.message : String(err) };
  }
}
