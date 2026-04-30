/**
 * UAB Bridge Daemon (System Service)
 *
 * Installs UABServer as a background service that starts on boot.
 * - Mac: launchd (~/Library/LaunchAgents/)
 * - Windows: Task Scheduler (schtasks, user-level, no admin required)
 *
 * IMPORTANT: On Windows, all schtasks commands are run via cmd.exe
 * to avoid Git Bash path mangling (e.g., /create → C:/Program Files/Git/create).
 */

import { execSync } from 'child_process';
import { existsSync, writeFileSync, unlinkSync, mkdirSync } from 'fs';
import { join, resolve } from 'path';
import { homedir, platform } from 'os';
import { createLogger } from '../logger.js';

const log = createLogger('daemon');

const TASK_NAME = 'UAB Bridge';
const LABEL = 'com.lancelot.uab-bridge';
const PORT = '3100';
const HOST = '0.0.0.0'; // Bind to all interfaces so VMs can reach the host

/**
 * Run a command via cmd.exe on Windows to avoid Git Bash path mangling.
 * schtasks flags like /create, /query, /run get rewritten by MSYS2
 * unless we go through cmd.exe directly.
 */
function winExec(cmd: string): string {
  return execSync(cmd, {
    stdio: 'pipe',
    shell: 'cmd.exe',
    windowsHide: true,
  }).toString().trim();
}

function getNodePath(): string {
  return process.execPath;
}

function getCliPath(): string {
  return resolve('dist/cli.js');
}

function getInstallDir(): string {
  return resolve('.');
}

function getLogDir(): string {
  const home = homedir();
  if (platform() === 'darwin') {
    return join(home, 'Library', 'Logs', 'UAB Bridge');
  }
  const localAppData = process.env.LOCALAPPDATA || join(home, 'AppData', 'Local');
  return join(localAppData, 'UAB Bridge', 'Logs');
}

function getPlistPath(): string {
  return join(homedir(), 'Library', 'LaunchAgents', `${LABEL}.plist`);
}

// ─── Install ───────────────────────────────────────────────────

export async function installDaemon(apiKey?: string): Promise<{ success: boolean; message: string }> {
  const os = platform();
  const logDir = getLogDir();
  mkdirSync(logDir, { recursive: true });

  // Enable CDP remote debugging for all Electron apps.
  // This lets UAB connect via Chrome DevTools Protocol to get full DOM access
  // for apps like ChatGPT, VS Code, Slack, Discord, Teams, etc.
  enableElectronDebugging();

  try {
    if (os === 'darwin') {
      return installMac(logDir, apiKey);
    } else if (os === 'win32') {
      return installWindows(apiKey);
    } else {
      return { success: false, message: `Unsupported platform: ${os}` };
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log.error(`Failed to install daemon: ${msg}`);
    return { success: false, message: msg };
  }
}

function installMac(logDir: string, apiKey?: string): { success: boolean; message: string } {
  const nodePath = getNodePath();
  const cliPath = getCliPath();
  const installDir = getInstallDir();
  const plistPath = getPlistPath();

  const keyArgs = apiKey ? `
    <string>--api-key</string>
    <string>${apiKey}</string>` : '';

  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${nodePath}</string>
    <string>${cliPath}</string>
    <string>serve</string>
    <string>--host</string>
    <string>${HOST}</string>
    <string>--port</string>
    <string>${PORT}</string>${keyArgs}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${logDir}/uab-bridge.log</string>
  <key>StandardErrorPath</key>
  <string>${logDir}/uab-bridge-error.log</string>
  <key>WorkingDirectory</key>
  <string>${installDir}</string>
</dict>
</plist>`;

  const agentsDir = join(homedir(), 'Library', 'LaunchAgents');
  mkdirSync(agentsDir, { recursive: true });
  writeFileSync(plistPath, plist, 'utf-8');
  execSync(`launchctl load "${plistPath}"`, { stdio: 'pipe' });
  log.info('Daemon installed via launchd');
  return { success: true, message: 'UAB Bridge service installed (launchd).' };
}

function installWindows(apiKey?: string): { success: boolean; message: string } {
  const nodePath = getNodePath().replace(/\//g, '\\');
  const cliPath = getCliPath().replace(/\//g, '\\');

  // Build the command that Task Scheduler will run
  const keyArg = apiKey ? ` --api-key ${apiKey}` : '';
  const taskCommand = `"${nodePath}" "${cliPath}" serve --host ${HOST} --port ${PORT}${keyArg}`;
  const username = process.env.USERNAME || '';

  winExec(
    `schtasks /create /tn "${TASK_NAME}" /tr "${taskCommand}" /sc ONLOGON /ru "${username}" /f`,
  );
  log.info('Daemon installed via Task Scheduler');
  return { success: true, message: 'UAB Bridge service installed (Task Scheduler).' };
}

// ─── Uninstall ─────────────────────────────────────────────────

export async function uninstallDaemon(): Promise<{ success: boolean; message: string }> {
  const os = platform();

  try {
    if (os === 'darwin') {
      const plistPath = getPlistPath();
      if (existsSync(plistPath)) {
        try { execSync(`launchctl unload "${plistPath}"`, { stdio: 'pipe' }); } catch { /* already unloaded */ }
        unlinkSync(plistPath);
      }
      return { success: true, message: 'UAB Bridge service removed (launchd).' };
    } else if (os === 'win32') {
      try {
        winExec(`schtasks /delete /tn "${TASK_NAME}" /f`);
      } catch { /* task may not exist */ }
      return { success: true, message: 'UAB Bridge service removed (Task Scheduler).' };
    }
    return { success: false, message: `Unsupported platform: ${os}` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, message: msg };
  }
}

// ─── Status ────────────────────────────────────────────────────

export async function isDaemonInstalled(): Promise<boolean> {
  const os = platform();
  try {
    if (os === 'darwin') {
      return existsSync(getPlistPath());
    } else if (os === 'win32') {
      winExec(`schtasks /query /tn "${TASK_NAME}"`);
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

export async function isDaemonRunning(): Promise<boolean> {
  try {
    const http = await import('http');
    return new Promise((resolve) => {
      const req = http.default.get(`http://127.0.0.1:${PORT}/health`, { timeout: 500 }, (res) => {
        let data = '';
        res.on('data', (chunk: string) => { data += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(data);
            resolve(json.status === 'ok');
          } catch {
            resolve(false);
          }
        });
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    });
  } catch {
    return false;
  }
}

export async function startDaemon(): Promise<{ success: boolean; message: string }> {
  const os = platform();
  try {
    if (os === 'darwin') {
      execSync(`launchctl start ${LABEL}`, { stdio: 'pipe' });
    } else if (os === 'win32') {
      winExec(`schtasks /run /tn "${TASK_NAME}"`);
    } else {
      return { success: false, message: `Unsupported platform: ${os}` };
    }
    return { success: true, message: 'UAB Bridge service started.' };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, message: msg };
  }
}

export async function stopDaemon(): Promise<{ success: boolean; message: string }> {
  const os = platform();
  try {
    if (os === 'darwin') {
      execSync(`launchctl stop ${LABEL}`, { stdio: 'pipe' });
    } else if (os === 'win32') {
      winExec(`schtasks /end /tn "${TASK_NAME}"`);
    } else {
      return { success: false, message: `Unsupported platform: ${os}` };
    }
    return { success: true, message: 'UAB Bridge service stopped.' };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, message: msg };
  }
}

// ─── Electron Debugging ──────────────────────────────────────

/**
 * Enable Chrome DevTools Protocol for all Electron apps.
 *
 * Sets ELECTRON_ENABLE_REMOTE_DEBUGGING=1 as a persistent user environment
 * variable. After this, any Electron app launched by the user will accept
 * CDP connections, giving UAB full DOM access instead of just the UIA shell.
 *
 * Affects: ChatGPT, VS Code, Slack, Discord, Teams, Notion, Obsidian,
 * Spotify, Figma, Postman, 1Password, Signal, and hundreds more.
 *
 * Windows: setx (writes to HKCU\Environment)
 * macOS: launchctl setenv + ~/Library/LaunchAgents plist
 */
function enableElectronDebugging(): void {
  const os = platform();
  const varName = 'ELECTRON_ENABLE_REMOTE_DEBUGGING';

  try {
    if (os === 'win32') {
      // Check if already set
      try {
        const current = winExec(`reg query "HKCU\\Environment" /v ${varName}`);
        if (current.includes('1')) {
          log.info('Electron remote debugging already enabled');
          return;
        }
      } catch { /* not set yet */ }

      winExec(`setx ${varName} 1`);
      log.info('Enabled Electron remote debugging (setx)');

    } else if (os === 'darwin') {
      // Set for current session
      try {
        execSync(`launchctl setenv ${varName} 1`, { stdio: 'pipe' });
      } catch { /* may not work on newer macOS */ }

      // Persist via a launchd plist that sets the env var at login
      const plistPath = join(homedir(), 'Library', 'LaunchAgents', 'com.lancelot.uab-electron-debug.plist');
      if (!existsSync(plistPath)) {
        const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.lancelot.uab-electron-debug</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/launchctl</string>
    <string>setenv</string>
    <string>${varName}</string>
    <string>1</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>`;
        mkdirSync(join(homedir(), 'Library', 'LaunchAgents'), { recursive: true });
        writeFileSync(plistPath, plist, 'utf-8');
        try {
          execSync(`launchctl load "${plistPath}"`, { stdio: 'pipe' });
        } catch { /* already loaded */ }
        log.info('Enabled Electron remote debugging (launchd plist)');
      }
    }
  } catch (err) {
    // Non-fatal — UIA fallback still works
    log.warn(`Could not enable Electron debugging: ${err}`);
  }
}
