// ============================================================
// Platform — cross-platform helpers
// ============================================================

import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';

export function getPlatform() {
  const p = os.platform();
  if (p === 'win32') return 'windows';
  if (p === 'darwin') return 'macos';
  return 'linux';
}

export function getDockerInstallUrl() {
  const urls = {
    windows: 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe',
    macos: 'https://desktop.docker.com/mac/main/amd64/Docker.dmg',
    linux: 'https://docs.docker.com/engine/install/',
  };
  return urls[getPlatform()];
}

export function getDockerStartInstructions() {
  const instructions = {
    windows: 'Open Docker Desktop from the Start menu and wait for it to start.',
    macos: 'Open Docker Desktop from Applications and wait for it to start.',
    linux: 'Run: sudo systemctl start docker',
  };
  return instructions[getPlatform()];
}

export function getGitInstallHint() {
  const hints = {
    windows: 'Download from https://git-scm.com/download/win',
    macos: 'Run: xcode-select --install',
    linux: 'Run: sudo apt install git   (or equivalent for your distro)',
  };
  return hints[getPlatform()];
}

export async function checkDiskSpace(dir) {
  try {
    // Node 18.15+ has fs.statfs
    const targetDir = fs.existsSync(dir) ? dir : path.dirname(path.resolve(dir));
    const stats = fs.statfsSync(targetDir);
    const availableGB = (stats.bavail * stats.bsize) / (1024 ** 3);
    return { availableGB: Math.round(availableGB * 10) / 10 };
  } catch {
    // Fallback: return unknown
    return { availableGB: -1 };
  }
}

export function getTotalRAM() {
  return Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10;
}

export function normalizePath(p) {
  return p.replace(/\\/g, '/');
}

export async function isPortAvailable(port) {
  const net = await import('node:net');
  return new Promise((resolve) => {
    const server = net.default.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close();
      resolve(true);
    });
    server.listen(port, '0.0.0.0');
  });
}

export async function checkNetworkConnectivity() {
  try {
    const res = await fetch('https://api.github.com', {
      method: 'HEAD',
      signal: AbortSignal.timeout(5000),
    });
    return res.ok || res.status === 403; // 403 = rate-limited but reachable
  } catch {
    return false;
  }
}

export function canWriteToDir(dir) {
  const fsp = fs;
  try {
    if (!fsp.existsSync(dir)) {
      // Check parent is writable
      const parent = path.dirname(path.resolve(dir));
      fsp.accessSync(parent, fs.constants.W_OK);
    } else {
      fsp.accessSync(dir, fs.constants.W_OK);
    }
    return true;
  } catch {
    return false;
  }
}

export async function getDockerSocketAccessible() {
  const platform = getPlatform();
  if (platform === 'windows' || platform === 'macos') {
    // Docker Desktop handles socket access via VM
    return { accessible: true, hint: null };
  }
  // Linux: check /var/run/docker.sock permissions
  try {
    fs.accessSync('/var/run/docker.sock', fs.constants.R_OK | fs.constants.W_OK);
    return { accessible: true, hint: null };
  } catch {
    return {
      accessible: false,
      hint: 'Run: sudo usermod -aG docker $USER && newgrp docker',
    };
  }
}

export async function getGpuVram() {
  try {
    const { execa } = await import('execa');
    const result = await execa('nvidia-smi', [
      '--query-gpu=memory.total',
      '--format=csv,noheader,nounits',
    ], { timeout: 5000 });
    const mb = parseInt(result.stdout.trim().split('\n')[0]);
    return isNaN(mb) ? null : mb;
  } catch {
    return null;
  }
}
